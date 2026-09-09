"""领域探索（explorer，问句驱动免读领域认知）单测。

守住的行为契约（GOAL.md）：
1. decompose 无 Key → 原句直检回退，不抛异常；
2. recall 去重（canonical_key 合并、被引取大、字段互补）与超量裁剪（保留近期席位）；
3. 计量纯算法：引用速度 = 被引/年龄，突增 = 近窗口 + 高速度；统计锚定 now_year 可测；
4. 聚类与深度研究图谱同口径（graph_analytics 分词/社区/标签），孤立点 cluster=-1；
5. 图谱产物是 ResearchGraph 形状（前端 DomainOverview 直接复用）；
6. 无 Key → synthesize 返回 None（统计视图照常可用），证据不足同样跳过；
7. run_exploration 端到端（stub provider + 无 Key）：状态 done、产物齐、SSE 事件顺序正确。
"""

import asyncio
from types import SimpleNamespace

import pytest

from backend.services.deep_research import explorer
from backend.services.deep_research.explorer import (
    _cap_papers, _dedupe_candidates, _normalize_synthesis,
    DecompositionUnavailableError, build_graph_payload, cluster_papers, compute_metrics, decompose_question,
    recall_papers, refine_decomposition_with_tools, run_exploration, synthesize_exploration,
)


@pytest.fixture(autouse=True)
def offline_retrieval(monkeypatch):
    from backend.services.deep_research import literature_retrieval as retrieval
    async def unavailable(*args, **kwargs):
        raise RuntimeError("offline test")
    monkeypatch.setattr(retrieval, "document_vectors", unavailable)
    monkeypatch.setattr(retrieval, "get_reranker_config", lambda: {"enabled": False})
    async def audit(*args, **kwargs):
        return {"status": "skipped", "support_rate": None}
    monkeypatch.setattr(explorer, "audit_answer", audit)


# ── 测试材料 ────────────────────────────────────────────────────────────────

def _cand(title, *, year=None, cites=0, doi=None, abstract=None, key=None):
    return SimpleNamespace(
        canonical_key=key or f"k:{title}", title=title, year=year,
        citation_count=cites, doi=doi, arxiv_id=None, authors=None,
        abstract=abstract, seed_relation="search_match",
    )


def _paper(title, year, cites, abstract=""):
    return {"key": f"k:{title}", "title": title, "year": year,
            "citation_count": cites, "abstract": abstract or title}


# ── 1) decompose ─────────────────────────────────────────────────────────────

def test_decompose_fallback_without_key():
    out = asyncio.run(decompose_question("用 AI 识别肿瘤有没有人做", None, max_queries=4))
    assert out["fallback"] is True
    assert out["en_queries"] == ["用 AI 识别肿瘤有没有人做"]
    assert out["intent"] == "landscape"


