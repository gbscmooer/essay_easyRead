"""Provider 召回能力单元测试 — 全 mock，零真实网络。"""

from __future__ import annotations

import httpx
import pytest

from backend.models.recall import RecallSource, build_recall_candidate, candidate_from_legacy_reference
from backend.services.deep_research.providers.openalex import OpenAlexProvider, _work_to_candidate
from backend.services.deep_research.providers.semantic_scholar import SemanticScholarProvider
from backend.services.paper_identity import build_canonical_id


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
