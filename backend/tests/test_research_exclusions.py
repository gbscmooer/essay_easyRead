import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.research_exclusions import active_exclusions, apply_exclusions
from backend.services.rate_limiter import resilient_get
from backend.services.research_exclusions import excluded, normalize_arxiv_id


def test_exclusion_matches_doi_arxiv_version_and_title():
    options = {"exclusions": {"dois": ["10.48550/arxiv.2312.10997"],
                               "arxiv_ids": ["2312.10997"],
                               "titles": ["Retrieval-Augmented Generation for Large Language Models: A Survey"]}}
    papers = [{"title": "RAG Survey", "arxiv_id": "2312.10997v5"},
              {"title": "Retrieval-Augmented Generation for Large Language Models: A Survey", "doi": "10.1/x"},
              {"title": "Keep", "doi": "10.1/keep"}]
    kept, stats = apply_exclusions(papers, options)
    assert [p["title"] for p in kept] == ["Keep"]
    assert stats["count"] == 2


@pytest.mark.parametrize("value", ["https://arxiv.org/pdf/2312.10997v5.pdf?download=1",
                                   "https://export.arxiv.org/abs/2312.10997v2",
                                   "10.48550/arXiv.2312.10997", "arxiv:2312.10997v5"])
def test_holdout_identity_aliases(value):
    assert normalize_arxiv_id(value) == "2312.10997"
    assert excluded({"pdf_url": value}, {"exclusions": {"arxiv_ids": ["2312.10997"]}})


def test_similar_healthcare_title_is_not_target():
    options = {"exclusions": {"titles": ["Retrieval-Augmented Generation for Large Language Models: A Survey"]}}
    assert not excluded({"title": "Retrieval augmented generation for large language models in healthcare: A systematic review"}, options)


@pytest.mark.asyncio
async def test_excluded_pdf_never_reaches_network(monkeypatch):
    from backend.services.deep_research.pdf_downloader import download_pdf
    request = AsyncMock()
    monkeypatch.setattr("backend.services.deep_research.pdf_downloader.resilient_get", request)
    token = active_exclusions.set({"exclusions": {"arxiv_ids": ["2312.10997"]}})
    try:
        with pytest.raises(ValueError, match="EXCLUDED"):
            await download_pdf("https://arxiv.org/pdf/2312.10997v7.pdf")
        request.assert_not_awaited()
    finally:
        active_exclusions.reset(token)


@pytest.mark.asyncio
async def test_report_cannot_extract_excluded_paper(monkeypatch):
    from backend.services.deep_research import survey_report as sr
    llm = AsyncMock()
    monkeypatch.setattr(sr, "_llm_json", llm)
    token = active_exclusions.set({"exclusions": {"arxiv_ids": ["2312.10997"]}})
    try:
        report = await sr.build_survey_report("q", [{"key": "arxiv:2312.10997", "abstract": "hidden"}],
                                              {}, None, {"api_key": "test"}, fetch_full_text=True)
        llm.assert_not_awaited()
        assert report["coverage"]["corpus"] == 0
        assert report["references"] == []
    finally:
        active_exclusions.reset(token)


@pytest.mark.asyncio
async def test_resilient_get_blocks_excluded_redirect(monkeypatch):
    token = active_exclusions.set({"exclusions": {"arxiv_ids": ["2312.10997"]}})
    try:
        response = MagicMock()
        response.status_code = 302
        response.is_redirect = True
        response.headers = {"location": "https://arxiv.org/pdf/2312.10997v5"}
        response.url = "https://example.org/landing"
        client = AsyncMock()
        client.get = AsyncMock(return_value=response)
        with pytest.raises(ValueError, match="EXCLUDED_RESEARCH_PAPER"):
            await resilient_get("https://example.org/landing", client=client, max_retries=1)
    finally:
        active_exclusions.reset(token)
