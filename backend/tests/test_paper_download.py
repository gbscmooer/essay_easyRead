"""paper_download 下载状态测试。"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.models.paper import Paper
from backend.services.paper_download import (
    _matches_title_candidate,
    download_for_paper,
    resolve_pdf_url,
)
from backend.services.paper_identity import PaperWork, register_stub


@pytest.mark.asyncio
async def test_download_failure_keeps_metadata(session):
    work = PaperWork(title="No PDF Paper", doi="10.7777/nopdf", authors="Dave", year=2022)
    paper = register_stub(session, work)
    paper_id = paper.id

    with patch(
        "backend.services.paper_download.resolve_pdf_url",
        new=AsyncMock(return_value=None),
    ):
        result = await download_for_paper(session, paper_id)

    assert result is None
    refreshed = session.get(Paper, paper_id)
    assert refreshed is not None
    assert refreshed.title == "No PDF Paper"
    assert refreshed.doi == "10.7777/nopdf"
    assert refreshed.download_status == "unavailable"
    assert refreshed.pdf_path == ""


@pytest.mark.asyncio
async def test_download_success_updates_status(session, test_upload_dir):
    import os

    work = PaperWork(title="Downloadable", doi="10.8888/dl", authors="Eve", year=2021)
    paper = register_stub(session, work)
    fake_path = os.path.join(test_upload_dir, "downloaded.pdf")
    with open(fake_path, "wb") as f:
        f.write(b"%PDF-1.4 fake")

    with patch(
        "backend.services.paper_download.resolve_pdf_url",
        new=AsyncMock(return_value="http://example.com/paper.pdf"),
    ), patch(
        "backend.services.deep_research.pdf_downloader.download_pdf",
        new=AsyncMock(return_value=fake_path),
    ):
        result = await download_for_paper(session, paper.id)

    assert result == fake_path
    refreshed = session.get(Paper, paper.id)
    assert refreshed.download_status == "available"
    assert refreshed.pdf_path == fake_path


@pytest.mark.asyncio
async def test_download_http_failure_sets_failed(session):
    work = PaperWork(title="Fail DL", arxiv_id="2302.00002")
    paper = register_stub(session, work)

    with patch(
        "backend.services.paper_download.resolve_pdf_url",
        new=AsyncMock(return_value="http://example.com/paper.pdf"),
    ), patch(
        "backend.services.deep_research.pdf_downloader.download_pdf",
        new=AsyncMock(return_value=None),
    ):
        result = await download_for_paper(session, paper.id)

    assert result is None
    refreshed = session.get(Paper, paper.id)
    assert refreshed.download_status == "failed"
    assert refreshed.title == "Fail DL"
    assert session.get(Paper, paper.id) is not None


def test_title_pdf_candidate_requires_title_author_and_year():
    work = PaperWork(
        title="Graph Retrieval for Science",
        authors="Alice Smith, Bob Lee",
        year=2024,
    )
    assert _matches_title_candidate(
        work,
        title="Graph Retrieval for Science!",
        authors="Alice Smith",
        year=2023,
    )
    assert not _matches_title_candidate(
        work,
        title="Graph Retrieval for Science",
        authors="Mallory",
        year=2024,
    )
    assert not _matches_title_candidate(
        work,
        title="Graph Retrieval for Science",
        authors="Alice Smith",
        year=2020,
    )


@pytest.mark.asyncio
async def test_resolve_pdf_url_uses_candidate_pdf_url_without_title_search():
    work = PaperWork(
        title="Provider Candidate",
        doi="10.1234/provider",
        url="https://example.org/provider.pdf",
    )
    assert await resolve_pdf_url(work) == "https://example.org/provider.pdf"


@pytest.mark.asyncio
async def test_resolve_unpaywall_by_doi():
    from backend.services.paper_download import _resolve_unpaywall_by_doi
    from unittest.mock import MagicMock
    import httpx

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.json = MagicMock(return_value={
        "best_oa_location": {
            "url_for_pdf": "https://unpaywall.org/pdf/10.1234/oa.pdf",
        }
    })

    with patch("backend.services.paper_download.resilient_get", new=AsyncMock(return_value=mock_resp)):
        pdf_url = await _resolve_unpaywall_by_doi("10.1234/oa")
        assert pdf_url == "https://unpaywall.org/pdf/10.1234/oa.pdf"


@pytest.mark.asyncio
async def test_resolve_elsevier_doi():
    from backend.services.paper_download import _is_elsevier_doi, _get_elsevier_article_url
    assert _is_elsevier_doi("10.1016/j.neucom.2023.01.001")
    assert not _is_elsevier_doi("10.1109/CVPR.2023.001")
    assert _get_elsevier_article_url("10.1016/j.artint.2022.01") == "https://api.elsevier.com/content/article/doi/10.1016%2Fj.artint.2022.01"


def test_institutional_publisher_pdf_url_mapping():
    from backend.services.paper_download import _get_institutional_publisher_pdf_url
    # Springer / Nature
    assert _get_institutional_publisher_pdf_url("10.1007/s11263-020-01300-4") == "https://link.springer.com/content/pdf/10.1007/s11263-020-01300-4.pdf"
    assert _get_institutional_publisher_pdf_url("10.1038/s41586-020-2649-2") == "https://link.springer.com/content/pdf/10.1038/s41586-020-2649-2.pdf"
    # Wiley
    assert _get_institutional_publisher_pdf_url("10.1002/anie.202100001") == "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/anie.202100001"
    # ACS
    assert _get_institutional_publisher_pdf_url("10.1021/jacs.0c00001") == "https://pubs.acs.org/doi/pdf/10.1021/jacs.0c00001"
    # Taylor & Francis
    assert _get_institutional_publisher_pdf_url("10.1080/01621459.2020.000000") == "https://www.tandfonline.com/doi/pdf/10.1080/01621459.2020.000000"
    # Elsevier
    assert "api.elsevier.com" in _get_institutional_publisher_pdf_url("10.1016/j.cell.2021.01.001")


@pytest.mark.asyncio
async def test_resolve_europe_pmc_by_doi():
    from backend.services.paper_download import _resolve_europe_pmc_by_doi
    from unittest.mock import MagicMock
    import httpx

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.json = MagicMock(return_value={
        "resultList": {
            "result": [
                {
                    "fullTextUrlList": {
                        "fullTextUrl": [
                            {"documentStyle": "pdf", "url": "https://europepmc.org/articles/PMC12345/pdf"}
                        ]
                    }
                }
            ]
        }
    })

    with patch("backend.services.paper_download.resilient_get", new=AsyncMock(return_value=mock_resp)):
        pdf = await _resolve_europe_pmc_by_doi("10.1038/nature12345")
        assert pdf == "https://europepmc.org/articles/PMC12345/pdf"


def test_extract_pdf_url_from_html():
    from backend.services.deep_research.pdf_downloader import extract_pdf_url_from_html

    html = """
    <html>
      <head>
        <meta name="citation_title" content="A Great Paper">
        <meta name="citation_pdf_url" content="https://example.org/papers/great.pdf">
      </head>
      <body>Paper Content</body>
    </html>
    """
    assert extract_pdf_url_from_html(html, "https://example.org/abs/123") == "https://example.org/papers/great.pdf"

    # 相对路径解析
    rel_html = '<meta name="citation_pdf_url" content="/content/pdf/paper.pdf">'
    assert extract_pdf_url_from_html(rel_html, "https://publisher.com/article/10.1000") == "https://publisher.com/content/pdf/paper.pdf"


@pytest.mark.asyncio
async def test_download_papers_batch(engine, test_upload_dir):
    import os
    from sqlmodel import Session
    from backend.services.paper_download import download_papers_batch

    def session_factory():
        return Session(engine)
    fake_path = os.path.join(test_upload_dir, "batch_sample.pdf")
    with open(fake_path, "wb") as f:
        f.write(b"%PDF-1.4 sample")

    with patch(
        "backend.services.paper_download.resolve_pdf_url",
        new=AsyncMock(return_value="https://example.com/batch.pdf"),
    ), patch(
        "backend.services.deep_research.pdf_downloader.download_pdf",
        new=AsyncMock(return_value=fake_path),
    ):
        with session_factory() as s:
            p1 = register_stub(s, PaperWork(title="P1", arxiv_id="2101.00001"))
            p2 = register_stub(s, PaperWork(title="P2", arxiv_id="2101.00002"))
            pid1, pid2 = p1.id, p2.id

        results = await download_papers_batch(session_factory, [pid1, pid2], max_concurrency=2)
        assert results[pid1] == fake_path
        assert results[pid2] == fake_path

