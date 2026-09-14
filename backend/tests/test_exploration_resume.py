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
5. 暂停中的 run 占住「同一时刻仅一个探索任务」的名额。

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
                 "detail": "测试环境", "latency_ms": 1, "http_status": 200}]

    monkeypatch.setattr(
        "backend.services.deep_research.preflight.preflight_research_sources", _healthy_sources,
    )

    from backend.services.deep_research import literature_retrieval as retrieval

    async def unavailable(*args, **kwargs):
        raise RuntimeError("offline test")
    monkeypatch.setattr(retrieval, "document_vectors", unavailable)
    monkeypatch.setattr(retrieval, "get_reranker_config", lambda: {"enabled": False})

    async def audit(*args, **kwargs):
        return {"status": "skipped", "support_rate": None}
    monkeypatch.setattr(explorer, "audit_answer", audit)

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
        assert run.current_stage == "metrics"
        # preflight/decompose/recall/enrich 四个阶段已检查点化；语料随 result 落库
        assert set(run.checkpoint["completed_stages"]) == {"preflight", "decompose", "recall", "enrich"}
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
        assert run.current_stage == "report"
        # 3 篇在暂停前已开工并完成（并发槽 3），检查点逐篇落库；其余在篇目边界让出
        checkpoint = run.checkpoint
        cached = checkpoint["report"]["extractions"]
        assert set(cached) == set(extracted)
        assert all(entry["status"] == "examined" for entry in cached.values())
        assert set(checkpoint["completed_stages"]) >= {"preflight", "decompose", "recall", "enrich", "metrics"}
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


# ── 3) build_survey_report 断点缝（单元级）────────────────────────────────────

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

    async def on_selected(keys):
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

    # resume 重启 worker（_start 捕获协程避免真实执行）
    started: list = []
    monkeypatch.setattr(
        "backend.routers.exploration._start",
        lambda coro: (started.append(coro), coro.close()),
    )
    response = client.post(f"/api/exploration/runs/{run_id}/resume", json={})
    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert len(started) == 1

    # GET 暴露检查点摘要（供前端展示可续传进度）
    detail = client.get(f"/api/exploration/runs/{run_id}").json()
    assert detail["checkpoint_summary"]["completed_stages"] == ["preflight", "decompose", "recall"]


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
