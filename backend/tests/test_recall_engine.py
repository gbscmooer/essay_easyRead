"""目标量召回引擎（recall_engine）单元测试：全部离线，源与 LLM 均注入桩。

覆盖审查报告 F1–F6 的验收边界：
- F1 默认 arXiv 通道真实执行候选转换；源错误单独记账不吞成零命中
- F2 排除规则在任何模型输入/计数/裁剪之前生效
- F3 无摘要候选走标题级初筛；unknown 不挤占 confirmed 配额
- F4 筛后确认量不足时预算内补搜；stop_reason 分类
- F5 经典通道每轮只发射一次
- F6 planner 返回非 dict/缺字段/异常均不致命，材料保留
"""

import asyncio
import json
from types import SimpleNamespace

from backend.services.deep_research.recall_engine import (
    MAX_KEYWORD_ATTEMPTS, _coverage_status, _filter_pool,
    dual_track_trim, relevance_trim, run_target_recall,
)


def _cand(title, *, year=2025, cites=0, doi=None, abstract=None, key=None):
    return SimpleNamespace(
        title=title, canonical_key=key or f"t:{title.lower()}", year=year,
        citation_count=cites, doi=doi, arxiv_id=None, url=None, pdf_url=None,
        authors=None, abstract=abstract, seed_relation=None, extra=None,
    )


def _accept_all(user):
    return {"accepted": [item["key"] for item in json.loads(user)["papers"]],
            "rejected": [], "uncertain": []}


# ── 双轨配额裁剪 ─────────────────────────────────────────────────────────────

def test_dual_track_trim_keeps_classic_and_recent():
    papers = [
        {"key": "old1", "title": "foundational", "year": 1995, "citation_count": 3000},  # ≈94/年
        {"key": "old2", "title": "classic", "year": 2000, "citation_count": 1200},       # ≈55/年
        {"key": "new1", "title": "rising", "year": 2026, "citation_count": 150},         # 150/年
        {"key": "new2", "title": "fresh", "year": 2025, "citation_count": 40},           # 20/年
    ]
    kept = dual_track_trim(papers, 4, now_year=2026)
    assert {p["key"] for p in kept} == {"old1", "old2", "new1", "new2"}

    kept2 = dual_track_trim(papers, 2, now_year=2026)
    assert {p["key"] for p in kept2} == {"old1", "new1"}  # 经典轨半 + 近年轨半


def test_dual_track_trim_empty_and_small():
    assert dual_track_trim([], 10, now_year=2026) == []
    assert dual_track_trim([{"key": "a", "year": 2024, "citation_count": 1}], 10, 2026)


def test_topical_candidate_survives_high_citation_pretrim():
    papers = [{"key": f"off-{i}", "title": "Mouse genome sequencing",
               "abstract": "genetics", "citation_count": 1000 + i, "year": 2020}
              for i in range(20)]
    papers.append({"key": "key-paper", "title": "Defect detection with visual anomalies",
                   "abstract": "industrial anomaly detection", "citation_count": 0,
                   "year": 2026})
    chosen = relevance_trim(papers, 5, "defect detection", ["visual anomaly detection"], 2026)
    assert "key-paper" in {paper["key"] for paper in chosen}


def test_coverage_requires_probes_in_same_paper():
    dims = [{"dimension": "benchmark", "probes": [
        "benchmark", "dataset", "mvtec", "testbed", "corpus", "split"]}]
    papers = [{"title": "benchmark", "abstract": ""},
              {"title": "dataset", "abstract": ""}]
    assert _coverage_status(papers, dims)[0]["covered"] is False


def test_filter_requires_explicit_acceptance():
    papers = [{"key": "good", "title": "Vision Mamba", "abstract": "visual state space"},
              {"key": "uncertain", "title": "Other", "abstract": "unclear"}]

    async def llm_json(system, user):
        return {"accepted": ["good"], "rejected": [], "uncertain": ["uncertain"]}

    kept, audit = asyncio.run(_filter_pool(
        papers, "vision mamba", batch_size=10, llm_json=llm_json, verdict_cache={}))
    assert {paper["key"]: paper["relevance_status"] for paper in kept} == {
        "good": "confirmed", "uncertain": "unknown"}
    assert audit["confirmed"] == 1 and audit["unknown"] == 1


# ── F1：默认 arXiv 通道与源级错误记账 ───────────────────────────────────────

