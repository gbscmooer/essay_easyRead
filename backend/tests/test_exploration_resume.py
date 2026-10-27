"""探索断点续传与协作式暂停（legacy v1）测试。

守住的行为契约：
1. 阶段边界检查点：每个阶段完成后 checkpoint.completed_stages 落库，
   语料/计量/综合产物随 result 列逐阶段持久化；
2. 失败 run 续传：resume 只补未完成阶段——已完成阶段直接复用落库快照，
   不重烧检索/模型请求；run 完成后检查点清空；
3. 报告阶段逐篇断点：examination 结果逐篇落 checkpoint.report.extractions，
   暂停在篇目边界生效（StagePausedError），续跑复用冻结的选材清单与已提取
   篇目，只补缺的篇目，且任何篇目不会被提取两次；
4. 僵尸 run（DB running 但本进程无活跃 worker）可被直接暂停/续传；
5. 暂停中的 run 占住「同一时刻仅一个探索任务」的名额；
6. 暂停看门狗：worker 卡死在不可中断调用、旗标无人消费时，宽限期后强制取消
   worker 并落库 paused（点「暂停」不再永久停在「暂停中…」）。

全部使用合成数据与临时库，不发起任何网络请求。
"""

import asyncio

import pytest
from sqlmodel import Session

from backend.models.exploration import ExplorationRun
from backend.services.deep_research import explorer, survey_report
from backend.services.deep_research.explorer import run_exploration
from backend.services.deep_research.stage_agent import StagePausedError


def _cand(title, *, year=2023, cites=10):
    from types import SimpleNamespace
    return SimpleNamespace(
        canonical_key=f"k:{title}", title=title, year=year, citation_count=cites,
        doi=None, arxiv_id=None, authors=None, abstract=f"{title} abstract",
        seed_relation="search_match",
    )


def _paper(title, *, year=2023, cites=10):
    return {"key": f"k:{title}", "title": title, "year": year,
            "citation_count": cites, "abstract": f"{title} abstract"}


class _StubProvider:
    name = "stub"

    def __init__(self):
        self.search_calls = 0

    async def search_works(self, query, limit=20, **kwargs):
        self.search_calls += 1
        if "tumor" in query:
            return [_cand(f"tumor detection {w}") for w in ("cnn", "transformer", "unet")]
        return []

    async def aclose(self):
        pass


