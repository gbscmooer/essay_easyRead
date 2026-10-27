"""Provider 召回能力单元测试 — 全 mock，零真实网络。"""

from __future__ import annotations

import httpx
import pytest

from backend.models.recall import RecallSource, build_recall_candidate, candidate_from_legacy_reference
from backend.services.deep_research.providers.openalex import OpenAlexProvider, _work_to_candidate
from backend.services.deep_research.providers.semantic_scholar import SemanticScholarProvider
from backend.services.paper_identity import build_canonical_id


async def _async_value(v):
    """把同步值包装成立即可用的协程（stub _safe_get 用）。"""
    return v


# ── fixtures ─────────────────────────────────────────────

SHARED_DOI = "10.48550/arXiv.1706.03762"
SHARED_TITLE = "Attention Is All You Need"

OA_CITATION_WORK = {
    "id": "https://openalex.org/W9990001",
    "title": SHARED_TITLE,
    "publication_year": 2017,
    "cited_by_count": 120000,
    "doi": f"https://doi.org/{SHARED_DOI}",
    "ids": {"openalex": "W9990001"},
    "open_access": {"is_oa": True, "oa_url": "https://arxiv.org/pdf/1706.03762.pdf"},
    "primary_location": {"pdf_url": "https://arxiv.org/pdf/1706.03762.pdf"},
    "locations": [],
    "abstract_inverted_index": {"Attention": [0], "is": [1], "all": [2], "you": [3], "need": [4]},
    "authorships": [
        {"author": {"display_name": "Ashish Vaswani"}},
        {"author": {"display_name": "Noam Shazeer"}},
    ],
}

S2_CITATION_RAW = {
    "s2_id": "abc12345-6789-abcd-ef01-234567890abc",
    "title": SHARED_TITLE,
    "year": 2017,
    "citation_count": 120000,
    "doi": SHARED_DOI,
    "arxiv_id": "1706.03762",
    "pdf_url": "https://arxiv.org/pdf/1706.03762.pdf",
    "is_open_access": True,
    "authors": "Ashish Vaswani, Noam Shazeer et al.",
    "abstract": "The dominant sequence transduction models...",
}


# ── schema / canonical_key ────────────────────────────────


def test_same_doi_across_providers_same_canonical_key():
    oa = _work_to_candidate(
        OA_CITATION_WORK,
        source=RecallSource.FORWARD_CIT,
        source_rank=0,
    )
    s2 = build_recall_candidate(
        title=S2_CITATION_RAW["title"],
        doi=S2_CITATION_RAW["doi"],
        arxiv_id=S2_CITATION_RAW["arxiv_id"],
        s2_id=S2_CITATION_RAW["s2_id"],
        source=RecallSource.FORWARD_CIT,
    )
    assert oa is not None
    assert s2 is not None
    assert oa.canonical_key == s2.canonical_key
    assert oa.canonical_key == build_canonical_id(doi=SHARED_DOI)


def test_candidate_from_legacy_reference_backward_ref():
    raw = {
        "ext_id": "W123",
        "title": "Test Paper",
        "year": 2020,
        "citation_count": 42,
        "doi": "10.1234/test.2020",
        "arxiv_id": None,
        "pdf_url": "https://example.com/paper.pdf",
    }
    cand = candidate_from_legacy_reference(
        raw,
        source=RecallSource.BACKWARD_REF,
        source_rank=1,
        provider_name="openalex",
        seed_paper_id="W999",
        seed_relation="references",
    )
    assert cand is not None
    assert cand.source == RecallSource.BACKWARD_REF
    assert cand.source_rank == 1
    assert cand.seed_paper_id == "W999"
    assert "provider:openalex" in cand.provenance
    assert cand.canonical_key == build_canonical_id(doi="10.1234/test.2020")


# ── OpenAlex provider ─────────────────────────────────────


