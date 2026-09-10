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
    MAX_KEYWORD_ATTEMPTS, dual_track_trim, run_target_recall,
)


def _cand(title, *, year=2025, cites=0, doi=None, abstract=None, key=None):
    return SimpleNamespace(
        title=title, canonical_key=key or f"t:{title.lower()}", year=year,
        citation_count=cites, doi=doi, arxiv_id=None, url=None, pdf_url=None,
        authors=None, abstract=abstract, seed_relation=None, extra=None,
    )


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
        return {"rejected": []}

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
        return {"rejected": []}

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
        if "标题" in system:  # 标题级筛选
            return {"rejected": [{"key": "k:genome", "reason": "跨域"}]}
        if "检索规划员" in system:
            return {"next_keywords": [], "done": True, "reason": "ok"}
        return {"rejected": []}

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
        if "标题" in system:
            raise RuntimeError("title filter down")  # 无摘要者全部 unknown
        return {"rejected": []}

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
        if "筛选员" in system and "标题" not in system:
            items = json.loads(user)["papers"]
            if all("alpha" in it["key"] for it in items):
                return {"rejected": [{"key": it["key"], "reason": "无关"} for it in items]}
            return {"rejected": []}
        return {"rejected": []}

    papers, audit = asyncio.run(run_target_recall(
        "q", ["alpha query"], depth="quick", now_year=2026, llm_cfg={"api_key": "k"},
        search_one=search_one, llm_json=llm_seq,
        backfill_abstracts=lambda dois: asyncio.sleep(0, result={}),
    ))
    assert any(str(r.get("round")).startswith("topup") for r in audit["rounds"])
    assert papers and all("beta" in p["key"] for p in papers)  # 全拒的 alpha 未进入语料
    assert audit["relevance"]["confirmed"] == len(papers)
    assert audit["stop_reason"] == "planner_stop"


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
        return {"rejected": []}

    asyncio.run(run_target_recall(
        "q", ["alpha beta", "gamma delta", "epsilon zeta"], depth="quick", now_year=2026,
        llm_cfg={"api_key": "k"}, search_one=search_one, llm_json=llm_json,
        backfill_abstracts=lambda dois: asyncio.sleep(0, result={}),
    ))
    # 第一轮 3 个关键词：经典通道只应对锚定词发射 scopus+openalex 各一次
    assert cited_calls == [("scopus", "alpha beta"), ("openalex", "alpha beta")]


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
            return {"rejected": []}

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
        return {"rejected": []}

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
        return {"rejected": []}

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
        return {"rejected": []}

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
        return {"rejected": []}

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
    assert audit["abstract_backfill"] == {"needed": 60, "filled": 60}
    assert all(p.get("abstract") for p in papers)