@pytest.fixture()
def resume_env(tmp_path, monkeypatch):
    """隔离环境：独立 SQLite + stub provider + 无 LLM Key（统计视图路径）。"""
    from sqlmodel import SQLModel, create_engine

    engine = create_engine(f"sqlite:///{tmp_path}/resume.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(explorer, "get_engine", lambda: engine)

    provider = _StubProvider()
    monkeypatch.setattr(
        "backend.services.deep_research.providers.get_provider", lambda cfg: provider,
    )
    monkeypatch.setattr(
        "backend.services.ai_config.get_analysis_config",
        lambda: {"api_key": "", "base_url": "", "model": ""},
    )

    async def _healthy_sources(*args, **kwargs):
        return [{"name": "openalex", "role": "primary", "status": "ok",
                 "detail": "测试环境", "latency_ms": 1, "http_status": 200},
                {"name": "arxiv", "role": "supplement", "status": "ok",
                 "detail": "测试环境", "latency_ms": 1, "http_status": 200}]

    monkeypatch.setattr(
        "backend.services.deep_research.preflight.preflight_research_sources", _healthy_sources,
    )

    from backend.services.deep_research import literature_retrieval as retrieval

    async def unavailable(*args, **kwargs):
        raise RuntimeError("offline test")
    monkeypatch.setattr(retrieval, "document_vectors", unavailable)
    monkeypatch.setattr(retrieval, "get_reranker_config", lambda: {"enabled": False})

    events: list[tuple[str, dict]] = []

    class _Bus:
        async def publish(self, evt):
            events.append((evt.event, evt.data))

        async def close(self):
            pass

    monkeypatch.setattr(explorer, "get_exploration_bus", lambda rid: _Bus())
    monkeypatch.setattr(explorer, "has_exploration_bus", lambda rid: True)
    return engine, events, provider


def _make_run(engine, question="tumor detection deep learning", depth="standard"):
    with Session(engine) as s:
        run = ExplorationRun(question=question, depth=depth)
        s.add(run)
        s.commit()
        s.refresh(run)
        return run.id


# ── 1) 阶段检查点 + 失败续传跳过已完成阶段 ─────────────────────────────────────

def test_failed_run_keeps_checkpoints_and_resume_skips_completed_stages(resume_env, monkeypatch):
    engine, events, provider = resume_env
    run_id = _make_run(engine)

    # 首轮在 metrics 阶段注入失败（paper_similarity 在 enrich 之后调用），之后恢复真实实现
    real_paper_similarity = explorer.paper_similarity
    attempts = {"n": 0}

    async def flaky(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("metrics exploded")
        return await real_paper_similarity(*args, **kwargs)

    monkeypatch.setattr(explorer, "paper_similarity", flaky)
    asyncio.run(run_exploration(run_id))

    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "failed"
        assert run.current_stage == "screen"
        # preflight/protocol/recall 已检查点化（enrich+metrics 并入 screen，未完成）；
        # 语料随 result 落库
        assert set(run.checkpoint["completed_stages"]) == {"preflight", "protocol", "recall"}
        assert len(run.result["papers"]) == 3
        assert run.result["provider"] == "stub"
    searches_before = provider.search_calls
    assert searches_before > 0

    # 续传：不再重烧检索（stub 搜索计数不增长），直接从 metrics 继续
    asyncio.run(run_exploration(run_id, resume=True))

    assert provider.search_calls == searches_before
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "done"
        assert run.checkpoint is None  # 完成后检查点清空
        assert run.result["metrics"]["total"] == 3
    names = [e for e, _ in events]
    assert "paused" not in names and "error" in names  # 首轮失败仍回放 error 事件


def test_resume_from_scratch_equals_fresh_run(resume_env):
    """没有任何检查点的 failed run（如旧版本数据）也能 resume：等价重跑。"""
    engine, events, provider = resume_env
    run_id = _make_run(engine)
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        run.status = "failed"
        run.error_message = "历史遗留失败"
        s.add(run)
        s.commit()
    asyncio.run(run_exploration(run_id, resume=True))
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "done"
        assert run.checkpoint is None


# ── 2) 报告阶段逐篇断点 + 篇目间暂停 + 续传复用 ────────────────────────────────

@pytest.fixture()
def agent_env(tmp_path, monkeypatch):
    """有 LLM Key 的隔离环境：检索/富化/综合全部 stub，只有报告逐篇提取可观测。"""
    from sqlmodel import SQLModel, create_engine

    engine = create_engine(f"sqlite:///{tmp_path}/agent.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(explorer, "get_engine", lambda: engine)
    monkeypatch.setattr(
        "backend.services.ai_config.get_analysis_config",
        lambda: {"api_key": "test-key", "base_url": "https://gateway.example/v1", "model": "test-model"},
    )

    async def _healthy_sources(*args, **kwargs):
        return [{"name": "openalex", "role": "primary", "status": "ok",
                 "detail": "测试环境", "latency_ms": 1, "http_status": 200},
                {"name": "arxiv", "role": "supplement", "status": "ok",
                 "detail": "测试环境", "latency_ms": 1, "http_status": 200}]

    monkeypatch.setattr(
        "backend.services.deep_research.preflight.preflight_research_sources", _healthy_sources,
    )

    corpus = [_paper(f"paper {i}", year=2020 + i, cites=50 - i) for i in range(5)]

    async def fake_recall(question, queries, **kwargs):
        return [dict(p) for p in corpus], {"engine": "target_recall_v1", "rounds": [], "effective_rounds": 1}

    async def fake_enrich(papers, **kwargs):
        return {"history_matched": 0, "venue_metrics": 0}

    async def fake_refine(question, decompose, llm_cfg, **kwargs):
        return {"intent": "landscape", "en_queries": ["tumor detection"], "focus_terms": [],
                "fallback": False, "core_query": "tumor detection"}, {"status": "completed", "iterations": 1}

    async def fake_synth(*args, **kwargs):
        return None

    # LLM 网关快速失败（不触网、不等待超时）：decompose_question 走回退拆解
    from backend.services.ai_analyzer import LLMResponseError

    async def fast_fail_llm(*args, **kwargs):
        raise LLMResponseError("LLM_EMPTY_CONTENT", base_url="https://gateway.example/v1",
                               model="test-model")

    monkeypatch.setattr(explorer, "run_target_recall", fake_recall)
    monkeypatch.setattr(explorer, "enrich_papers", fake_enrich)
    monkeypatch.setattr(explorer, "refine_decomposition_with_tools", fake_refine)
    monkeypatch.setattr(explorer, "synthesize_exploration", fake_synth)
    monkeypatch.setattr("backend.services.deep_research.domain_synthesizer._llm_json", fast_fail_llm)

    events: list[tuple[str, dict]] = []

    class _Bus:
        async def publish(self, evt):
            events.append((evt.event, evt.data))

        async def close(self):
            pass

    monkeypatch.setattr(explorer, "get_exploration_bus", lambda rid: _Bus())
    monkeypatch.setattr(explorer, "has_exploration_bus", lambda rid: True)
    return engine, events, corpus


def test_report_extraction_pause_then_resume_reuses_cached_extractions(agent_env, monkeypatch):
    engine, events, corpus = agent_env
    run_id = _make_run(engine)

    extracted: list[str] = []

    async def extracting_stub(question, paper, excerpt, kind, cfg, depth, iteration_progress):
        extracted.append(paper["key"])
        # 第 1 篇提取期间用户请求暂停 → 后续排队篇目在安全点让出
        explorer._pause_requests.add(run_id)
        return {"metrics": []}, 0, {"status": "completed", "iterations": 1}

    monkeypatch.setattr(survey_report, "extract_paper_with_agent", extracting_stub)

    asyncio.run(run_exploration(run_id))

    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "paused"
        assert run.current_stage == "extract"
        # 3 篇在暂停前已开工并完成（并发槽 3），检查点逐篇落库；其余在篇目边界让出
        checkpoint = run.checkpoint
        cached = checkpoint["report"]["extractions"]
        assert set(cached) == set(extracted)
        assert all(entry["status"] == "examined" for entry in cached.values())
        assert set(checkpoint["completed_stages"]) >= {"preflight", "protocol", "recall", "screen", "select"}
    assert "paused" in [e for e, _ in events]
    first_session_keys = list(extracted)

    # 续传：清掉暂停旗标，只补缺的篇目，任何篇目不被提取两次
    explorer._pause_requests.discard(run_id)

    async def completing_stub(question, paper, excerpt, kind, cfg, depth, iteration_progress):
        extracted.append(paper["key"])
        return {"metrics": []}, 0, {"status": "completed", "iterations": 1}

    monkeypatch.setattr(survey_report, "extract_paper_with_agent", completing_stub)
    asyncio.run(run_exploration(run_id, resume=True))

    assert len(extracted) == len(corpus)  # 5 篇各提取一次
    assert len(set(extracted)) == len(extracted)
    assert len(first_session_keys) < len(extracted)  # 续传确实补了新篇目
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "done"
        assert run.checkpoint is None  # 完成后检查点清空
        report = run.result["survey_report"]
        assert report["coverage"]["examined"] == len(corpus)
        assert report["coverage"]["selected"] == len(corpus)
    names = [e for e, _ in events]
    assert names[-1] == "done"


# ── 2b) v8 阶段边界产物完整性（run 审查 P0-1/P0-2 回归）────────────────────────

def _tasks_rows(paper):
    return {"tasks": [{"task": f"t-{paper['key']}", "subtask": "", "dataset": "",
                       "method": "", "evidence": []}]}


def test_pause_after_extract_resume_keeps_stage_artifacts(agent_env, monkeypatch):
    """extract 完成后暂停：report_plan/report_extract 随 result 落库，续跑不得
    重建 result 丢产物、不得重新提取。"""
    engine, events, corpus = agent_env
    run_id = _make_run(engine)

    async def completing_extract(question, paper, excerpt, kind, cfg, depth, iteration_progress):
        return _tasks_rows(paper), 0, {"status": "completed", "iterations": 1}

    monkeypatch.setattr(survey_report, "extract_paper_with_agent", completing_extract)

    async def synth_sets_pause(*args, **kwargs):
        explorer._pause_requests.add(run_id)  # 综合入口置位 → synthesize 完成后在边界暂停
        return None

    monkeypatch.setattr(explorer, "synthesize_exploration", synth_sets_pause)

    asyncio.run(run_exploration(run_id))
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "paused"
        assert run.current_stage == "synthesize"
        assert "extract" in run.checkpoint["completed_stages"]
        # 阶段产物随 result 落库（P0-2）：不是只存在于编排内存
        assert run.result["report_plan"]["selected_keys"]
        assert run.result["report_extract"]["coverage"]["examined"] == len(corpus)

    explorer._pause_requests.discard(run_id)

    async def must_not_extract(*args, **kwargs):
        raise AssertionError("extract 已完成的续跑不得重新提取")

    monkeypatch.setattr(survey_report, "extract_paper_with_agent", must_not_extract)
    asyncio.run(run_exploration(run_id, resume=True))

    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "done"
        # screen 恢复路径不得整体重建 result（P0-1）：冻结的选材与提取产物原样保留
        assert run.result["report_plan"]["selected_keys"]
        assert run.result["report_extract"]["coverage"]["examined"] == len(corpus)
        assert not run.result.get("report_error")
        # 恢复分支同样闭合时间线：不残留 duration_s=None/status=running 的行（复审 P2）
        timeline = (run.result.get("sources") or {}).get("stage_timeline") or []
        assert timeline and all(row.get("duration_s") is not None for row in timeline)
    assert [e for e, _ in events][-1] == "done"


def test_pause_after_draft_resume_reuses_frozen_draft(agent_env, monkeypatch):
    """synthesize 冻结草稿后暂停：续跑 report 阶段直接保存，不得重新写作。"""
    import backend.services.deep_research.report_delivery as report_delivery

    engine, events, corpus = agent_env
    run_id = _make_run(engine)

    async def extracting_with_evidence(question, paper, excerpt, kind, cfg, depth,
                                       iteration_progress):
        return ({"tasks": [{"task": "detection", "subtask": "", "dataset": "MVTec",
                            "method": "cnn",
                            "evidence": [{"paper_key": paper["key"],
                                          "quote": paper["abstract"],
                                          "source_kind": "abstract"}]}]},
                0, {"status": "completed", "iterations": 1})

    monkeypatch.setattr(survey_report, "extract_paper_with_agent", extracting_with_evidence)

    async def draft_sets_pause(*args, **kwargs):
        explorer._pause_requests.add(run_id)
        return {"ok": True, "state": {"marker": "frozen-draft"}}

    monkeypatch.setattr(report_delivery, "draft_report", draft_sets_pause)

    asyncio.run(run_exploration(run_id))
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "paused"
        # 冻结草稿随 result 落库
        assert run.result["draft"]["marker"] == "frozen-draft"

    explorer._pause_requests.discard(run_id)

    async def must_not_redraft(*args, **kwargs):
        raise AssertionError("synthesize 已完成的续跑不得重新写作")

    monkeypatch.setattr(report_delivery, "draft_report", must_not_redraft)

    captured: dict = {}

    def fake_finalize(state, **kwargs):
        captured["state"] = state
        return {
            "projections": {"summary": "s", "findings": [], "limitations": [],
                            "gaps": [], "novelty": ""},
            "delivery": {"narrative_source": "final_draft", "delivery_status": "ready"},
            "narrative": {"sections": {}},
            "quality_status": "unchecked",
            "agent_loop": {"status": "completed"},
            "audit": {"status": "not_requested"},
        }

    monkeypatch.setattr(report_delivery, "finalize_generated_report", fake_finalize)

    asyncio.run(run_exploration(run_id, resume=True))
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "done"
        # report 阶段拿到的是恢复出的冻结草稿（P0-1：screen 不得清掉 draft）
        assert captured["state"]["marker"] == "frozen-draft"
        # 草稿中间态不随终稿落库
        assert "draft" not in (run.result or {})
    assert [e for e, _ in events][-1] == "done"


def test_pause_after_select_resume_keeps_downloaded_full_text(agent_env, monkeypatch, tmp_path):
    """select 下载完成后暂停：全文快照随 result 落库，续跑不得重复下载。"""
    engine, events, corpus = agent_env
    run_id = _make_run(engine)

    with_pdf = [dict(p, pdf_url=f"https://example.com/{p['key']}.pdf") for p in corpus]

    async def fake_recall_pdf(question, queries, **kwargs):
        return [dict(p) for p in with_pdf], {
            "engine": "target_recall_v1", "rounds": [], "effective_rounds": 1}

    monkeypatch.setattr(explorer, "run_target_recall", fake_recall_pdf)

    downloads: list[str] = []
    pdf_path = tmp_path / "fake.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    async def fake_download(url, timeout=15, **kwargs):
        downloads.append(url)
        explorer._pause_requests.add(run_id)  # 下载完成后置位 → select 完成后在边界暂停
        return type("Result", (), {"status": "available", "path": str(pdf_path)})()

    class _FakeContent:
        full_text = "downloaded full text for the paper. " * 10

    monkeypatch.setattr(
        "backend.services.deep_research.pdf_downloader.download_pdf_detailed", fake_download)
    monkeypatch.setattr(
        "backend.services.pdf_parser.extract_pdf_content", lambda path: _FakeContent())

    asyncio.run(run_exploration(run_id))
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "paused"
        assert run.current_stage == "select"
        assert "select" in run.checkpoint["completed_stages"]
        assert downloads  # 首轮确实走过下载
        # 全文快照随 result 落库（P0-2）：papers 恢复时自带 source_text
        persisted = {p["key"]: p for p in run.result["papers"]}
        assert sum(1 for p in persisted.values() if p.get("source_text")) == len(corpus)
        assert run.result["report_plan"]["selected_keys"]

    explorer._pause_requests.discard(run_id)
    downloads.clear()

    async def fail_download(url, *args, **kwargs):
        raise AssertionError("select 已完成的续跑不得重复下载全文")

    monkeypatch.setattr(
        "backend.services.deep_research.pdf_downloader.download_pdf_detailed", fail_download)

    extracted_kinds: list[str] = []

    async def extracting(question, paper, excerpt, kind, cfg, depth, iteration_progress):
        extracted_kinds.append(kind)
        return _tasks_rows(paper), 0, {"status": "completed", "iterations": 1}

    monkeypatch.setattr(survey_report, "extract_paper_with_agent", extracting)

    asyncio.run(run_exploration(run_id, resume=True))
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "done"
    # 提取全部基于恢复出的全文（未重新下载）
    assert extracted_kinds and all(k == "full_text_excerpt" for k in extracted_kinds)


# ── 3) build_survey_report 断点缝（单元级）────────────────────────────────────

def test_research_time_budget_freeze_does_not_stop_independent_extraction(agent_env, monkeypatch):
    """检索预算耗尽后，冻结选材仍进入独立 extract 域并补齐缺失证据。"""
    engine, events, corpus = agent_env
    run_id = _make_run(engine, depth="lite")

    recall_calls = {"n": 0}
    real_recall = explorer.run_target_recall

    async def counting_recall(question, queries, **kwargs):
        recall_calls["n"] += 1
        return await real_recall(question, queries, **kwargs)

    monkeypatch.setattr(explorer, "run_target_recall", counting_recall)

    extracted: list[str] = []
    phase_contexts: list[tuple[object, object]] = []

    async def extracting(question, paper, excerpt, kind, cfg, depth, iteration_progress, **kwargs):
        from backend.services.llm import llm_budget, llm_deadline
        extracted.append(paper["key"])
        phase_contexts.append((llm_budget(), llm_deadline()))
        return _tasks_rows(paper), 0, {"status": "completed", "iterations": 1}

    monkeypatch.setattr(survey_report, "extract_paper_with_agent", extracting)

    # 复现复审探针场景：研究线 = 900−240 = 660s；active_seconds=661 → 研究侧
    # 耗尽但交付侧仍剩 ~239s。冻结选材 5 篇、已缓存提取 2 篇。
    def _cached_entry(p):
        return {"key": p["key"], "status": "examined", "source_kind": "abstract",
                "rejected_rows": 0,
                "rows": {"tasks": [{"task": f"t-{p['key']}", "subtask": "", "dataset": "",
                                    "method": "", "evidence": []}]}}

    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        run.status = "paused"
        run.current_stage = "screen"
        run.decompose = {"intent": "landscape", "en_queries": ["tumor detection"],
                         "focus_terms": [], "fallback": False, "core_query": "tumor detection"}
        run.result = {
            "decompose": run.decompose, "provider": "stub",
            "papers": [dict(p) for p in corpus], "metrics": {}, "graph": {},
            "sources": {
                "preflight": [{"name": "openalex", "role": "primary", "status": "ok",
                               "detail": "t", "latency_ms": 1, "http_status": 200},
                              {"name": "arxiv", "role": "supplement", "status": "ok",
                               "detail": "t", "latency_ms": 1, "http_status": 200}],
                "warnings": [], "stage_timeline": [], "agent_loops": {},
            },
        }
        run.checkpoint = {
            "checkpoint_schema_version": 2,
            "workflow_revision": explorer.WORKFLOW_REVISION,
            "completed_stages": ["preflight", "protocol", "recall"],
            "budget": {"active_seconds": 661.0,
                       "resources": {"model_attempts": {"limit": 40, "used": 1.0,
                                                        "reserved": 0.0}}},
            "report": {"selected_keys": [p["key"] for p in corpus],
                       "extractions": {p["key"]: _cached_entry(p) for p in corpus[:2]}},
            "updated_at": "2026-09-19T00:00:00",
        }
        s.add(run)
        s.commit()

    asyncio.run(run_exploration(run_id, resume=True))

    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "done"
        sources = run.result.get("sources") or {}
        loops = sources.get("agent_loops") or {}
        assert loops.get("select", {}).get("stop_reason") == "research_time_budget_exhausted"
        assert "extract" not in loops or loops.get("extract", {}).get("stop_reason") \
            != "research_time_budget_exhausted"
        # 计量/聚类（纯算法）不受冻结影响
        assert (run.result.get("metrics") or {}).get("total") == len(corpus)
        # 冻结事件入预算审计与来源警告
        exhaustion = [row.get("resource") for row in
                      (run.result.get("budget") or {}).get("exhaustion_log") or []]
        assert "research_time" in exhaustion
        assert any("研究侧时间预算已耗尽" in str(w) for w in sources.get("warnings") or [])
        assert sources["execution_policy"]["unbounded_stages"] == [
            "extract", "synthesize", "report"]
        # 两篇缓存直接复用，其余三篇在独立 extract 域补齐。
        report = run.result.get("survey_report") or {}
        assert report["coverage"]["examined"] == len(corpus)
        assert report["coverage"].get("skipped_frozen", 0) == 0
    assert set(extracted) == {p["key"] for p in corpus[2:]}
    assert phase_contexts and all(ctx == (None, None) for ctx in phase_contexts)
    assert recall_calls["n"] == 0  # 已完成的召回不因冻结重跑
    assert [e for e, _ in events][-1] == "done"


def test_time_cap_writing_line_skips_unstarted_extraction_and_still_delivers(agent_env, monkeypatch):
    """运行时长硬上限（非 enforce 档同样生效）：续跑前已累计时长越过写作预留线，
    未开工的篇目不再提取（skipped_frozen），已缓存提取照常复用，run 正常交付。"""
    engine, events, corpus = agent_env
    run_id = _make_run(engine, depth="quick")

    extracted: list[str] = []

    async def extracting(question, paper, excerpt, kind, cfg, depth, iteration_progress, **kwargs):
        extracted.append(paper["key"])
        return _tasks_rows(paper), 0, {"status": "completed", "iterations": 1}

    monkeypatch.setattr(survey_report, "extract_paper_with_agent", extracting)

    def _cached_entry(p):
        return {"key": p["key"], "status": "examined", "source_kind": "abstract",
                "rejected_rows": 0,
                "rows": {"tasks": [{"task": f"t-{p['key']}", "subtask": "", "dataset": "",
                                    "method": "", "evidence": []}]}}

    # quick 写作线 = 1200 − 240 = 960s；此前各阶段已累计 1000s（暂停不计，续跑沿用）
    timeline = [{"stage": "recall", "started_at": "2026-10-25T00:00:00+00:00",
                 "duration_s": 1000.0, "status": "completed"}]
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        run.status = "paused"
        run.current_stage = "screen"
        run.decompose = {"intent": "landscape", "en_queries": ["tumor detection"],
                         "focus_terms": [], "fallback": False, "core_query": "tumor detection"}
        run.result = {
            "decompose": run.decompose, "provider": "stub",
            "papers": [dict(p) for p in corpus], "metrics": {}, "graph": {},
            "sources": {
                "preflight": [{"name": "openalex", "role": "primary", "status": "ok",
                               "detail": "t", "latency_ms": 1, "http_status": 200}],
                "warnings": [], "stage_timeline": timeline, "agent_loops": {},
            },
        }
        run.checkpoint = {
            "checkpoint_schema_version": 2,
            "workflow_revision": explorer.WORKFLOW_REVISION,
            "completed_stages": ["preflight", "protocol", "recall"],
            "report": {"selected_keys": [p["key"] for p in corpus],
                       "extractions": {p["key"]: _cached_entry(p) for p in corpus[:2]}},
            "updated_at": "2026-10-25T00:00:00",
        }
        s.add(run)
        s.commit()

    asyncio.run(run_exploration(run_id, resume=True))

    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "done"
        sources = run.result.get("sources") or {}
        assert extracted == []
        report = run.result.get("survey_report") or {}
        assert report["coverage"]["examined"] == 2
        assert report["coverage"]["skipped_frozen"] == len(corpus) - 2
        exhaustion = [row.get("resource") for row in
                      (run.result.get("budget") or {}).get("exhaustion_log") or []]
        assert "time_cap_writing" in exhaustion and "research_time" not in exhaustion
        assert any("写作预留线" in str(w) for w in sources.get("warnings") or [])
        cap = sources["execution_policy"]["time_cap"]
        assert cap["seconds"] == 1200 and cap["elapsed_s"] >= 1000
    assert [e for e, _ in events][-1] == "done"


def test_read_papers_budget_does_not_cap_independent_extraction(agent_env, monkeypatch):
    """历史 read_papers 余额为零也不缩小独立 extract 域的档位选材。"""
    engine, events, corpus = agent_env
    run_id = _make_run(engine, depth="lite")

    extracted: list[str] = []

    async def extracting(question, paper, excerpt, kind, cfg, depth, iteration_progress, **kwargs):
        extracted.append(paper["key"])
        return _tasks_rows(paper), 0, {"status": "completed", "iterations": 1}

    monkeypatch.setattr(survey_report, "extract_paper_with_agent", extracting)

    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        run.status = "paused"
        run.current_stage = "screen"
        run.decompose = {"intent": "landscape", "en_queries": ["tumor detection"],
                         "focus_terms": [], "fallback": False, "core_query": "tumor detection"}
        run.result = {
            "decompose": run.decompose, "provider": "stub",
            "papers": [dict(p) for p in corpus], "metrics": {}, "graph": {},
            "sources": {
                "preflight": [{"name": "openalex", "role": "primary", "status": "ok",
                               "detail": "t", "latency_ms": 1, "http_status": 200},
                              {"name": "arxiv", "role": "supplement", "status": "ok",
                               "detail": "t", "latency_ms": 1, "http_status": 200}],
                "warnings": [], "stage_timeline": [], "agent_loops": {},
            },
        }
        run.checkpoint = {
            "checkpoint_schema_version": 2,
            "workflow_revision": explorer.WORKFLOW_REVISION,
            "completed_stages": ["preflight", "protocol", "recall"],
            # read_papers 8/8 用满 → remaining=0；活动时间很小（研究侧未冻结）
            "budget": {"active_seconds": 10.0,
                       "resources": {"model_attempts": {"limit": 40, "used": 1.0, "reserved": 0.0},
                                      "read_papers": {"limit": 8, "used": 8.0, "reserved": 0.0}}},
            "report": {},
            "updated_at": "2026-09-19T00:00:00",
        }
        s.add(run)
        s.commit()

    asyncio.run(run_exploration(run_id, resume=True))

    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "done"
        report = run.result.get("survey_report") or {}
        assert report["coverage"]["selected"] == len(corpus)
        assert report["coverage"]["examined"] == len(corpus)
        assert (run.result.get("sources") or {})["execution_policy"]["unbounded_stages"] \
            == ["extract", "synthesize", "report"]
    assert set(extracted) == {p["key"] for p in corpus}


def test_research_time_frozen_without_cache_produces_limited_report(agent_env, monkeypatch):
    """冻结且没有任何已缓存提取/冻结选材：report 不做研究侧选材与提取，产出
    带确定性说明的受限报告（检索与计量结果保留）。"""
    engine, events, corpus = agent_env
    run_id = _make_run(engine, depth="lite")

    async def must_not_extract(*args, **kwargs):
        raise AssertionError("研究侧时间已耗尽：不得执行证据提取")

    monkeypatch.setattr(survey_report, "extract_paper_with_agent", must_not_extract)

    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        run.status = "paused"
        run.current_stage = "screen"
        run.decompose = {"intent": "landscape", "en_queries": ["tumor detection"],
                         "focus_terms": [], "fallback": False, "core_query": "tumor detection"}
        run.result = {
            "decompose": run.decompose, "provider": "stub",
            "papers": [dict(p) for p in corpus], "metrics": {}, "graph": {},
            "sources": {
                "preflight": [{"name": "openalex", "role": "primary", "status": "ok",
                               "detail": "t", "latency_ms": 1, "http_status": 200},
                              {"name": "arxiv", "role": "supplement", "status": "ok",
                               "detail": "t", "latency_ms": 1, "http_status": 200}],
                "warnings": [], "stage_timeline": [], "agent_loops": {},
            },
        }
        run.checkpoint = {
            "checkpoint_schema_version": 2,
            "workflow_revision": explorer.WORKFLOW_REVISION,
            "completed_stages": ["preflight", "protocol", "recall"],
            "budget": {"active_seconds": 661.0,
                       "resources": {"model_attempts": {"limit": 40, "used": 1.0,
                                                        "reserved": 0.0}}},
            "report": {},
            "updated_at": "2026-09-19T00:00:00",
        }
        s.add(run)
        s.commit()

    asyncio.run(run_exploration(run_id, resume=True))

    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "done"
        report = run.result.get("survey_report") or {}
        assert report["coverage"]["selected"] == 0
        assert report["coverage"]["examined"] == 0
        assert (report.get("method") or {}).get("selection", {}).get("coverage_policy") \
            == "research_time_frozen"
        assert any("研究侧时间预算已耗尽" in str(w) for w in report.get("warnings") or [])


def test_arxiv_supplement_probe_failure_updates_dead_set(resume_env, monkeypatch):
    """v1 迁移恢复补测 arXiv 判死：熔断集合重算并传给召回——召回不得再尝试
    已知判死的补充源（复审 P1：只补测不更新 preflight_dead 的缺口）。"""
    engine, events, _provider = resume_env
    run_id = _make_run(engine)

    async def failed_probe():
        return {"name": "arxiv", "role": "supplement", "status": "network_error",
                "detail": "探测失败", "latency_ms": None, "http_status": None}

    monkeypatch.setattr(
        "backend.services.deep_research.preflight._probe_arxiv", failed_probe)

    captured: dict = {}

    async def fake_recall(queries, **kwargs):
        captured["preflight_dead"] = set(kwargs.get("preflight_dead") or set())
        return [], "stub"

    monkeypatch.setattr(explorer, "recall_papers", fake_recall)

    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        run.status = "paused"
        run.current_stage = "recall"
        run.decompose = {"intent": "landscape", "en_queries": ["tumor detection"],
                         "focus_terms": [], "fallback": False, "core_query": "tumor detection"}
        # v1 式预检快照：只有 Scopus/OpenAlex，无 arXiv 条目
        run.result = {"sources": {
            "preflight": [{"name": "openalex", "role": "primary", "status": "ok",
                           "detail": "t", "latency_ms": 1, "http_status": 200}],
            "warnings": [], "stage_timeline": [], "agent_loops": {},
        }}
        run.checkpoint = {"completed_stages": ["preflight"],
                          "updated_at": "2026-09-19T00:00:00"}
        s.add(run)
        s.commit()

    asyncio.run(run_exploration(run_id, resume=True))

    assert "arxiv" in captured.get("preflight_dead", set())
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        names = {h.get("name") for h in (run.result.get("sources") or {}).get("preflight") or []}
        assert "arxiv" in names  # 失败结论也随 sources 落库（下次恢复不再补测）

def test_build_survey_report_resume_state_reuses_selection_and_extractions(monkeypatch):
    papers = [_paper(f"p{i}") for i in range(3)]
    cfg = {"api_key": "k", "model": "m"}

    async def fake_retrieve(eligible, question, seeds=None, *, limit=32):
        return [dict(p) for p in eligible], {}

    monkeypatch.setattr(survey_report, "retrieve_materials", fake_retrieve)

    calls: list[str] = []

    async def stub_extract(question, paper, excerpt, kind, cfg, depth, iteration_progress):
        calls.append(paper["key"])
        rows = {"tasks": [{"task": f"t-{paper['key']}", "subtask": "", "dataset": "",
                                       "method": "", "evidence": []}]}
        return rows, 0, {"status": "completed"}

    monkeypatch.setattr(survey_report, "extract_paper_with_agent", stub_extract)

    # 第一次：一直请求暂停 → 所有篇目让出 → StagePausedError，但选材清单已冻结
    selections: list[list[str]] = []

    async def on_selected(keys, backfill_keys=None):
        selections.append(list(keys))

    async def paused():
        return True

    with pytest.raises(StagePausedError):
        asyncio.run(survey_report.build_survey_report(
            "question", papers, {}, None, cfg,
            resume_state=None, on_selected=on_selected, should_pause=paused,
        ))
    assert selections and len(selections[0]) == 3

    # 第二次：带上冻结选材与 2 篇已提取缓存 → 只补第 3 篇
    cached = {
        selections[0][0]: {"key": selections[0][0], "status": "examined", "source_kind": "abstract",
                           "rejected_rows": 0, "rows": {"tasks": [{"task": "cached-1", "subtask": "", "dataset": "", "method": "", "evidence": []}]}},
        selections[0][1]: {"key": selections[0][1], "status": "examined", "source_kind": "abstract",
                           "rejected_rows": 0, "rows": {"tasks": [{"task": "cached-2", "subtask": "", "dataset": "", "method": "", "evidence": []}]}},
    }

    async def not_paused():
        return False

    report = asyncio.run(survey_report.build_survey_report(
        "question", papers, {}, None, cfg,
        resume_state={"selected_keys": selections[0], "extractions": cached},
        should_pause=not_paused,
    ))
    assert calls == [selections[0][2]]  # 只提取缺失的一篇
    assert report["coverage"]["examined"] == 3
    task_rows = report["tables"]["tasks"]
    assert {row["task"] for row in task_rows} == {"cached-1", "cached-2", f"t-{selections[0][2]}"}
    assert report["status"] == "completed"


# ── 4) 路由层：僵尸 run 的暂停/续传 ────────────────────────────────────────────

def test_zombie_run_pause_and_resume_via_api(client, engine, monkeypatch):
    """DB 状态 running 但本进程无活跃 worker（服务重启遗留）：pause 直接落库，resume 重启 worker。"""
    run_id = _make_run(engine, question="zombie run")
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        run.status = "running"
        run.checkpoint = {"completed_stages": ["preflight", "decompose", "recall"], "updated_at": "now"}
        s.add(run)
        s.commit()

    response = client.post(f"/api/exploration/runs/{run_id}/pause", json={})
    assert response.status_code == 200
    assert response.json()["status"] == "paused"
    with Session(engine) as s:
        assert s.get(ExplorationRun, run_id).status == "paused"

    # 再 pause 一次 → 幂等重放
    response = client.post(f"/api/exploration/runs/{run_id}/pause", json={})
    assert response.json()["idempotent_replay"] is True

    # resume 重启 worker（start_worker 捕获协程避免真实执行）
    started: list = []
    monkeypatch.setattr(
        "backend.routers.exploration.start_worker",
        lambda run_id, coro: (started.append(coro), coro.close()),
    )
    response = client.post(f"/api/exploration/runs/{run_id}/resume", json={})
    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert len(started) == 1

    # GET 暴露检查点摘要（供前端展示可续传进度）
    detail = client.get(f"/api/exploration/runs/{run_id}").json()
    assert detail["checkpoint_summary"]["completed_stages"] == ["preflight", "decompose", "recall"]


def test_pause_watchdog_force_cancels_unresponsive_worker(engine, monkeypatch):
    """worker 卡死在不可中断调用、安全点到不了：看门狗宽限期后强制取消并落库 paused。

    历史案例：429 Retry-After 巨值让 worker 在 asyncio.sleep 里挂死数小时，
    协作式暂停旗标永远无人观察，用户点「暂停」后停在「暂停中…」。
    """
    from backend.routers import exploration as exploration_router

    monkeypatch.setattr(exploration_router, "PAUSE_ESCALATION_SECONDS", 0.05)
    monkeypatch.setattr(exploration_router, "PAUSE_FINALIZE_GRACE_SECONDS", 0.05)
    run_id = _make_run(engine, question="stuck run")
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        run.status = "running"
        run.current_stage = "recall"
        s.add(run)
        s.commit()

    started = asyncio.Event()

    async def stuck_worker():
        started.set()
        await asyncio.sleep(3600)  # 模拟卡死：暂停旗标永远等不到安全点

    async def scenario():
        task = explorer.start_worker(run_id, stuck_worker())
        await started.wait()
        assert explorer.request_pause(run_id) is True
        exploration_router._schedule_pause_escalation(run_id)
        await asyncio.sleep(0.2)
        assert task.done() and task.cancelled()
        with Session(engine) as s:
            assert s.get(ExplorationRun, run_id).status == "paused"
        assert not explorer.pause_pending(run_id)

    asyncio.run(scenario())


def test_request_pause_without_live_worker_reports_not_observable():
    """无活跃 worker（进程重启后的僵尸 run）时 request_pause 返回 False，
    路由层据此直接落库置 paused，而不是等待一个不存在的安全点。"""
    assert explorer.request_pause(10 ** 9) is False
    explorer.cancel_pause_request(10 ** 9)


def test_pause_with_live_worker_sets_flag_and_schedules_escalation(client, engine, monkeypatch):
    """有活跃 worker 时 pause 只置旗标并调度看门狗：不直接改库，等安全点或强制取消。"""
    from backend.routers import exploration as exploration_router

    # request_pause 内部经 explorer.has_live_worker 判断可观察性
    monkeypatch.setattr(explorer, "has_live_worker", lambda run_id: True)
    scheduled: list[int] = []
    monkeypatch.setattr(exploration_router, "_schedule_pause_escalation",
                        lambda run_id: scheduled.append(run_id))
    run_id = _make_run(engine, question="live worker run")
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        run.status = "running"
        s.add(run)
        s.commit()

    response = client.post(f"/api/exploration/runs/{run_id}/pause", json={})
    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert scheduled == [run_id]
    assert explorer.pause_pending(run_id)
    explorer.cancel_pause_request(run_id)


def test_paused_run_blocks_new_exploration(client, engine):
    run_id = _make_run(engine, question="paused holder")
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        run.status = "paused"
        s.add(run)
        s.commit()
    response = client.post("/api/exploration/runs", json={"question": "another question"})
    assert response.status_code == 409
    # 全局异常处理器输出 {detail, code}
    assert "暂停" in response.json()["detail"]
    assert response.json()["code"] == "EXPLORATION_RUNNING"


def test_stream_replays_paused_state(client, engine):
    run_id = _make_run(engine, question="paused stream")
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        run.status = "paused"
        run.current_stage = "report"
        s.add(run)
        s.commit()
    with client.stream("GET", f"/api/exploration/runs/{run_id}/stream") as response:
        assert response.status_code == 200
        body = b"".join(response.iter_bytes()).decode()
    assert "event: paused" in body
    assert "report" in body


def test_stream_replays_running_run_history(client, engine):
    """刷新页面后重连进行中的 run：先回放历史事件恢复进度，而不是停在 8%。"""
    from backend.models.schemas import StreamEvent
    from backend.services.deep_research.explorer import get_exploration_bus

    run_id = _make_run(engine, question="running replay")
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        run.status = "running"
        run.current_stage = "report"
        s.add(run)
        s.commit()

    async def seed_history():
        bus = get_exploration_bus(run_id)
        await bus.publish(StreamEvent(event="stage_start", data={"stage": "report"}))
        await bus.publish(StreamEvent(event="report_progress", data={"done": 5, "total": 32}))
        await bus.close()  # 无订阅者时发布的「过去」事件留在历史里供回放

    asyncio.run(seed_history())
    try:
        with client.stream("GET", f"/api/exploration/runs/{run_id}/stream") as response:
            assert response.status_code == 200
            body = b"".join(response.iter_bytes()).decode()
        assert "event: stage_start" in body
        assert "event: report_progress" in body
        assert '"done": 5' in body
    finally:
        explorer.drop_exploration_bus(run_id)


def test_resume_preserves_stage_timeline_segments(agent_env, monkeypatch):
    """暂停→续跑（2026-09-18 用户报：阶段耗时全丢/全算到当前阶段）：
    各阶段耗时不得被续跑 worker 的空时间线覆盖清零；同一阶段（report）
    保留暂停前 paused 段与续跑后新段，前端按阶段求和。"""
    engine, events, _corpus = agent_env
    run_id = _make_run(engine)

    async def pausing_stub(question, paper, excerpt, kind, cfg, depth, iteration_progress):
        explorer._pause_requests.add(run_id)
        return {"metrics": []}, 0, {"status": "completed", "iterations": 1}

    monkeypatch.setattr(survey_report, "extract_paper_with_agent", pausing_stub)
    asyncio.run(run_exploration(run_id))

    def _by_stage(timeline):
        grouped: dict[str, list[dict]] = {}
        for row in timeline:
            grouped.setdefault(row["stage"], []).append(row)
        return grouped

    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "paused"
        assert run.result["budget"]["paused"] is True
        assert run.checkpoint["budget"]["paused"] is True
        grouped = _by_stage(run.result["sources"]["stage_timeline"])
        for stage in ("preflight", "protocol", "recall", "screen", "select"):
            assert stage in grouped, f"暂停时 {stage} 时间线缺失"
            assert grouped[stage][-1]["duration_s"] is not None
        assert any(r["status"] == "paused" for r in grouped.get("extract", []))
        durations_before = {
            stage: sum(r["duration_s"] or 0 for r in rows) for stage, rows in grouped.items()
        }

    explorer._pause_requests.discard(run_id)

    async def finishing_stub(question, paper, excerpt, kind, cfg, depth, iteration_progress):
        return {"metrics": []}, 0, {"status": "completed", "iterations": 1}

    monkeypatch.setattr(survey_report, "extract_paper_with_agent", finishing_stub)
    asyncio.run(run_exploration(run_id, resume=True))

    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "done"
        grouped = _by_stage(run.result["sources"]["stage_timeline"])
        for stage, seconds in durations_before.items():
            after = sum(r["duration_s"] or 0 for r in grouped.get(stage, []))
            assert after >= seconds, f"{stage} 耗时在续跑后被覆盖缩水: {seconds} -> {after}"
        # extract 保留暂停前 paused 段 + 续跑后 completed 新段（同一阶段多次进出求和）
        extract_rows = grouped["extract"]
        assert len(extract_rows) >= 2
        assert any(r["status"] == "paused" for r in extract_rows)
        assert any(r["status"] == "completed" for r in extract_rows)


# ── 5) v1 → v2 检查点迁移（v8：按产物可信度分层复用 + 幂等）───────────────────

def _make_v1_run(engine, *, with_papers=True):
    """构造 v1 旧检查点的 run：preflight/decompose/recall/enrich/metrics/synthesize 完成。"""
    from backend.models.exploration import ExplorationRun
    with Session(engine) as s:
        run = ExplorationRun(
            question="migrate v1", depth="standard", status="paused",
            current_stage="report",
            decompose={"intent": "landscape", "en_queries": ["tumor detection"],
                       "focus_terms": [], "fallback": False, "core_query": "tumor detection"},
            options={},
        )
        papers = ([_paper("old paper", year=2024, cites=10)] if with_papers else [])
        run.result = {
            "papers": papers, "provider": "stub",
            "metrics": {"total": len(papers)}, "graph": {},
            "synthesis": {"verdict": {"status": "done_partial"}},
            "survey_report": {"title": "legacy report", "status": "completed"},
            "sources": {"stage_timeline": [], "warnings": []},
        }
        run.checkpoint = {
            "completed_stages": ["preflight", "decompose", "recall", "enrich",
                                 "metrics", "synthesize"],
            "report": {"selected_keys": ["k:old paper"],
                       "extractions": {"k:old paper": {"key": "k:old paper",
                                                       "status": "examined",
                                                       "source_kind": "abstract",
                                                       "rejected_rows": 0, "rows": {}}}},
            "budget": {"resources": {"model_attempts": {"limit": 80, "used": 30.0, "reserved": 0.0}}},
            "updated_at": "2026-09-01T00:00:00",
        }
        s.add(run)
        s.commit()
        s.refresh(run)
        return run.id


def test_v1_checkpoint_migration_layered_and_idempotent(resume_env, monkeypatch):
    """v1 → v2 迁移：召回候选池与预检结论保留，计量/综合废弃重跑，提取缓存保留；
    迁移幂等（重复恢复不重扣预算/不覆盖产物）；legacy_usage 不占 enforce 限额。"""
    engine, events, _provider = resume_env
    run_id = _make_v1_run(engine)

    # v1 预检快照没有 arXiv 探测：迁移恢复须补跑一次（幂等——补齐后不再请求）
    arxiv_probes: list[int] = []

    async def _fake_probe_arxiv():
        arxiv_probes.append(1)
        return {"name": "arxiv", "role": "supplement", "status": "ok",
                "detail": "测试环境", "latency_ms": 1, "http_status": 200}

    monkeypatch.setattr(
        "backend.services.deep_research.preflight._probe_arxiv", _fake_probe_arxiv)

    # 只跑迁移路径：protocol 起步即抛暂停，观察迁移写库
    real_ensure = explorer.ensure_coverage_protocol

    async def pause_after_migration(decompose, question, llm_cfg):
        explorer._pause_requests.add(run_id)
        return await real_ensure(decompose, question, llm_cfg)

    monkeypatch.setattr(explorer, "ensure_coverage_protocol", pause_after_migration)
    asyncio.run(run_exploration(run_id, resume=True))

    from backend.models.exploration import ExplorationRun
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        cp = run.checkpoint
        assert cp["checkpoint_schema_version"] == 2
        assert cp["migration_status"] == "migrated"
        # 分层复用：preflight/protocol/recall 保留；enrich/metrics/synthesize/report 废弃
        assert set(cp["completed_stages"]) >= {"preflight", "protocol", "recall"}
        assert "screen" not in cp["completed_stages"]
        assert any("recall" in item for item in cp["reusable_artifacts"])
        assert set(cp["invalidated_artifacts"]) >= {"metrics", "synthesize"}
        # 提取缓存保留（extract 阶段 resume 输入）
        assert "k:old paper" in (cp["report"] or {}).get("extractions", {})
        # legacy_usage 记入检查点，预算快照的 baseline 抬高
        assert cp["legacy_usage"].get("model_attempts") == 30.0
        # P1-6：v1 预检缺 arXiv → 恢复路径补跑探测并随 sources 落库
        assert len(arxiv_probes) == 1
        assert any(h.get("name") == "arxiv" for h in (run.result.get("sources") or {}).get("preflight") or [])
        # 迁移后旧报告仍在 result（screen 不整体重建；历史版本在跑完时转 revision）
        assert run.result.get("survey_report", {}).get("title") == "legacy report"

    # 幂等：同一旧检查点重复恢复不重复迁移、不丢提取缓存；本次跑到完成，
    # 验证旧报告转为历史 revision 而不是被静默覆盖
    explorer._pause_requests.discard(run_id)
    monkeypatch.setattr(explorer, "ensure_coverage_protocol", real_ensure)
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        run.status = "paused"
        s.add(run)
        s.commit()
    asyncio.run(run_exploration(run_id, resume=True))
    from sqlmodel import select as sm_select
    from backend.models.research_core import ResearchAsset, ResearchReportRevision
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        # 跑到完成：检查点清空（重复恢复不重复迁移——arXiv 探测计数是幂等证据）
        assert run.status == "done"
        assert run.checkpoint is None
        assert len(arxiv_probes) == 1
        # 旧报告作为未选中的历史 revision 保留，新报告成为选定版本
        assets = s.exec(sm_select(ResearchAsset).where(
            ResearchAsset.run_id == run_id,
            ResearchAsset.content_kind == "report")).all()
        titles = {(a.inline_payload or {}).get("title") for a in assets}
        assert "legacy report" in titles
        selected = s.exec(sm_select(ResearchReportRevision).where(
            ResearchReportRevision.run_id == run_id,
            ResearchReportRevision.is_selected == True)).all()  # noqa: E712
        assert len(selected) == 1


def test_v1_checkpoint_without_papers_marks_needs_restart(resume_env):
    """recall 标记完成但 result 缺语料：needs_restart（保留产物，不改普通 failed）。"""
    engine, events, _provider = resume_env
    run_id = _make_v1_run(engine, with_papers=False)
    asyncio.run(run_exploration(run_id, resume=True))
    from backend.models.exploration import ExplorationRun
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "needs_restart"
        assert run.checkpoint["migration_status"] == "needs_restart"
        # 旧产物保留（导出能力不受影响）
        assert run.result.get("survey_report", {}).get("title") == "legacy report"


# ── 暂停时干预（2026-10-23）：剔除文献后续跑，从筛选阶段起在缩小后的语料上重做 ──

def test_pause_adjust_excludes_paper_and_reruns_downstream(resume_env, monkeypatch):
    from backend.models.schemas import RunAdjustRequest
    from backend.routers import exploration as router

    engine, events, provider = resume_env
    run_id = _make_run(engine)
    real_paper_similarity = explorer.paper_similarity
    attempts = {"n": 0}

    async def flaky(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("stop after recall")
        return await real_paper_similarity(*args, **kwargs)

    monkeypatch.setattr(explorer, "paper_similarity", flaky)
    asyncio.run(run_exploration(run_id))
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        run.status = "paused"
        s.add(run)
        s.commit()
        drop = next(p for p in run.result["papers"] if "unet" in p["title"])

    monkeypatch.setattr(router, "get_engine", lambda: engine)
    monkeypatch.setattr(router, "has_exploration_bus", lambda rid: False)
    with pytest.raises(router.HTTPException):
        router.adjust_paused_run(run_id, RunAdjustRequest(exclude_keys=["missing"]))
    out = router.adjust_paused_run(run_id, RunAdjustRequest(exclude_keys=[drop["key"]], add_queries=["tumor segmentation"]))
    assert out["intervention"]["excluded"] == [drop["title"]]
    with Session(engine) as s:
        options = s.get(ExplorationRun, run_id).options
        assert options["pending_user_queries"] == ["tumor segmentation"]

    searches_before = provider.search_calls
    asyncio.run(run_exploration(run_id, resume=True))
    # 无模型 Key 时不补搜（补搜依赖相关性筛选），追加检索式仍记为已消费，不重复执行
    assert provider.search_calls == searches_before
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "done"
        assert [p["title"] for p in run.result["papers"]] and drop["title"] not in [p["title"] for p in run.result["papers"]]
        assert run.result["metrics"]["total"] == 2
        assert "pending_user_queries" not in run.options and run.options["user_queries"] == ["tumor segmentation"]
        messages = [item["message"] for item in run.result["integrity"]["items"]]
        assert any("剔除 1 篇文献" in m and "tumor segmentation" in m for m in messages)


def test_answer_only_mode_skips_synthesis_and_report(resume_env):
    """开放问答模式：证据提取后只写答案，不生成领域综合与综述报告，run 正常完成。"""
    engine, events, _ = resume_env
    with Session(engine) as s:
        run = ExplorationRun(question="tumor detection deep learning", depth="quick",
                             options={"answer_only": True})
        s.add(run)
        s.commit()
        s.refresh(run)
        run_id = run.id
    asyncio.run(run_exploration(run_id))
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "done"
        loops = run.result["sources"]["agent_loops"]
        assert loops["synthesize"]["stop_reason"] == loops["report"]["stop_reason"] == "answer_only"
        assert not run.result.get("survey_report") and not run.result.get("synthesis")
    done = [d for name, d in events if name == "stage_done" and d.get("stage") in ("synthesize", "report")]
    assert [d["summary"] for d in done] == [{"skipped": "answer_only"}] * 2


def test_answer_agent_mode_skips_per_paper_extraction(resume_env):
    """开放问答 + 指针式作答：逐篇结构化提取不再需要（作答时按论断检索原文句），提取阶段跳过，run 正常完成。"""
    engine, events, _ = resume_env
    with Session(engine) as s:
        run = ExplorationRun(question="tumor detection deep learning", depth="quick",
                             options={"answer_only": True, "answer_writer": "agent"})
        s.add(run)
        s.commit()
        s.refresh(run)
        run_id = run.id
    asyncio.run(run_exploration(run_id))
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "done"
        loops = run.result["sources"]["agent_loops"]
        assert loops["extract"]["stop_reason"] == "answer_agent"
        assert not run.result.get("report_extract")
    done = [d for name, d in events if name == "stage_done" and d.get("stage") == "extract"]
    assert [d["summary"] for d in done] == [{"skipped": "answer_agent"}]


def test_answer_only_defaults_to_agent_and_grounded_keeps_extraction(resume_env):
    """开放问答默认指针式作答（跳过逐篇提取）；显式指定 grounded 时照常逐篇提取。"""
    engine, events, _ = resume_env
    ids = {}
    with Session(engine) as s:
        for name, opts in (("default", {"answer_only": True}),
                           ("grounded", {"answer_only": True, "answer_writer": "grounded"})):
            run = ExplorationRun(question="tumor detection deep learning", depth="quick", options=opts)
            s.add(run)
            s.commit()
            s.refresh(run)
            ids[name] = run.id
    for run_id in ids.values():
        asyncio.run(run_exploration(run_id))
    with Session(engine) as s:
        default = s.get(ExplorationRun, ids["default"])
        grounded = s.get(ExplorationRun, ids["grounded"])
        assert default.status == grounded.status == "done"
        assert default.result["sources"]["agent_loops"]["extract"]["stop_reason"] == "answer_agent"
        assert (grounded.result["sources"]["agent_loops"].get("extract") or {}).get("stop_reason") != "answer_agent"