@pytest.mark.asyncio
async def test_openalex_citations_maps_to_recall_candidate(monkeypatch):
    provider = OpenAlexProvider()

    async def fake_safe_get(path, params=None):
        if path == "/works" and params and params.get("filter", "").startswith("cites:"):
            return {"results": [OA_CITATION_WORK]}
        return None

    monkeypatch.setattr(provider, "_safe_get", fake_safe_get)
    results = await provider.get_paper_citations("W2741809807", limit=5)

    assert len(results) == 1
    cand = results[0]
    assert cand.title == SHARED_TITLE
    assert cand.source == RecallSource.FORWARD_CIT
    assert cand.source_rank == 0
    assert cand.seed_paper_id == "W2741809807"
    assert cand.seed_relation == "cited_by"
    assert cand.doi == SHARED_DOI.lower()
    assert cand.openalex_id == "W9990001"
    assert cand.abstract is not None
    assert "Attention" in cand.abstract
    assert cand.provenance == ["provider:openalex", "method:forward_cit"]


@pytest.mark.asyncio
async def test_openalex_recommendations_from_related_works(monkeypatch):
    provider = OpenAlexProvider()

    async def fake_safe_get(path, params=None):
        if path == "/works/W123" and params and params.get("select") == "related_works":
            return {"related_works": ["https://openalex.org/W9990001"]}
        if path == "/works" and params and "openalex:W9990001" in params.get("filter", ""):
            return {"results": [OA_CITATION_WORK]}
        return None

    monkeypatch.setattr(provider, "_safe_get", fake_safe_get)
    results = await provider.get_recommendations("W123", limit=5)

    assert len(results) == 1
    assert results[0].source == RecallSource.RECOMMEND
    assert results[0].seed_relation == "related"


@pytest.mark.asyncio
async def test_openalex_search_works(monkeypatch):
    provider = OpenAlexProvider()

    async def fake_safe_get(path, params=None):
        if path == "/works" and params and params.get("search"):
            return {"results": [OA_CITATION_WORK]}
        return None

    monkeypatch.setattr(provider, "_safe_get", fake_safe_get)
    results = await provider.search_works("transformer attention", limit=3)

    assert len(results) == 1
    assert results[0].source == RecallSource.SEARCH
    assert results[0].seed_relation == "search_match"


@pytest.mark.asyncio
async def test_openalex_429_returns_empty(monkeypatch):
    provider = OpenAlexProvider()

    async def fake_safe_get(path, params=None):
        raise httpx.HTTPStatusError("429", request=None, response=httpx.Response(429))

    monkeypatch.setattr(provider, "_safe_get", fake_safe_get)
    assert await provider.get_paper_citations("W1") == []
    assert await provider.get_recommendations("W1") == []
    assert await provider.search_works("query") == []


@pytest.mark.asyncio
async def test_openalex_timeout_returns_empty(monkeypatch):
    provider = OpenAlexProvider()

    async def fake_safe_get(path, params=None):
        raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(provider, "_safe_get", fake_safe_get)
    assert await provider.get_paper_citations("W1") == []


# ── Semantic Scholar provider ─────────────────────────────


@pytest.mark.asyncio
async def test_s2_citations_maps_to_recall_candidate(monkeypatch):
    provider = SemanticScholarProvider()

    async def fake_citations(paper_id, limit=50):
        assert paper_id == "seed-s2-id"
        return [S2_CITATION_RAW]

    monkeypatch.setattr(
        "backend.services.deep_research.providers.semantic_scholar._s2_citations",
        fake_citations,
    )
    results = await provider.get_paper_citations("seed-s2-id", limit=5)

    assert len(results) == 1
    cand = results[0]
    assert cand.title == SHARED_TITLE
    assert cand.source == RecallSource.FORWARD_CIT
    assert cand.s2_id == S2_CITATION_RAW["s2_id"]
    assert cand.canonical_key == build_canonical_id(doi=SHARED_DOI)
    assert cand.provenance == ["provider:semantic_scholar", "method:forward_cit"]


@pytest.mark.asyncio
async def test_s2_recommendations(monkeypatch):
    provider = SemanticScholarProvider()

    async def fake_recs(paper_id, limit=50):
        return [S2_CITATION_RAW]

    monkeypatch.setattr(
        "backend.services.deep_research.providers.semantic_scholar._s2_recommendations",
        fake_recs,
    )
    results = await provider.get_recommendations("seed-s2-id")
    assert len(results) == 1
    assert results[0].source == RecallSource.RECOMMEND