def test_default_arxiv_branch_builds_candidates(monkeypatch):
    """默认 arXiv 分支应真实执行候选转换（导入路径正确，不被吞成零命中）。"""
    from backend.services.deep_research import arxiv_client
    from backend.services.deep_research.recall_engine import _default_search_one

    async def fake_search_topic(query, limit=20):
        return [{"title": f"Mamba vision {i}", "arxiv_id": f"2401.0000{i}",
                 "doi": None, "year": 2024, "authors": None,
                 "abstract": "state space model", "pdf_url": "https://arxiv.org/pdf/x"}
                for i in range(3)]

    monkeypatch.setattr(arxiv_client, "search_topic", fake_search_topic)
    out = asyncio.run(_default_search_one("arxiv", "mamba vision", 5, "relevance"))
    assert len(out) == 3
    assert getattr(out[0], "abstract") == "state space model"


def test_source_errors_recorded_not_swallowed_as_zero():
    """单源抛异常：计入 source_errors/source_failures，其余源照常工作。"""

    async def search_one(source, keyword, limit, order):
        if source == "arxiv":
            raise RuntimeError("network down")
        return [_cand(f"{keyword} {source} {i}", key=f"k:{keyword}:{source}:{i}",
                      abstract="abs") for i in range(2)]

    async def llm_json(system, user):
        if "检索规划员" in system:
            return {"next_keywords": [], "done": True, "reason": "ok"}
        return _accept_all(user)

    papers, audit = asyncio.run(run_target_recall(
        "q", ["alpha"], depth="quick", now_year=2026, llm_cfg={"api_key": "k"},
        search_one=search_one, llm_json=llm_json,
        backfill_abstracts=lambda dois: asyncio.sleep(0, result={}),
    ))
    stat = audit["keyword_stats"][0]
    assert stat["source_errors"] == {"arxiv": "RuntimeError"}
    assert stat["sources"]["scopus"] == 2 and stat["sources"]["openalex"] == 2
    assert audit["source_failures"]["arxiv:RuntimeError"] == 1
    assert len(papers) == 4  # 只有 scopus/openalex 各 2 篇


# ── F2：排除规则前置 ────────────────────────────────────────────────────────

def test_exclusions_applied_before_model_inputs_and_output():
    from backend.services.research_exclusions import active_exclusions

    planner_users = []

    async def search_one(source, keyword, limit, order):
        return [
            _cand("Mamba vision relevant", key="k:relevant", abstract="ssm vision"),
            _cand("Excluded mouse genome paper", key="k:genome", abstract="genome",
                  doi="10.1/genome"),
        ]

    async def llm_json(system, user):
        if "检索规划员" in system:
            planner_users.append(user)
            return {"next_keywords": [], "done": True, "reason": "ok"}
        return _accept_all(user)

    token = active_exclusions.set({"exclusions": {"dois": ["10.1/genome"]}})
    try:
        papers, audit = asyncio.run(run_target_recall(
            "vision mamba", ["mamba vision"], depth="quick", now_year=2026,
            llm_cfg={"api_key": "k"}, search_one=search_one, llm_json=llm_json,
            backfill_abstracts=lambda dois: asyncio.sleep(0, result={}),
        ))
    finally:
        active_exclusions.reset(token)

    assert audit["excluded"] >= 1
    assert {p["key"] for p in papers} == {"k:relevant"}
    for payload in planner_users:
        assert "mouse genome" not in payload  # 排除论文不进入规划员输入


# ── F3：无摘要候选的三级判定 ────────────────────────────────────────────────

def test_title_filter_rejects_cross_domain_no_abstract_paper():
    """高被引跨域无摘要论文必须经标题初筛，不能直通正式语料。"""

    async def search_one(source, keyword, limit, order):
        if "mamba" in keyword:
            return [
                _cand("Vision Mamba survey", key="k:ok", cites=5, abstract="ssm vision survey"),
                _cand("Mouse genome sequencing", key="k:genome", cites=9999, abstract=None),
            ]
        return []

    async def llm_json(system, user):
        if "只有标题" in system:  # 标题级筛选
            return {"accepted": [], "rejected": [{"key": "k:genome", "reason": "跨域"}]}
        if "检索规划员" in system:
            return {"next_keywords": [], "done": True, "reason": "ok"}
        return _accept_all(user)

    papers, audit = asyncio.run(run_target_recall(
        "vision mamba", ["mamba vision"], depth="quick", now_year=2026,
        llm_cfg={"api_key": "k"}, search_one=search_one, llm_json=llm_json,
        backfill_abstracts=lambda dois: asyncio.sleep(0, result={}),
    ))
    assert {p["key"] for p in papers} == {"k:ok"}
    assert papers[0]["relevance_status"] == "confirmed"
    assert audit["relevance"]["confirmed"] == 1


