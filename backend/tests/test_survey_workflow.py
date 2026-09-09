import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.services.deep_research import arxiv_client, survey_report as sr
from backend.services.rate_limiter import AdaptiveThrottle, DomainRateLimiter
from backend.services.research_exclusions import active_exclusions


@pytest.mark.asyncio
async def test_arxiv_cache_and_atom_material(monkeypatch):
    arxiv_client._req_lock = asyncio.Lock()
    arxiv_client._query_cache.clear()
    xml = '''<feed xmlns="http://www.w3.org/2005/Atom">
    <entry><id>https://arxiv.org/abs/2305.06983v2</id><title>Active retrieval</title>
    <published>2023-05-01</published><summary>Evaluation with a benchmark</summary></entry></feed>'''
    request = AsyncMock(return_value=SimpleNamespace(text=xml))
    monkeypatch.setattr("backend.services.rate_limiter.resilient_get", request)
    a, b = await asyncio.gather(arxiv_client.search_topic("retrieval generation"),
                                arxiv_client.search_topic("retrieval generation"))
    assert a == b and a[0]["abstract"] == "Evaluation with a benchmark"
    assert a[0]["arxiv_id"] == "2305.06983"
    request.assert_awaited_once()
    assert request.call_args.kwargs["params"]["search_query"] == "all:retrieval AND all:generation"


@pytest.mark.asyncio
async def test_scopus_throttle_is_shared_and_cancel_does_not_leak():
    from backend.services.deep_research.providers.scopus import _shared_throttle
    assert _shared_throttle() is _shared_throttle()
    throttle = AdaptiveThrottle(start=0.01, floor=0.01, concurrency=1)
    throttle.on_rate_limited(1)
    task = asyncio.create_task(throttle.acquire())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert throttle._sem._value == 1


@pytest.mark.asyncio
async def test_retry_after_delays_other_waiting_requests():
    throttle = AdaptiveThrottle(start=0.02, floor=0.02, concurrency=2)
    await throttle.acquire()
    throttle.release()
    waiter = asyncio.create_task(throttle.acquire())
    await asyncio.sleep(0.005)
    start = asyncio.get_running_loop().time()
    throttle.on_rate_limited(0.04)
    await waiter
    throttle.release()
    assert asyncio.get_running_loop().time() - start >= 0.035


def test_arxiv_api_and_pdf_share_a_single_budget():
    limiter = DomainRateLimiter()
    assert limiter._get_domain("https://export.arxiv.org/api/query") == "arxiv.org"
    assert limiter._get_rule("arxiv.org") == (3.0, 1)


@pytest.mark.asyncio
async def test_pdf_candidates_fall_through_and_clean_up(monkeypatch, tmp_path):
    async def select(papers, *args, **kwargs):
        return papers, {"warnings": []}
    monkeypatch.setattr(sr, "retrieve_materials", select)
    monkeypatch.setattr(sr, "_llm_json", AsyncMock(return_value={"metrics": []}))
    monkeypatch.setattr("backend.services.paper_download.resolve_candidate_pdf_urls",
                        AsyncMock(return_value=["https://example.org/failed", "https://example.org/good"]))
    path = tmp_path / "source.pdf"
    path.write_bytes(b"%PDF-1.4")
    download = AsyncMock(side_effect=[None, str(path)])
    monkeypatch.setattr("backend.services.deep_research.pdf_downloader.download_pdf", download)
    monkeypatch.setattr("backend.services.pdf_parser.extract_pdf_content",
                        lambda path: SimpleNamespace(full_text="Evaluation of question answering on ExampleQA."))
    paper = {"key": "doi:10.1/example", "title": "Example", "doi": "10.1/example"}
    report = await sr.build_survey_report("q", [paper], {}, None, {"api_key": "test"}, fetch_full_text=True)
    assert download.await_count == 2
    assert report["coverage"]["full_text"] == 1
    assert paper["full_text_status"] == "available"
    assert not path.exists()


@pytest.mark.asyncio
async def test_supplement_is_bounded_and_filters_holdout(monkeypatch):
    from backend.services.deep_research.explorer import recall_papers
    provider = SimpleNamespace(name="scopus", search_works=AsyncMock(return_value=[]), aclose=AsyncMock())
    monkeypatch.setattr("backend.services.deep_research.providers.get_provider", lambda cfg: provider)
    search = AsyncMock(return_value=[{"title": "Hidden survey", "arxiv_id": "2312.10997"},
                                    {"title": "Independent study", "arxiv_id": "2305.06983", "abstract": "material"}])
    monkeypatch.setattr(arxiv_client, "search_topic", search)
    token = active_exclusions.set({"exclusions": {"arxiv_ids": ["2312.10997"]}})
    try:
        audit = {}
        papers, _ = await recall_papers(["query one", "query two", "query two"], per_query=25, cap=100,
                                        now_year=2026, supplement_arxiv=True, search_audit=audit)
        assert search.await_count == 3
        assert provider.search_works.await_count == 3
        assert [p["arxiv_id"] for p in papers] == ["2305.06983"]
        assert audit["excluded"]["count"] == 1
    finally:
        active_exclusions.reset(token)


def test_table_facets_reserve_material_beyond_top_relevance():
    papers = [{"key": str(i), "title": "retrieval generation study", "abstract": "language models"} for i in range(20)]
    papers += [{"key": "eval", "title": "evaluation framework benchmark tool", "abstract": ""},
               {"key": "tasks", "title": "experiment dataset downstream task", "abstract": ""}]
    chosen = sr.select_report_materials(papers, 12)
    assert len(chosen) == 12
    assert {"eval", "tasks"}.issubset({p["key"] for p in chosen})