@pytest.mark.asyncio
async def test_s2_search_works(monkeypatch):
    provider = SemanticScholarProvider()

    async def fake_search(query, limit=20):
        return [S2_CITATION_RAW]

    monkeypatch.setattr(
        "backend.services.deep_research.providers.semantic_scholar._s2_search",
        fake_search,
    )
    results = await provider.search_works("attention transformer")
    assert len(results) == 1
    assert results[0].source == RecallSource.SEARCH


@pytest.mark.asyncio
async def test_s2_429_returns_empty(monkeypatch):
    provider = SemanticScholarProvider()

    async def fake_citations(paper_id, limit=50):
        return []

    monkeypatch.setattr(
        "backend.services.deep_research.providers.semantic_scholar._s2_citations",
        fake_citations,
    )
    assert await provider.get_paper_citations("x") == []


@pytest.mark.asyncio
async def test_s2_underlying_safe_get_on_429(monkeypatch):
    from backend.services import semantic_scholar as s2_mod

    async def fake_rate_limited_get(url, params, timeout=15.0):
        raise httpx.HTTPStatusError("429", request=None, response=httpx.Response(429))

    monkeypatch.setattr(s2_mod, "_rate_limited_get", fake_rate_limited_get)
    assert await s2_mod.get_paper_references("paper-id") == []
    assert await s2_mod.get_paper_citations("paper-id") == []
    assert await s2_mod.get_paper_recommendations("paper-id") == []
    assert await s2_mod.search_works("query") == []


@pytest.mark.asyncio
async def test_s2_legacy_references_still_dict_format(monkeypatch):
    provider = SemanticScholarProvider()

    async def fake_refs(paper_id, limit=50):
        return [{
            "s2_id": "uuid-test",
            "title": "Legacy Ref",
            "year": 2019,
            "citation_count": 10,
            "doi": "10.5555/legacy",
            "arxiv_id": None,
            "pdf_url": None,
        }]

    monkeypatch.setattr(
        "backend.services.deep_research.providers.semantic_scholar._s2_refs",
        fake_refs,
    )
    refs = await provider.get_paper_references("pid")
    assert isinstance(refs, list)
    assert refs[0]["ext_id"] == "uuid-test"
    assert "title" in refs[0]


# ── Scopus 首选策略与检索质量过滤（GOAL.md：Elsevier 为主检索源）────────────

def test_get_provider_auto_scopus_first_with_key():
    """有 Key → 无条件 Scopus 首选，OpenAlex 做熔断回退。"""
    from backend.services.deep_research.providers import (
        FailoverProvider, OpenAlexProvider, ScopusProvider, get_provider,
    )
    p = get_provider({"provider": "auto", "elsevier_api_key": "k-123"})
    assert isinstance(p, FailoverProvider)
    assert isinstance(p._primary, ScopusProvider)
    assert p._primary.api_key == "k-123"
    assert isinstance(p._secondary, OpenAlexProvider)


def test_get_provider_auto_openalex_without_key(monkeypatch):
    """无 Key → 直接 OpenAlex：Scopus Search 只认 Key（校园机构 IP 不放行），
    无 Key 硬冲只会白付 2×401（2026-09-02 校园出口实测）。"""
    from backend.config import settings as app_settings
    from backend.services.deep_research.providers import (
        OpenAlexProvider, get_provider,
    )
    monkeypatch.setattr(app_settings, "elsevier_api_key", "", raising=False)
    p = get_provider({"provider": "auto"})
    assert isinstance(p, OpenAlexProvider)


def test_scopus_search_query_academic_filter():
    """无引号多词（loose AND，防整串引号变精确短语 0 命中）+ 学术类型过滤。"""
    from backend.services.deep_research.providers.scopus import ScopusProvider
    assert ScopusProvider.build_search_query("tumor detection") == "TITLE-ABS-KEY(tumor detection)"
    q = ScopusProvider.build_search_query("artificial intelligence tumor detection", academic_only=True)
    assert q.startswith("TITLE-ABS-KEY(artificial intelligence tumor detection) AND DOCTYPE(")
    assert '"' not in q
    # 只要 文章/会议/综述/短综述/in-press，排除 letter/note/erratum 噪声
    for dt in ("ar", "cp", "re"):
        assert f" {dt} " in q or f"({dt} " in q


