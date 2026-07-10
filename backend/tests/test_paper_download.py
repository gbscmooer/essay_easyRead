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