def test_unknown_papers_do_not_displace_confirmed():
    """筛选失败的 unknown 保留但不挤占 confirmed 的目标配额。"""

    async def search_one(source, keyword, limit, order):
        return [
            _cand(f"confirmed {i}", key=f"k:c{i}", cites=10, abstract="mamba vision")
            for i in range(3)
        ] + [_cand("no abstract high cited", key="k:u1", cites=9999, abstract=None)]

    async def llm_json(system, user):
        if "检索规划员" in system:
            return {"next_keywords": [], "done": True, "reason": "ok"}
        if "只有标题" in system:
            raise RuntimeError("title filter down")  # 无摘要者全部 unknown
        return _accept_all(user)

    papers, audit = asyncio.run(run_target_recall(
        "vision mamba", ["mamba vision"], depth="quick", now_year=2026,
        llm_cfg={"api_key": "k"}, search_one=search_one, llm_json=llm_json,
        backfill_abstracts=lambda dois: asyncio.sleep(0, result={}),
    ))
    by_key = {p["key"]: p for p in papers}
    assert len(papers) == 4  # quick 目标远大于池子：confirmed 3 + unknown 1 都保留
    assert by_key["k:c0"]["relevance_status"] == "confirmed"
    assert by_key["k:u1"]["relevance_status"] == "unknown"
    assert audit["relevance"]["unknown"] == 1


# ── F4/F5/F6：补搜、经典通道、planner 容错 ─────────────────────────────────

def test_filter_shortfall_triggers_topup_within_budget():
    """首轮命中全被筛选拒绝、规划员已说 done → 仍应预算内补搜（筛后确认量驱动）。"""
    state = {"planner_calls": 0}

    async def search_one(source, keyword, limit, order):
        return [_cand(f"{keyword} {source} {i}", key=f"k:{keyword}:{source}:{i}",
                      abstract=f"about {keyword}") for i in range(3)]

    async def llm_seq(system, user):
        if "检索规划员" in system:
            state["planner_calls"] += 1
            if state["planner_calls"] == 2:
                return {"next_keywords": ["beta words"], "done": False, "reason": "补搜缺口"}
            return {"next_keywords": [], "done": True, "reason": "ok"}
        if "筛选员" in system and "只有标题" not in system:
            items = json.loads(user)["papers"]
            if all("alpha" in it["key"] for it in items):
                return {"accepted": [], "rejected": [{"key": it["key"], "reason": "无关"} for it in items]}
            return _accept_all(user)
        return _accept_all(user)

    papers, audit = asyncio.run(run_target_recall(
        "q", ["alpha query"], depth="quick", now_year=2026, llm_cfg={"api_key": "k"},
        search_one=search_one, llm_json=llm_seq,
        backfill_abstracts=lambda dois: asyncio.sleep(0, result={}),
    ))
    assert any(str(r.get("round")).startswith("topup") for r in audit["rounds"])
    assert papers and all("beta" in p["key"] for p in papers)  # 全拒的 alpha 未进入语料
    assert audit["relevance"]["confirmed"] == len(papers)
    assert audit["stop_reason"] == "planner_stop"


def test_time_up_stops_expansion_but_keeps_filtering_collected_papers():
    """运行时长到研究线：首轮后不再规划/补搜，已收集语料照常筛选，分段计时入审计。"""
    calls = {"planner": 0, "filter": 0}

    async def search_one(source, keyword, limit, order):
        return [_cand(f"{keyword} {source} {i}", key=f"k:{keyword}:{source}:{i}",
                      abstract=f"about {keyword}") for i in range(3)]

    async def llm_seq(system, user):
        if "检索规划员" in system:
            calls["planner"] += 1
            return {"next_keywords": ["beta words"], "done": False, "reason": "继续"}
        calls["filter"] += 1
        return _accept_all(user)

    papers, audit = asyncio.run(run_target_recall(
        "q", ["alpha query"], depth="quick", now_year=2026, llm_cfg={"api_key": "k"},
        search_one=search_one, llm_json=llm_seq, time_up=lambda: True,
        backfill_abstracts=lambda dois: asyncio.sleep(0, result={}),
    ))
    assert audit["stop_reason"] == "time_budget"
    assert audit["supplement_budget_stop"] == "time_budget"
    assert calls["planner"] == 0 and calls["filter"] > 0
    assert len(audit["rounds"]) == 1 and papers
    assert {"search", "filter", "backfill"} <= set(audit["timing_s"])