@pytest.mark.asyncio
async def test_scopus_search_works_passes_quality_params(monkeypatch):
    from backend.services.deep_research.providers.scopus import ScopusProvider
    p = ScopusProvider(api_key="")
    captured: dict = {}

    async def fake_get(path, params=None):
        captured["params"] = dict(params or {})
        return {"search-results": {"entry": [
            {"dc:title": "Tumor detection", "citedby-count": "5", "dc:identifier": "SCOPUS_ID:1"},
        ]}}

    monkeypatch.setattr(p, "_get", fake_get)
    out = await p.search_works("tumor detection", limit=5, academic_only=True)
    assert "DOCTYPE" in captured["params"]["query"]
    assert len(out) == 1 and out[0].citation_count == 5


@pytest.mark.asyncio
async def test_scopus_zero_hit_long_query_relaxes_to_or_first_page(monkeypatch):
    from backend.services.deep_research.providers.scopus import ScopusProvider, relaxed_queries
    p = ScopusProvider(api_key="")
    queries: list[tuple[str, int]] = []

    async def fake_get(path, params=None):
        queries.append((params["query"], params["count"]))
        if " OR " not in params["query"]:
            return {"search-results": {"entry": [{"error": "Result set was empty"}]}}
        return {"search-results": {"entry": [
            {"dc:title": "BLE ranging", "citedby-count": "3", "dc:identifier": "SCOPUS_ID:2"}]}}

    monkeypatch.setattr(p, "_get", fake_get)
    out = await p.search_works("BLE RSSI to distance mapping path loss", limit=50)
    assert [c.title for c in out] == ["BLE ranging"]
    assert queries[-1] == ("TITLE-ABS-KEY(BLE OR RSSI OR distance OR mapping OR path OR loss)", 25)
    assert relaxed_queries("RRT narrow") == ["RRT narrow"]  # 短式不放宽


@pytest.mark.asyncio
async def test_openalex_search_works_type_filter(monkeypatch):
    from backend.services.deep_research.providers.openalex import OpenAlexProvider
    p = OpenAlexProvider()
    captured: dict = {}

    async def fake_safe_get(path, params=None):
        captured["params"] = dict(params or {})
        return {"results": [dict(OA_CITATION_WORK)]}

    monkeypatch.setattr(p, "_safe_get", fake_safe_get)
    out = await p.search_works("attention", limit=5, academic_only=True)
    assert "type:article|review|preprint|book-chapter" in captured["params"].get("filter", "")
    assert len(out) == 1
    await p.search_works("attention", limit=5)
    assert "filter" not in captured["params"]


@pytest.mark.asyncio
async def test_failover_search_works_retries_when_primary_dies_mid_call():
    """并发下 primary 在调用中途熔断：该检索式当场改走备用源，不丢结果。"""
    from backend.services.deep_research.providers import FailoverProvider

    class _DeadPrimary:
        name = "dead"
        def __init__(self):
            self._disabled = False
            self.calls = 0

        async def search_works(self, query, limit=20, **kw):
            self.calls += 1
            self._disabled = True  # 典型时序：无效 Key 的 401 在响应处理时置熔断
            return []

    class _Secondary:
        name = "backup"
        def __init__(self):
            self.calls = 0

        async def search_works(self, query, limit=20, **kw):
            self.calls += 1
            return ["hit"]

    primary, secondary = _DeadPrimary(), _Secondary()
    fp = FailoverProvider(primary, secondary)
    hits = await fp.search_works("q", limit=5)
    assert hits == ["hit"]
    assert secondary.calls == 1
    assert fp._active is secondary