def test_decompose_stops_on_llm_failure_without_usable_english_query(monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("llm down")
    monkeypatch.setattr("backend.services.deep_research.domain_synthesizer._llm_json", boom)
    with pytest.raises(DecompositionUnavailableError, match="未使用中文原句检索"):
        asyncio.run(decompose_question("用 AI 识别肿瘤有没有人做", {"api_key": "k", "model": "m"}, max_queries=4))


def test_decompose_keeps_usable_english_fallback_on_llm_failure(monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("llm down")
    monkeypatch.setattr("backend.services.deep_research.domain_synthesizer._llm_json", boom)
    out = asyncio.run(decompose_question("Mamba for tumor detection", {"api_key": "k", "model": "m"}, max_queries=4))
    assert out["fallback"] is True
    assert any("Mamba" in query for query in out["en_queries"])


def test_decompose_stops_when_valid_json_still_contains_only_chinese_queries(monkeypatch):
    """不是所有 JSON 都是合格拆解：学术库检索式必须是真正的英文关键词串。"""
    async def fake_llm(*args, **kwargs):
        return {"intent": "landscape", "en_queries": ["肿瘤识别", "AI 肿瘤诊断"], "focus_terms": []}

    monkeypatch.setattr("backend.services.deep_research.domain_synthesizer._llm_json", fake_llm)
    with pytest.raises(DecompositionUnavailableError, match="没有可用的英文学术检索式"):
        asyncio.run(decompose_question("用 AI 识别肿瘤有没有人做", {"api_key": "k", "model": "m"}, max_queries=4))


def test_decompose_normalizes_and_sanitizes(monkeypatch):
    async def fake_llm(cfg, system, user, *, max_tokens, temperature=0.15):
        return {"intent": "novelty", "en_queries": ['tumor "detection" (deep learning)', "", "tumor detection"],
                "focus_terms": ["肿瘤检测"]}
    monkeypatch.setattr("backend.services.deep_research.domain_synthesizer._llm_json", fake_llm)
    out = asyncio.run(decompose_question("q", {"api_key": "k", "model": "m"}, max_queries=2))
    assert out["fallback"] is False
    assert len(out["en_queries"]) == 2
    assert '"' not in out["en_queries"][0]
    assert out["intent"] == "novelty"


def test_decompose_strips_boolean_syntax(monkeypatch):
    """模型夹带 TITLE-ABS-KEY(... OR ...) 布尔语法时剥干净（Scopus 会二次包裹）。"""
    async def fake_llm(cfg, system, user, *, max_tokens, temperature=0.15):
        return {"intent": "novelty",
                "en_queries": ['TITLE-ABS-KEY(("artificial intelligence" OR "deep learning") AND (tumor detection))'],
                "focus_terms": []}
    monkeypatch.setattr("backend.services.deep_research.domain_synthesizer._llm_json", fake_llm)
    out = asyncio.run(decompose_question("q", {"api_key": "k", "model": "m"}, max_queries=4))
    q = out["en_queries"][0]
    assert "TITLE-ABS-KEY" not in q
    assert " OR " not in q and " AND " not in q
    assert "artificial intelligence" in q and "tumor detection" in q


def test_query_react_loop_requires_multiple_real_previews_for_zero_hits(monkeypatch):
    decisions = iter([
        {"action": "finish", "finish": True, "rationale_summary": "初步完成"},
        {"action": "preview_search", "arguments": {}},
        {"action": "revise_queries", "arguments": {
            "en_queries": ["artificial intelligence cancer diagnosis", "deep learning tumor detection"],
            "core_query": "cancer diagnosis",
        }},
        {"action": "preview_search", "arguments": {}},
        {"action": "finish", "finish": True, "rationale_summary": "两轮仍无命中"},
    ])

    async def fake_llm(*args, **kwargs):
        return next(decisions)

    calls = []

    async def empty_recall(queries, **kwargs):
        calls.append(list(queries))
        return [], "stub"

    monkeypatch.setattr("backend.services.deep_research.domain_synthesizer._llm_json", fake_llm)
    monkeypatch.setattr(explorer, "recall_papers", empty_recall)
    decompose, audit = asyncio.run(refine_decomposition_with_tools(
        "用 AI 识别肿瘤有没有人做",
        {"intent": "novelty", "en_queries": ["AI tumor detection"], "focus_terms": [],
         "fallback": False, "core_query": "tumor detection"},
        {"api_key": "k", "model": "m"}, depth="standard", now_year=2026,
    ))
    assert audit["status"] == "completed"
    assert audit["preview_hit_count"] == 0
    assert len(audit["preview_rounds"]) == len(calls) == 2
    assert decompose["en_queries"][0] == "artificial intelligence cancer diagnosis"
    assert audit["trace"][0]["validation"]["passed"] is False


# ── 2) recall：去重与裁剪 ────────────────────────────────────────────────────

def test_recall_runs_queries_in_parallel(monkeypatch):
    """召回按检索式并行发射（付费源自适应限速下的安全并行），不再逐式串行。"""

    class _SlowStub:
        name = "slowstub"

        def __init__(self):
            self.active = 0
            self.max_active = 0

        async def search_works(self, query, limit=20, **kwargs):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.05)
            self.active -= 1
            return [
                _cand(f"{query} p{i}", year=2020, cites=i, key=f"k:{query}-{i}")
                for i in range(2)
            ]

        async def aclose(self):
            pass

    stub = _SlowStub()
    monkeypatch.setattr(
        "backend.services.deep_research.providers.get_provider", lambda cfg: stub,
    )
    papers, name = asyncio.run(recall_papers(
        ["tumor detection", "language model", "cancer ai"], per_query=10, cap=50, now_year=2026,
    ))
    assert name == "slowstub"
    # 请求预算：3 条 relevance + 1 条经典补充；每路2条，避免经典层把付费请求翻倍
    assert len(papers) == 6  # Quoted classic query returns the same normalized titles.
    # 串行实现 max_active 恒为 1；并行后同时 in-flight ≥ 2
    assert stub.max_active >= 2


def test_recall_dual_channel_requests_relevance_and_cited(monkeypatch):
    """双通道召回：每条检索式同时发 relevance 主路与被引经典路（领域全景的双层承诺）。"""

    class _RecordingStub:
        name = "recstub"

        def __init__(self):
            self.calls: list[tuple[str, str, int]] = []

        async def search_works(self, query, limit=20, **kwargs):
            self.calls.append((query, str(kwargs.get("order", "relevance")), limit))
            tag = "c" if kwargs.get("order") == "cited" else "r"
            return [_cand(f"{query} {tag}", year=2020, cites=1, key=f"k:{query}-{tag}")]

        async def aclose(self):
            pass

    stub = _RecordingStub()
    monkeypatch.setattr(
        "backend.services.deep_research.providers.get_provider", lambda cfg: stub,
    )
    papers, _ = asyncio.run(recall_papers(
        ["rag evaluation framework benchmark", "rag llm strategies methods"], per_query=24, cap=50, now_year=2026,
    ))
    # relevance 路 limit=per_query；cited 路查询为前 3 词加引号的精确短语，跨式去重
    assert ("rag evaluation framework benchmark", "relevance", 24) in stub.calls
    assert ('"rag evaluation framework"', "cited", 12) in stub.calls
    assert len([c for c in stub.calls if c[1] == "cited"]) == 1
    assert len(stub.calls) == 3
    # 两路候选键不同故都保留（真实源同篇论文共享 canonical_key，由 _dedupe_candidates 合并）
    assert sorted(p["key"] for p in papers) == [
        'k:"rag evaluation framework"-c',
        "k:rag evaluation framework benchmark-r",
        "k:rag llm strategies methods-r",
    ]


def test_dedupe_merges_by_key_and_takes_max_citations():
    a = _cand("Tumor detection", year=2020, cites=10, abstract="abs")
    b = _cand("Tumor detection", year=2020, cites=30, doi="10.1/x")   # 同 key：被引取大、doi 互补
    c = _cand("Other paper", year=2021, cites=1, key="k:other")
    out = _dedupe_candidates([a, b, c])
    assert len(out) == 2
    merged = next(p for p in out if p["key"] == "k:Tumor detection")
    assert merged["citation_count"] == 30
    assert merged["doi"] == "10.1/x"
    assert merged["abstract"] == "abs"


def test_cap_preserves_upstream_relevance_order():
    old = [_paper(f"old {i}", 2015, 100 + i) for i in range(10)]
    fresh = [_paper(f"new {i}", 2025, 1) for i in range(3)]
    capped = _cap_papers(old + fresh, cap=12, now_year=2026)
    assert len(capped) == 12
    assert capped == (old + fresh)[:12]
    assert _cap_papers(fresh + old, cap=3, now_year=2026) == fresh


# ── 2b) 盲测排除 ─────────────────────────────────────────────────────────────

def test_apply_exclusions_filters_doi_and_arxiv_with_normalization():
    papers = [
        {"key": "k:survey", "title": "RAG Survey", "doi": "10.48550/ARXIV.2312.10997"},
        {"key": "k:survey-v", "title": "RAG Survey v5", "arxiv_id": "2312.10997v5"},
        {"key": "k:keep-doi", "title": "Kept by DOI", "doi": "10.1/keep"},
        {"key": "k:keep-plain", "title": "Kept plain"},
    ]
    options = {"exclusions": {"dois": ["https://doi.org/10.48550/arxiv.2312.10997"],
                              "arxiv_ids": ["arxiv:2312.10997"]}}
    kept, stats = explorer.apply_exclusions(papers, options)
    assert [p["key"] for p in kept] == ["k:keep-doi", "k:keep-plain"]
    assert stats["count"] == 2
    assert stats["removed_titles"] == ["RAG Survey", "RAG Survey v5"]
    # 归一化后两侧一致：大写 DOI、doi.org 前缀、版本号、arxiv: 前缀都命中同一排除项


def test_apply_exclusions_noop_without_options():
    papers = [{"key": "k:a", "title": "A", "doi": "10.1/a"}]
    kept, stats = explorer.apply_exclusions(papers, {})
    assert kept is papers
    assert stats == {"count": 0}


def test_seed_options_snapshots_normalized_exclusions():
    from backend.services.research_sources import seed_options
    options = seed_options(None, None, None, True,
                           exclude_dois=["https://doi.org/10.48550/arXiv.2312.10997", " 10.1/x ", "10.1/x"],
                           exclude_arxiv_ids=["2312.10997v5"])
    assert options["exclusions"] == {
        "dois": ["10.1/x", "10.48550/arxiv.2312.10997"],
        "arxiv_ids": ["2312.10997"],
    }
    # 无排除项时不写入键，保持 options 快照最小
    assert "exclusions" not in seed_options(None, None, None, True)


# ── 3) metrics ───────────────────────────────────────────────────────────────

def test_metrics_velocity_surge_and_stats():
    papers = [
        _paper("classic", 2016, 500),   # 速度 50，但不在近窗口 → 无 surge
        _paper("rising", 2025, 12),     # 速度 12，近窗口 → surge
        _paper("recent slow", 2025, 1), # 速度 1 → 无 surge
        _paper("unknown year", None, 9),
    ]
    m = compute_metrics(papers, now_year=2026)
    by_title = {p["title"]: p for p in papers}
    assert by_title["classic"]["citation_velocity"] == 50.0
    assert by_title["classic"]["surge"] is False
    assert by_title["rising"]["surge"] is False  # Missing history cannot establish growth.
    assert by_title["recent slow"]["surge"] is False
    assert by_title["unknown year"]["citation_velocity"] is None
    assert m["total"] == 4
    assert m["by_year"] == {2016: 1, 2025: 2}
    assert m["recent_share"] == round(2 / 3, 3)
    assert m["year_span"] == [2016, 2025]
    assert m["top_cited"][0] == "k:classic"
    assert m["rising"] == []


# ── 3b) metrics 升级：近 2 年动态 / 轨迹 / 同年百分位 / 期刊 / 关键词 ─────────

def _with_history(paper, *, w2, w1=0):
    """给论文注入 OpenAlex 风格 counts_by_year（近 2 年合计 w2，再往前 2 年合计 w1）。"""
    paper["counts_by_year"] = [
        {"year": 2024, "cited_by_count": w2 // 2},
        {"year": 2025, "cited_by_count": w2 - w2 // 2},
        {"year": 2022, "cited_by_count": w1 // 2},
        {"year": 2023, "cited_by_count": w1 - w1 // 2},
    ]
    return paper


def test_metrics_recent_window_and_trajectory():
    hot_old = _with_history(_paper("hot old", 2018, 100), w2=60, w1=10)    # 爬升
    cold_old = _with_history(_paper("cold old", 2018, 100), w2=4, w1=40)   # 回落
    flat_old = _with_history(_paper("flat old", 2018, 80), w2=20, w1=20)   # 平稳
    young = _with_history(_paper("young", 2025, 10), w2=10, w1=0)          # 太新不定型
    compute_metrics([hot_old, cold_old, flat_old, young], now_year=2026)
    assert hot_old["recent_citations"] == 60
    assert hot_old["citation_velocity_2y"] == 30.0
    assert hot_old["trajectory"] == "rising"
    assert cold_old["trajectory"] == "declining"
    assert flat_old["trajectory"] == "steady"
    assert young["trajectory"] is None          # 历史 <4 年不算轨迹
    # 突增用近 2 年速度：young 近 2 年 10 → 5/年 ≥ 2 → surge
    assert young["surge"] is False  # Publication year is not a complete exposure year.


def test_metrics_surge_prefers_recent_velocity_over_lifetime():
    """全周期速度虚高的老新贵：近 2 年失速时不应再算突增（且不在近窗口本来也不算）。"""
    p = _with_history(_paper("faded", 2025, 90), w2=2, w1=0)  # velocity 90，2y 速度 1
    compute_metrics([p], now_year=2026)
    assert p["surge"] is False


def test_metrics_cohort_percentile_and_highly_cited():
    """同年 cohort 内被引百分位：唯一 top（cohort≥5）→ highly_cited；小 cohort 不打徽章。"""
    papers = [_paper(f"p2020-{i}", 2020, 1 + i) for i in range(20)]  # 1..20
    papers[0]["citation_count"] = 999                                  # 唯一 top
    small = [_paper(f"p2021-{i}", 2021, i) for i in range(3)]          # cohort=3
    small[2]["citation_count"] = 500
    m = compute_metrics(papers + small, now_year=2026)
    top = next(p for p in papers if p["title"] == "p2020-0")
    mid = next(p for p in papers if p["title"] == "p2020-10")          # 11 被引
    small_top = small[2]
    assert top["citation_percentile"] == 97.5
    assert top["highly_cited"] is True
    # less=9（原 1..10 中 1 被改成 999），equal=1 → (9+0.5)/20 = 47.5
    assert mid["citation_percentile"] == 47.5
    assert mid["highly_cited"] is False
    # cohort<5：百分位照算，但不给 highly_cited 徽章（样本太小，top1% 无意义）
    assert small_top["citation_percentile"] == 83.3
    assert small_top["highly_cited"] is False
    assert m["highly_cited"] == ["k:p2020-0"]


def test_metrics_venue_ranking_and_keyword_trend():
    papers = [
        {**_paper("a", 2024, 10), "venue": {"name": "Nature", "sjr": 19.7, "snip": 10.6, "citescore": 77.7},
         "keywords": ["test-time adaptation", "segmentation"]},
        {**_paper("b", 2025, 5), "venue": {"name": "Nature", "sjr": 19.7, "snip": 10.6, "citescore": 77.7},
         "keywords": ["test-time adaptation"]},
        {**_paper("c", 2025, 1), "venue": {"name": "ArXiv"}, "keywords": ["test-time adaptation", "mamba"]},
        _paper("d", 2025, 1),  # 无 venue / 关键词
    ]
    m = compute_metrics(papers, now_year=2026)
    assert m["venue_ranking"][0]["name"] == "Nature"
    assert m["venue_ranking"][0]["count"] == 2
    assert m["venue_ranking"][0]["sjr"] == 19.7
    assert all(r["count"] >= 2 for r in m["venue_ranking"])   # 单篇期刊不上榜
    trend = m["keyword_trend"]
    # ≥3 篇才进矩阵：test-time adaptation 3 次；mamba/segmentation 被过滤
    assert [r["keyword"] for r in trend["rows"]] == ["test-time adaptation"]
    assert trend["years"] == [2024, 2025]      # 语料内的年份范围（2026 无论文不占列）
    assert trend["rows"][0]["counts"] == [1, 2]
    assert m["enriched"]["with_history"] == 0


def test_dedupe_merges_extra_enrichment_fields():
    a = _cand("Tumor detection", year=2020, cites=10)
    a.extra = {"scopus_id": "123", "venue_source_id": "15134", "venue_name": "Information Sciences"}
    b = _cand("Tumor detection", year=2020, cites=30, doi="10.1/x")
    b.extra = {"counts_by_year": [{"year": 2025, "cited_by_count": 4}], "keywords": ["tumor"]}
    out = _dedupe_candidates([a, b])
    merged = out[0]
    assert merged["venue_source_id"] == "15134"      # Scopus 期刊身份
    assert merged["counts_by_year"] == [{"year": 2025, "cited_by_count": 4}]
    assert merged["keywords"] == ["tumor"]
    assert merged["citation_count"] == 30


# ── 3c) enrich：OpenAlex 补查 + Serial Title 期刊指标 ──────────────────────────

def test_enrich_papers_fills_history_keywords_and_venue(monkeypatch):
    from backend.services.deep_research import explorer

    class _OA:
        async def get_citation_history(self, doi):
            assert doi == "10.1/x"
            return {
                "openalex_cited_by": 9,
                "counts_by_year": [{"year": 2026, "cited_by_count": 3}, {"year": 2025, "cited_by_count": 6}],
                "abstract": "abs from openalex",
                "keywords": ["test-time adaptation"],
                "venue_name": "Information Sciences",
                "venue_issn": "0020-0255",
            }

    monkeypatch.setattr(
        "backend.services.deep_research.providers.openalex.get_shared_openalex_provider", lambda: _OA(),
    )

    class _ScopusStub:
        def __init__(self, api_key=""):
            self.ids = None

        async def get_serial_metrics(self, ids):
            self.ids = list(ids)
            return {"15134": {"name": "Information Sciences", "sjr": 1.5, "snip": 1.7, "citescore": 14.6}}

        async def _get(self, path, params=None):
            return {}

        async def aclose(self):
            pass

    stub = _ScopusStub()
    monkeypatch.setattr("backend.services.deep_research.providers.scopus.ScopusProvider", lambda api_key="": stub)
    monkeypatch.setattr(
        "backend.services.deep_research.providers._elsevier_key", lambda cfg: "k",
    )
    monkeypatch.setattr(
        "backend.services.ai_config.get_research_config", lambda: {"elsevier_api_key": "k"},
    )

    with_doi = {"key": "k1", "title": "t1", "doi": "10.1/x", "venue_source_id": "15134",
                "venue_name": "Information Sciences"}
    no_doi = {"key": "k2", "title": "t2"}
    stats = asyncio.run(explorer.enrich_papers([with_doi, no_doi]))
    assert stats["history_matched"] == 1
    assert with_doi["counts_by_year"][0]["cited_by_count"] == 3
    assert with_doi["abstract"] == "abs from openalex"
    assert with_doi["keywords"] == ["test-time adaptation"]
    assert with_doi["venue"]["sjr"] == 1.5           # Serial Title 指标挂上
    assert stub.ids == ["15134"]                     # source-id 批量查询
    assert "counts_by_year" not in no_doi            # 无 DOI 不动
    assert "venue" not in no_doi


def test_enrich_papers_non_fatal_on_failures(monkeypatch):
    from backend.services.deep_research import explorer

    class _BrokenOA:
        async def get_citation_history(self, doi):
            raise RuntimeError("openalex down")

    monkeypatch.setattr(
        "backend.services.deep_research.providers.openalex.get_shared_openalex_provider", lambda: _BrokenOA(),
    )
    monkeypatch.setattr(
        "backend.services.deep_research.providers._elsevier_key", lambda cfg: "",
    )
    papers = [{"key": "k", "title": "t", "doi": "10.1/x"}]
    stats = asyncio.run(explorer.enrich_papers(papers))
    assert stats == {"history_matched": 0, "venue_metrics": 0}
    assert "counts_by_year" not in papers[0]


# ── 4) cluster ───────────────────────────────────────────────────────────────

def _cluster_fixture():
    tumor = [
        _paper(f"tumor detection via {w}", 2020 + i, 50 * (6 - i),
               f"deep learning tumor detection {w} cancer segmentation")
        for i, w in enumerate(["cnn", "transformer", "attention", "unet", "gan"])
    ]
    nlp = [
        _paper(f"language model {w}", 2019 + i, 40 * (5 - i),
               f"pretraining language model {w} transformer text")
        for i, w in enumerate(["bert", "gpt", "t5", "roberta", "llama"])
    ]
    lonely = _paper("quantum chemistry", 2024, 3, "quantum chemistry simulation")
    return tumor + nlp + [lonely]


def test_cluster_separates_topics_and_marks_isolated():
    papers = _cluster_fixture()
    key2cluster, edges, mod = cluster_papers(papers)
    tumor_keys = {p["key"] for p in papers if "tumor" in p["title"]}
    nlp_keys = {p["key"] for p in papers if "language" in p["title"]}
    c_tumor = {key2cluster[k] for k in tumor_keys}
    c_nlp = {key2cluster[k] for k in nlp_keys}
    # 两个主题各自聚成一簇，且互不重叠
    assert len(c_tumor) == 1 and len(c_nlp) == 1
    assert c_tumor != c_nlp
    # 完全无关的孤文 → cluster=-1
    assert key2cluster["k:quantum chemistry"] == -1
    assert all(e["kind"] == "related" for e in edges)
    assert 0.0 <= mod <= 1.0
    # 簇标签写入论文（TF-ICF 关键词，非空）
    labeled = [p["cluster_label"] for p in papers if p["cluster"] >= 0]
    assert labeled and all(labeled)


# ── 5) graph payload 形状 ────────────────────────────────────────────────────

def test_graph_payload_is_research_graph_shaped():
    papers = _cluster_fixture()
    key2cluster, edges, mod = cluster_papers(papers)
    g = build_graph_payload(papers, edges, modularity=mod)
    ids = [n["id"] for n in g["nodes"]]
    assert len(ids) == len(set(ids)) == len(papers)
    assert all(n["kind"] == "paper" and n["label"] for n in g["nodes"])
    metas = [n["meta"] for n in g["nodes"]]
    assert all(isinstance(m["cluster"], int) for m in metas)
    assert all(0.0 <= m["importance"] <= 1.0 for m in metas)
    assert all(e["kind"] == "related" for e in g["edges"])
    idset = set(ids)
    assert all(e["source"] in idset and e["target"] in idset for e in g["edges"])
    assert g["stats"]["node_count"] == len(papers)
    assert g["stats"]["by_kind"]["related"] == len(g["edges"])
    assert g["stats"]["clusters"] >= 1


# ── 6) synthesize：无 Key / 证据不足 / 归一化 ─────────────────────────────────

def test_synthesize_skipped_without_key():
    out = asyncio.run(synthesize_exploration(
        "q", {"en_queries": ["a"]}, _cluster_fixture(), {}, None, max_papers=10))
    assert out is None


def test_synthesize_skipped_when_evidence_thin():
    papers = [_paper("a", 2020, 1), _paper("b", 2021, 1)]
    out = asyncio.run(synthesize_exploration(
        "q", {"en_queries": ["a"]}, papers, {}, {"api_key": "k", "model": "m"}, max_papers=10))
    assert out is None


def test_synthesize_react_loop_inspects_tools_before_finish(monkeypatch):
    papers = [_paper(f"paper {i} tumor", 2020 + i, 10 + i, "tumor detection evidence")
              for i in range(4)]
    decisions = iter([
        {"action": "inspect_evidence", "arguments": {"query": "tumor detection", "limit": 3}},
        {"action": "inspect_metrics", "arguments": {}},
        {"action": "submit_synthesis", "arguments": {
            "verdict": {"status": "done_partial", "confidence": 0.7,
                        "explanation": "材料中存在相关工作。", "evidence": ["E1"]},
            "maturity": "已有部分工作。", "landscape_summary": "材料显示已有相关方向。",
            "hotspots": [], "gaps": [], "follow_ups": [], "markdown": "报告 [E1]",
        }},
        {"action": "finish", "finish": True},
    ])

    async def fake_llm(*args, **kwargs):
        return next(decisions)

    async def fake_retrieve(items, *args, **kwargs):
        return items, {"method": "fixture"}

    async def no_paradigms(*args, **kwargs):
        return []

    monkeypatch.setattr("backend.services.deep_research.domain_synthesizer._llm_json", fake_llm)
    monkeypatch.setattr(explorer, "retrieve_materials", fake_retrieve)
    monkeypatch.setattr(explorer, "_task_paradigms", no_paradigms)
    events = []

    async def on_iteration(payload):
        events.append(payload)

    out = asyncio.run(synthesize_exploration(
        "tumor idea", {"en_queries": ["tumor detection"], "fallback": False}, papers,
        {"total": 4, "clusters": 1}, {"api_key": "k", "model": "m"},
        max_papers=4, depth="standard", on_iteration=on_iteration,
    ))
    assert out is not None and out.verdict.status == "done_partial"
    assert out.stats["agent_loop"]["status"] == "completed"
    assert out.stats["agent_loop"]["iterations"] == 4
    assert any(event.get("phase") == "tool" for event in events)


def test_normalize_synthesis_clamps_enum_confidence_evidence():
    subset = [{**_paper(f"p{i}", 2020, 10), "_evidence_no": f"E{i}"} for i in range(1, 4)]
    data = {
        "verdict": {
            "status": "totally_done",           # 越界 → unclear
            "confidence": 5,                    # 越界 → 1.0
            "explanation": "x" * 1000,          # 截断
            "evidence": ["E1", "E9", "E2"],     # E9 不存在 → 丢弃
        },
        "hotspots": ["h1", "h2", "h3", "h4", "h5", "h6", "h7", "h8"],  # 截到 6
        "markdown": "m",
    }
    s = _normalize_synthesis(data, subset)
    assert s.verdict.status == "unclear"
    assert s.verdict.confidence == 1.0
    assert len(s.verdict.explanation) <= 601
    assert s.verdict.evidence_keys == ["k:p1", "k:p2"]
    assert len(s.hotspots) == 6
    assert s.markdown_report == "m"


def test_normalize_synthesis_downgrades_gap_on_fallback_retrieval():
    """铁律护栏：回退检索（查全率不可信）下不允许声称「空白」，降级为 unclear。"""
    subset = [{**_paper(f"p{i}", 2020, 10), "_evidence_no": f"E{i}"} for i in range(1, 4)]
    data = {"verdict": {"status": "gap", "confidence": 0.9, "explanation": "材料中未见直接实现。",
                        "evidence": []}}
    s = _normalize_synthesis(data, subset, retrieval_fallback=True)
    assert s.verdict.status == "unclear"
    assert s.verdict.confidence <= 0.5
    assert "查全率存疑" in s.verdict.explanation
    # 正常拆解检索下的 gap 判定不受影响
    s2 = _normalize_synthesis(data, subset, retrieval_fallback=False)
    assert s2.verdict.status == "gap"


# ── 7) run_exploration 端到端（stub provider + 无 Key）───────────────────────

class _StubProvider:
    name = "stub"

    async def search_works(self, query, limit=20, **kwargs):
        if "tumor" in query:
            return [
                _cand(f"tumor detection {w}", year=2020 + i, cites=100 - i * 10,
                      abstract=f"deep learning tumor detection {w} cancer", key=f"k:td{w}")
                for i, w in enumerate(["cnn", "transformer", "unet", "attention", "gan"])
            ]
        if "language" in query:
            return [
                _cand(f"language model {w}", year=2019 + i, cites=90 - i * 10,
                      abstract=f"pretraining language model {w} text", key=f"k:lm{w}")
                for i, w in enumerate(["bert", "gpt", "t5", "roberta"])
            ]
        return []

    async def aclose(self):
        pass


@pytest.fixture()
def exploration_env(tmp_path, monkeypatch):
    """隔离环境：独立 SQLite + stub provider + 无 LLM Key。返回 (engine, events)。"""
    from sqlmodel import SQLModel, create_engine

    engine = create_engine(f"sqlite:///{tmp_path}/exploration.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(explorer, "get_engine", lambda: engine)

    monkeypatch.setattr(
        "backend.services.deep_research.providers.get_provider", lambda cfg: _StubProvider(),
    )
    monkeypatch.setattr(
        "backend.services.ai_config.get_analysis_config", lambda: {"api_key": "", "base_url": "", "model": ""},
    )

    # stage 0 数据源预检：测试环境不触网，恒返回健康的 stub 源
    async def _healthy_sources(*args, **kwargs):
        return [{"name": "openalex", "role": "primary", "status": "ok",
                 "detail": "测试环境", "latency_ms": 1, "http_status": 200}]

    monkeypatch.setattr(
        "backend.services.deep_research.preflight.preflight_research_sources", _healthy_sources,
    )

    events: list[tuple[str, dict]] = []

    class _Bus:
        async def publish(self, evt):
            events.append((evt.event, evt.data))

        async def close(self):
            pass

    monkeypatch.setattr(explorer, "get_exploration_bus", lambda rid: _Bus())
    return engine, events


def _make_run(engine):
    from backend.models.exploration import ExplorationRun
    from sqlmodel import Session
    with Session(engine) as s:
        run = ExplorationRun(question="q", depth="standard")
        s.add(run)
        s.commit()
        s.refresh(run)
        return run.id


def test_run_exploration_end_to_end(exploration_env):
    from backend.models.exploration import ExplorationRun
    from sqlmodel import Session

    engine, events = exploration_env
    with Session(engine) as s:
        run = ExplorationRun(question="用 AI 识别肿瘤有没有人做", depth="standard")
        s.add(run)
        s.commit()
        s.refresh(run)
        run_id = run.id

    asyncio.run(run_exploration(run_id))

    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "done"
        assert run.provider == "stub"
        result = run.result
        assert result is not None

    # 无 Key → 回退拆解（原句直检索）→ stub 对中文问句不命中 → 空产物也要结构完整
    assert set(result.keys()) >= {"decompose", "papers", "metrics", "graph", "provider"}
    assert result["decompose"]["fallback"] is True
    assert result["metrics"]["total"] == len(result["papers"]) == 0
    assert result["graph"]["stats"]["node_count"] == 0
    assert "synthesis" not in result
    assert result["survey_report"]["status"] == "no_results"
    assert "不能证明全领域不存在" in result["survey_report"]["search_summary"]["interpretation"]
    assert result["sources"]["agent_loops"]["report"]["status"] == "completed"

    names = [e for e, _ in events]
    assert names[0] == "stage_start" and names[-1] == "done"
    assert "metrics_ready" in names
    assert "synthesis_ready" not in names
    assert "error" not in names


def test_run_exploration_stops_before_recall_when_configured_llm_cannot_translate_query(
        exploration_env, monkeypatch):
    """有模型配置但只得到中文原句时，不能落成 0 篇的成功运行。"""
    from backend.models.exploration import ExplorationRun
    from sqlmodel import Session

    async def empty_llm(*args, **kwargs):
        from backend.services.ai_analyzer import LLMResponseError
        raise LLMResponseError(
            "LLM_EMPTY_CONTENT", base_url="https://gateway.example/v1", model="test-model",
        )

    monkeypatch.setattr(
        "backend.services.ai_config.get_analysis_config",
        lambda: {"api_key": "test", "base_url": "https://gateway.example/v1", "model": "test-model"},
    )
    monkeypatch.setattr("backend.services.deep_research.domain_synthesizer._llm_json", empty_llm)

    engine, events = exploration_env
    with Session(engine) as s:
        run = ExplorationRun(question="用 AI 识别肿瘤有没有人做", depth="standard")
        s.add(run)
        s.commit()
        s.refresh(run)
        run_id = run.id

    asyncio.run(run_exploration(run_id))

    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "failed"
        assert run.current_stage == "decompose"
        assert "未使用中文原句检索" in (run.error_message or "")
        assert run.result is None
    names = [event for event, _ in events]
    assert "error" in names
    assert "recall_progress" not in names


def test_run_exploration_with_matching_query(exploration_env, monkeypatch):
    """绕过 decompose 的 LLM：直接注入英文检索式，验证数据面全链路。"""
    from sqlmodel import Session

    async def fake_decompose(question, cfg, *, max_queries):
        return {"intent": "novelty", "en_queries": ["tumor detection", "language model"],
                "focus_terms": [], "fallback": False}

    monkeypatch.setattr(explorer, "decompose_question", fake_decompose)

    engine, events = exploration_env
    run_id = _make_run(engine)

    asyncio.run(run_exploration(run_id))

    with Session(engine) as s:
        from backend.models.exploration import ExplorationRun
        run = s.get(ExplorationRun, run_id)
        assert run.status == "done"
        papers = run.result["papers"]
    assert len(papers) == 9  # 5 tumor + 4 language
    titles = " ".join(p["title"] for p in papers)
    assert "tumor" in titles and "language" in titles
    # 两个主题聚成 ≥2 个子方向，形成 related 边
    metrics = None
    for name, data in events:
        if name == "metrics_ready":
            metrics = data["metrics"]
    assert metrics["clusters"] >= 2


def test_recall_recovers_after_primary_breaker(monkeypatch):
    """首选源熔断（无效 Key 401）时召回自动改走备用源，检索式不丢。"""
    from backend.services.deep_research.providers import FailoverProvider

    class _DeadPrimary:
        name = "dead"
        def __init__(self):
            self._disabled = False
            self.calls = 0

        async def search_works(self, query, limit=20, **kw):
            self.calls += 1
            self._disabled = True
            return []

        async def aclose(self):
            pass

    class _Backup:
        name = "backup"
        def __init__(self):
            self.calls = 0

        async def search_works(self, query, limit=20, **kw):
            self.calls += 1
            return [_cand(f"{query} hit", year=2020, cites=1, key=f"k:{query}-hit")]

        async def aclose(self):
            pass

    primary, backup = _DeadPrimary(), _Backup()
    fp = FailoverProvider(primary, backup)
    monkeypatch.setattr(
        "backend.services.deep_research.providers.get_provider", lambda cfg: fp,
    )
    papers, name = asyncio.run(recall_papers(["q1", "q2"], per_query=5, cap=20, now_year=2026))
    assert name == "backup"          # 已切换到备用源（透明度字段如实反映）
    # 两条 relevance + 一条经典补充
    assert len(papers) == 2  # Same-title hits from the classic channel are merged.
    assert backup.calls == 3