def test_classic_channel_fired_once_per_round():
    cited_calls = []

    async def search_one(source, keyword, limit, order):
        if order == "cited":
            cited_calls.append((source, keyword))
            return []
        return [_cand(f"{keyword} {source} {i}", key=f"k:{keyword}:{source}:{i}",
                      abstract="a") for i in range(3)]

    async def llm_json(system, user):
        if "检索规划员" in system:
            return {"next_keywords": [], "done": True, "reason": "ok"}
        return _accept_all(user)

    asyncio.run(run_target_recall(
        "q", ["alpha beta", "gamma delta", "epsilon zeta"], depth="quick", now_year=2026,
        llm_cfg={"api_key": "k"}, search_one=search_one, llm_json=llm_json,
        backfill_abstracts=lambda dois: asyncio.sleep(0, result={}),
    ))
    # 第一轮 3 个关键词：经典通道只应对锚定词发射 scopus+openalex 各一次
    assert cited_calls == [("scopus", "alpha beta"), ("openalex", "alpha beta")]


# ── F5：轮中达标即停（累计硬顶） ─────────────────────────────────────────────

def test_main_loop_stops_mid_round_at_target():
    """累计到达目标量后立即停发关键词：不再把整轮预算烧成超额数百篇。"""
    calls: list[tuple[str, int]] = []

    async def search_one(source, keyword, limit, order):
        calls.append((keyword, limit))
        # 每源 30 篇、三源不重叠 → 单关键词最多 +90
        return [_cand(f"{keyword} {source} {i}", key=f"k:{keyword}:{source}:{i}",
                      abstract="abs") for i in range(30)]

    async def llm_json(system, user):
        if "检索规划员" in system:
            return {"next_keywords": ["kw-b", "kw-c", "kw-d", "kw-e"], "done": False,
                    "reason": "expand"}
        return _accept_all(user)

    papers, audit = asyncio.run(run_target_recall(
        "q", ["kw-a"], depth="quick", now_year=2026, llm_cfg={"api_key": "k"},
        search_one=search_one, llm_json=llm_json,
        backfill_abstracts=lambda dois: asyncio.sleep(0, result={}),
    ))

    assert audit["stop_reason"] == "target_reached"
    # 最终语料按目标量收口
    assert len(papers) == 200
    fired = {kw for kw, _ in calls}
    # 并发发射（KEYWORD_CONCURRENCY=3）下目标检查在**领词时**进行：
    # 未领到槽位的关键词不再发射（kw-e 不发），在飞的最多小幅超出
    assert "kw-e" not in fired
    assert fired == {"kw-a", "kw-b", "kw-c", "kw-d"}
    # 超额有界：达标轮的总语料 ≤ 目标 + 在飞关键词数 × 三源 × 单源上限
    assert audit["rounds"][-1]["corpus_after"] <= 200 + 3 * 3 * 25


def test_classic_round_skipped_once_target_reached():
    """已达目标量后经典通道不再发射（不做无谓的超额检索）。"""
    cited_calls: list[str] = []

    async def search_one(source, keyword, limit, order):
        if order == "cited":
            cited_calls.append(source)
            return []
        return [_cand(f"{keyword} {source} {i}", key=f"k:{keyword}:{source}:{i}",
                      abstract="a") for i in range(80)]

    async def llm_json(system, user):
        if "检索规划员" in system:
            return {"next_keywords": [], "done": True, "reason": "ok"}
        return _accept_all(user)

    asyncio.run(run_target_recall(
        "q", ["alpha"], depth="quick", now_year=2026, llm_cfg={"api_key": "k"},
        search_one=search_one, llm_json=llm_json,
        backfill_abstracts=lambda dois: asyncio.sleep(0, result={}),
    ))
    # 首个关键词已使语料超过 quick 目标 → 不再有 cited 通道调用
    assert cited_calls == []


# ── F6：planner 容错 ────────────────────────────────────────────────────────