def test_scopus_auth_header_name():
    """回归锁：认证头必须是 X-ELS-APIKey——写成 X-APIKey 时有效 Key 也 401
    "Invalid API Key"（2026-09-02 真实踩坑，曾误判为 Key 失效/校园 IP 不放行）。"""
    from backend.services.deep_research.providers.scopus import ScopusProvider

    p = ScopusProvider(api_key="k-123")
    headers = p._http().headers
    assert headers.get("X-ELS-APIKey") == "k-123"
    assert "X-APIKey" not in headers

    p2 = ScopusProvider(api_key="")  # 无 Key 不发认证头
    assert "X-ELS-APIKey" not in p2._http().headers


@pytest.mark.asyncio
async def test_scopus_search_paginates_over_25(monkeypatch):
    """回归锁：Scopus 单页上限 25（count>25 直接 400）——limit=40 必须翻两页补齐。"""
    from backend.services.deep_research.providers.scopus import ScopusProvider
    p = ScopusProvider(api_key="")
    calls: list[dict] = []

    async def fake_get(path, params=None):
        params = dict(params or {})
        calls.append(params)
        start = int(params.get("start", 0))
        count = int(params.get("count", 0))
        n = 25 if start == 0 else 15  # 第二页只剩 15 条 → 触发提前收尾
        return {"search-results": {"entry": [
            {"dc:title": f"paper {start + i}", "citedby-count": "1",
             "dc:identifier": f"SCOPUS_ID:{start + i}"}
            for i in range(n)
        ]}}

    monkeypatch.setattr(p, "_get", fake_get)
    out = await p.search_works("tumor detection", limit=40, academic_only=True)
    assert len(out) == 40
    # 第一页拉满 25，第二页只要剩余的 15；单页 count 永不超过 25
    assert [c["count"] for c in calls] == [25, 15]
    assert [c["start"] for c in calls] == [0, 25]
    assert all(c["count"] <= 25 for c in calls)


def test_scopus_entry_year_prefers_cover_date():
    """回归锁：Scopus Search 只返回 prism:coverDate（publicationDate 请求也不给）。"""
    from backend.services.deep_research.providers.scopus import ScopusProvider
    y = ScopusProvider._entry_year
    assert y({"prism:coverDate": "2024-03-01"}) == 2024
    assert y({"prism:publicationDate": "2021-11-05"}) == 2021  # 旧实现兼容
    assert y({"prism:coverDate": "2027-01-01"}) == 2027        # in-press 占位年份，下游守卫兜
    assert y({}) is None


# ── 指标富化（v1.4）：期刊身份 / SJR 批量 / OpenAlex 逐年引用 ────────────────

@pytest.mark.asyncio
async def test_scopus_search_carries_venue_identity(monkeypatch):
    """检索响应的 source-id/publicationName 免费随行 → candidate.extra 透传给探索侧。"""
    from backend.services.deep_research.providers.scopus import ScopusProvider
    p = ScopusProvider(api_key="")
    seen_fields: list[str] = []

    async def fake_get(path, params=None):
        params = dict(params or {})
        if "field" in params:
            seen_fields.append(params["field"])
        return {"search-results": {"entry": [
            {"dc:title": "Semantic-aware prototype optimization",
             "citedby-count": "3", "dc:identifier": "SCOPUS_ID:105046415198",
             "source-id": "15134", "prism:publicationName": "Information Sciences"},
        ]}}

    monkeypatch.setattr(p, "_get", fake_get)
    out = await p.search_works("test-time adaptation", limit=1)
    assert "source-id" in seen_fields[0] and "prism:publicationName" in seen_fields[0]
    assert out[0].extra["scopus_id"] == "105046415198"
    assert out[0].extra["venue_source_id"] == "15134"
    assert out[0].extra["venue_name"] == "Information Sciences"