def test_planner_malformed_returns_do_not_lose_material():
    """planner 返回非 dict / 缺字段 / 抛异常：引擎保留已召回材料并优雅终止。"""

    async def search_one(source, keyword, limit, order):
        return [_cand(f"{keyword} {source} {i}", key=f"k:{keyword}:{source}:{i}",
                      abstract="a") for i in range(2)]

    for plan_result, expected_stop in (
        ({"done": True}, "planner_stop"),
        (["not", "a", "dict"], "planner_stop"),
        ({"next_keywords": None}, "planner_stop"),
    ):
        async def llm_json(system, user):
            if "检索规划员" in system:
                return plan_result
            return _accept_all(user)

        papers, audit = asyncio.run(run_target_recall(
            "q", ["alpha"], depth="quick", now_year=2026, llm_cfg={"api_key": "k"},
            search_one=search_one, llm_json=llm_json,
            backfill_abstracts=lambda dois: asyncio.sleep(0, result={}),
        ))
        assert len(papers) == 6  # 材料保留
        assert audit["stop_reason"] == expected_stop

    async def llm_raise(system, user):
        if "检索规划员" in system:
            raise RuntimeError("gateway down")
        return _accept_all(user)

    papers, audit = asyncio.run(run_target_recall(
        "q", ["alpha"], depth="quick", now_year=2026, llm_cfg={"api_key": "k"},
        search_one=search_one, llm_json=llm_raise,
        backfill_abstracts=lambda dois: asyncio.sleep(0, result={}),
    ))
    assert len(papers) == 6 and audit["stop_reason"] == "planner_error"


# ── 基础 loop 语义回归 ─────────────────────────────────────────────────────

def test_zero_hit_keywords_do_not_consume_round_budget():
    planner_calls = []

    async def search_one(source, keyword, limit, order):
        if "deadword" in keyword:
            return []
        return [_cand(f"{keyword} {source} {i}", cites=i, key=f"k:{keyword}:{source}:{i}",
                      abstract="relevant") for i in range(10)]

    async def llm_json(system, user):
        if "检索规划员" in system:
            planner_calls.append(user)
            if len(planner_calls) == 1:
                return {"next_keywords": ["still deadword x"], "done": False, "reason": "重试同义"}
            return {"next_keywords": ["living words"], "done": False, "reason": "补搜"}
        return _accept_all(user)

    papers, audit = asyncio.run(run_target_recall(
        "q", ["deadword one", "deadword two"], depth="quick", now_year=2026,
        llm_cfg={"api_key": "k"}, search_one=search_one, llm_json=llm_json,
        backfill_abstracts=lambda dois: asyncio.sleep(0, result={}),
    ))
    assert audit["effective_rounds"] == 1  # 两轮零命中不计数
    assert audit["attempts"] >= 3
    assert papers


def test_attempt_cap_stops_all_zero_keyword_loop():
    counter = {"n": 0}

    async def search_one(source, keyword, limit, order):
        return []

    async def llm_json(system, user):
        if "检索规划员" in system:
            counter["n"] += 1
            return {"next_keywords": [f"attempt {counter['n']} {w}" for w in ("a", "b", "c", "d")],
                    "done": False, "reason": "stub"}
        return _accept_all(user)

    papers, audit = asyncio.run(run_target_recall(
        "q", ["nothing here"], depth="quick", now_year=2026,
        llm_cfg={"api_key": "k"}, search_one=search_one, llm_json=llm_json,
        backfill_abstracts=lambda dois: asyncio.sleep(0, result={}),
    ))
    assert papers == []
    assert audit["attempts"] == MAX_KEYWORD_ATTEMPTS
    assert audit["effective_rounds"] == 0
    assert audit["stop_reason"] == "attempt_budget"


def test_no_llm_runs_single_round_without_planner_or_filter():
    planner_or_filter = []

    async def search_one(source, keyword, limit, order):
        return [_cand(f"{keyword} {source} {i}", cites=i, key=f"k:{keyword}:{source}:{i}",
                      abstract="text") for i in range(5)]

    async def llm_json(system, user):
        planner_or_filter.append(system)
        return _accept_all(user)

    papers, audit = asyncio.run(run_target_recall(
        "q", ["alpha beta"], depth="quick", now_year=2026,
        llm_cfg=None, search_one=search_one, llm_json=llm_json,
        backfill_abstracts=lambda dois: asyncio.sleep(0, result={}),
    ))
    assert not planner_or_filter
    assert len(audit["rounds"]) == 1
    assert audit["filter"]["skipped"] is True
    assert all(p["relevance_status"] == "unfiltered" for p in papers)
    assert len(papers) == 15


# ── 摘要补全 ───────────────────────────────────────────────────────────────

def test_doi_backfill_batches_of_50_and_fills_abstracts():
    backfill_calls = []

    async def search_one(source, keyword, limit, order):
        return [_cand(f"paper {i}", doi=f"10.1/x{i}", key=f"k:{i}", abstract=None)
                for i in range(60)]

    async def backfill(dois):
        backfill_calls.append(list(dois))
        return {doi: f"abstract of {doi}" for doi in dois}

    papers, audit = asyncio.run(run_target_recall(
        "q", ["alpha"], depth="quick", now_year=2026, llm_cfg=None,
        search_one=search_one, backfill_abstracts=backfill,
    ))
    assert [len(c) for c in backfill_calls] == [50, 10]
    assert audit["abstract_backfill"] == {"needed": 60, "filled": 60, "topup_needed": 0, "topup_filled": 0,
                                          "retry_needed": 0, "retry_filled": 0, "local_filled": 0}
    assert all(p.get("abstract") for p in papers)


def test_local_mirror_match_fills_abstract_and_arxiv_id_before_doi_backfill():
    asked = []

    async def search_one(source, keyword, limit, order):
        return [_cand("On arXiv", doi="10.1/a", key="doi:10.1/a", abstract=None),
                _cand("Not on arXiv", doi="10.1/b", key="doi:10.1/b", abstract=None)]

    def match_local(items):
        return [{"abstract": "mirror abstract", "arxiv_id": "2401.00001"} if title == "On arXiv" else None
                for _doi, title in items]

    async def backfill(dois):
        asked.append(list(dois))
        return {}

    papers, audit = asyncio.run(run_target_recall(
        "q", ["alpha"], depth="quick", now_year=2026, llm_cfg=None,
        search_one=search_one, backfill_abstracts=backfill, match_local=match_local,
    ))
    hit = next(p for p in papers if p["key"] == "doi:10.1/a")
    assert hit["abstract"] == "mirror abstract" and hit["arxiv_id"] == "2401.00001"
    assert hit["pdf_url"] == "https://arxiv.org/pdf/2401.00001"
    assert asked[0] == ["10.1/b"]  # 镜像已补到的不再走 DOI 反查
    assert audit["abstract_backfill"]["local_filled"] == 1


def test_coverage_dimensions_drive_planner_and_topup():
    """覆盖驱动：问题含任务/指标词 → 维度清单进入规划员输入；缺口触发补搜。"""
    planner_payloads = []
    state = {"planner_calls": 0}

    async def search_one(source, keyword, limit, order):
        # 每个词都给足量论文，让数量目标一次达标，检验"覆盖缺口"仍触发补搜
        return [_cand(f"{keyword} {source} {i}", key=f"k:{keyword}:{source}:{i}",
                      abstract=f"mamba vision backbone {keyword} classification accuracy")
                for i in range(4)]

    async def llm_json(system, user):
        if "检索规划员" in system:
            planner_payloads.append(json.loads(user))
            state["planner_calls"] += 1
            if state["planner_calls"] == 1:
                return {"next_keywords": [], "done": True, "reason": "数量达标"}
            return {"next_keywords": ["detection segmentation benchmark"], "done": False,
                    "reason": "补覆盖缺口"}
        return _accept_all(user)

    question = "视觉 Mamba 在图像分类、目标检测、语义分割上与 Transformer 的参数量和推理速度对比"
    papers, audit = asyncio.run(run_target_recall(
        question, ["mamba vision"], depth="quick", now_year=2026, llm_cfg={"api_key": "k"},
        search_one=search_one, llm_json=llm_json,
        backfill_abstracts=lambda dois: asyncio.sleep(0, result={}),
    ))
    # 维度清单来自问题的任务/指标词
    dims = audit["coverage_dimensions"]
    assert "图像分类任务证据" in dims and "目标检测任务证据" in dims
    assert "参数量对比证据" in dims and "推理速度/延迟证据" in dims
    # 规划员输入携带覆盖清单
    assert planner_payloads and planner_payloads[0].get("coverage_checklist")
    # 摘要只含 classification/accuracy：检测/分割/速度维度未覆盖 → 触发补搜
    assert any(str(r.get("round")).startswith("topup") for r in audit["rounds"])
    assert audit["coverage_trace"]