@pytest.mark.asyncio
async def test_scopus_serial_metrics_batches_and_parses(monkeypatch):
    """Serial Title 按 source-id 逗号分隔批量（分块 20）；SJR/SNIP 取最新年份。"""
    from backend.services.deep_research.providers.scopus import ScopusProvider
    p = ScopusProvider(api_key="")
    chunks: list[str] = []

    def _entry(sid, name, sjr_pairs):
        return {
            "source-id": str(sid), "dc:title": name,
            "SJRList": {"SJR": [{"@year": str(y), "$": v} for y, v in sjr_pairs]},
            "SNIPList": {"SNIP": [{"@year": "2025", "$": "2.421"}]},
            "citeScoreYearInfoList": {"citeScoreCurrentMetric": "17.0"},
        }

    async def fake_get(path, params=None):
        params = dict(params or {})
        assert path == "/content/serial/title"
        chunk = params["source-id"]
        chunks.append(chunk)
        return {"serial-metadata-response": {"entry": [
            _entry(sid, f"Journal {sid}", [(2023, "1.0"), (2025, "1.939")])
            for sid in chunk.split(",")
        ]}}

    monkeypatch.setattr(p, "_get", fake_get)
    out = await p.get_serial_metrics([str(1000 + i) for i in range(45)])
    assert len(chunks) == 3 and [len(c.split(",")) for c in chunks] == [20, 20, 5]
    assert out["1000"]["sjr"] == 1.939        # 两年都返回时取最新（2025）
    assert out["1000"]["snip"] == 2.421
    assert out["1000"]["citescore"] == 17.0
    assert out["1044"]["name"] == "Journal 1044"


@pytest.mark.asyncio
async def test_openalex_search_carries_citation_dynamics(monkeypatch):
    """检索直接带回 counts_by_year / keywords / 期刊名（免费，零额外调用）。"""
    work = {
        **OA_CITATION_WORK,
        "counts_by_year": [{"year": 2026, "cited_by_count": 13121},
                           {"year": 2025, "cited_by_count": 29403}],
        "keywords": [
            {"display_name": "Transformer", "score": 0.82},
            {"display_name": "x", "score": 0.9},                 # 过短 → 剔除
            {"display_name": "low score topic", "score": 0.2},   # 低分 → 剔除（score 是 0~1 标度）
            {"display_name": "transformer", "score": 0.7},       # 大小写重复 → 去重
            {"display_name": "Computer science", "score": 0.78}, # 学科级泛化词 → 停用
        ],
        "primary_location": {"source": {"display_name": "NeurIPS", "issn": ["", "1234-5678"]}},
    }
    p = OpenAlexProvider()
    monkeypatch.setattr(p, "_safe_get", lambda path, params=None: _async_value({"results": [work]}))
    out = await p.search_works("attention", limit=1)
    assert out[0].extra["counts_by_year"][0] == {"year": 2026, "cited_by_count": 13121}
    assert out[0].extra["keywords"] == ["Transformer"]
    assert out[0].extra["venue_name"] == "NeurIPS"
    assert out[0].extra["venue_issn"] == "1234-5678"


@pytest.mark.asyncio
async def test_openalex_citation_history_by_doi(monkeypatch):
    """get_citation_history：Scopus 论文按 DOI 补逐年引用 + 摘要 + 关键词 + 期刊。"""
    work = {
        "id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/x",
        "cited_by_count": 90,
        "counts_by_year": [{"year": 2026, "cited_by_count": 9}, {"year": 2025, "cited_by_count": 21}],
        "abstract_inverted_index": {"Deep": [0], "residual": [1]},
        "keywords": [{"display_name": "residual learning", "score": 6.0}],
        "primary_location": {"source": {"display_name": "CVPR", "issn": ["1063-6919"]}},
    }
    p = OpenAlexProvider()
    paths: list[str] = []

    async def fake_safe_get(path, params=None):
        paths.append(path)
        return work

    monkeypatch.setattr(p, "_safe_get", fake_safe_get)
    hist = await p.get_citation_history("10.1/x")
    assert paths == ["/works/doi:10.1/x"]
    assert hist["openalex_cited_by"] == 90
    assert hist["counts_by_year"][1] == {"year": 2025, "cited_by_count": 21}
    assert hist["abstract"] == "Deep residual"
    assert hist["keywords"] == ["residual learning"]
    assert hist["venue_name"] == "CVPR"
    # DOI 缺省 / OpenAlex 未收录（404 → {}）→ None，不抛异常
    assert await p.get_citation_history("") is None
    monkeypatch.setattr(p, "_safe_get", lambda path, params=None: _async_value(None))
    assert await p.get_citation_history("10.2/y") is None