def test_coverage_dimensions_include_dynamic_topic_and_domain_dimensions():
    from backend.services.deep_research.recall_engine import _coverage_dimensions

    generic = _coverage_dimensions("蛋白质折叠研究", ["protein folding diffusion model"])
    assert generic[0]["dimension"].startswith("研究主题相关性")
    assert {"protein", "folding", "diffusion", "model"}.issubset(generic[0]["probes"])

    dims = _coverage_dimensions("detection 与 segmentation 的复杂度对比", ["vision benchmark"])
    assert len(dims) == 4
    medical = _coverage_dimensions("medical imaging in clinic", ["clinical diagnosis"])
    probes = next(row["probes"] for row in medical if row["dimension"] == "医学影像应用证据")
    assert "clinic" in probes and "clnic" not in probes


# ── 筛选并发与暂停安全点 ─────────────────────────────────────────────────────

def _filter_papers(n, with_abstract=True):
    return [{"key": f"k{i}", "title": f"paper {i}",
             "abstract": f"abstract about topic {i}" if with_abstract else "",
             "year": 2025, "citation_count": 0} for i in range(n)]


def test_filter_pool_runs_batches_concurrently():
    """筛选批次由多 worker 并发执行：同一时刻存在 ≥2 个在飞 LLM 调用。"""
    from backend.services.deep_research.recall_engine import _filter_pool

    in_flight = 0
    max_in_flight = 0

    async def llm_json(system, user):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return _accept_all(user)

    papers = _filter_papers(30)
    kept, audit = asyncio.run(_filter_pool(
        papers, "q", batch_size=10, llm_json=llm_json, verdict_cache={}))
    assert audit["batches"] == 3
    assert max_in_flight >= 2  # 串行实现 max 恒为 1
    assert len(kept) == 30 and all(p["relevance_status"] == "confirmed" for p in kept)


def test_filter_pool_pause_between_batches_preserves_inflight_verdicts():
    """暂停在领新批次时观察：在飞批次完成并写缓存，未领的批次留给续跑。"""
    from backend.services.deep_research.recall_engine import _filter_pool
    from backend.services.deep_research.stage_agent import StagePausedError

    screened_so_far = []

    async def on_progress(done, total):
        screened_so_far.append(done)

    async def llm_json(system, user):
        await asyncio.sleep(0.01)
        return _accept_all(user)

    papers = _filter_papers(60)

    def should_pause():
        # 首批完成后置位：worker 领下一批前会观察到
        return bool(screened_so_far) and screened_so_far[-1] >= 10

    verdict_cache: dict[str, str] = {}
    try:
        asyncio.run(_filter_pool(papers, "q", batch_size=10, llm_json=llm_json,
                                 verdict_cache=verdict_cache, should_pause=should_pause,
                                 on_progress=on_progress))
        raise AssertionError("expected StagePausedError")
    except StagePausedError:
        pass
    # 4 个 worker 同时领批：首批 4 批全部在飞并完成（判定入缓存不浪费），
    # 第 5 批起不再发射——未筛篇目留给续跑
    assert 10 <= len(verdict_cache) <= 40
    assert len(verdict_cache) < 60


def test_lite_depth_target_is_50_and_stops_mid_round():
    """lite（轻量）档：目标量 50，比快览更轻，达标即停不发多余关键词。"""
    fired: list[str] = []

    async def search_one(source, keyword, limit, order):
        fired.append(keyword)
        return [_cand(f"{keyword} {source} {i}", key=f"k:{keyword}:{source}:{i}",
                      abstract="abs") for i in range(30)]

    async def llm_json(system, user):
        if "检索规划员" in system:
            return {"next_keywords": ["kw-b", "kw-c"], "done": False, "reason": "expand"}
        return _accept_all(user)

    papers, audit = asyncio.run(run_target_recall(
        "q", ["kw-a"], depth="lite", now_year=2026, llm_cfg={"api_key": "k"},
        search_one=search_one, llm_json=llm_json,
        backfill_abstracts=lambda dois: asyncio.sleep(0, result={}),
    ))

    assert audit["stop_reason"] == "target_reached"
    assert len(papers) == 50  # 语料按 lite 目标量收口
    assert set(fired) == {"kw-a"}  # 单关键词 3 源×30 已超 50 → 后续词不再发射
