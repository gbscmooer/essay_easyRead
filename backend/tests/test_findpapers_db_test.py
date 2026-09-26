"""findpapers 数据库「测试」按钮的分档下载验证。

免费库（arXiv 等）：流式校验 PDF 头即可；付费库（IEEE/Scopus/WoS）必须
「真实下载一篇完整论文 → 验证魔数与正文 → 删除临时文件」才算通过——
头部字节检查只能证明可连接，证明不了订阅/IP 权限下的完整下载。
"""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.services.findpapers_service import test_database as run_db_test


def _search_ok(papers):
    return {"ok": True, "papers": papers, "error": ""}


def _patch_search(monkeypatch, papers):
    monkeypatch.setattr(
        "backend.services.findpapers_service._test_search_one_sync",
        lambda db, query, max_n=2: _search_ok(papers),
    )


@pytest.mark.asyncio
async def test_paid_database_real_download_success_then_delete(monkeypatch, tmp_path):
    """Scopus（付费）：完整下载一篇 → 验证 → 删除临时文件，全部成功才算可下载。"""
    pdf_path = tmp_path / "real-test.pdf"
    pdf_path.write_bytes(b"%PDF-1.7 short-paper")

    from backend.services.deep_research.pdf_downloader import DownloadResult

    _patch_search(monkeypatch, [{"title": "A short paper", "pdf_url": "", "doi": "10.1/short"}])
    monkeypatch.setattr(
        "backend.services.paper_download.resolve_candidate_pdf_urls",
        AsyncMock(return_value=["https://publisher.org/short.pdf"]),
    )
    # 付费库不得走头部字节检查路径
    monkeypatch.setattr(
        "backend.services.findpapers_service._verify_pdf_downloadable",
        AsyncMock(side_effect=AssertionError("付费库不应使用头部字节检查")),
    )

    async def fake_download(url, timeout=25.0, headers=None, max_redirect_depth=2):
        return DownloadResult(
            status="available", path=str(pdf_path),
            attempts=[{"host": "publisher.org", "stage": "response", "error_code": None,
                       "http_status": 200, "retryable": False, "elapsed_ms": 12}],
        )

    monkeypatch.setattr(
        "backend.services.deep_research.pdf_downloader.download_pdf_detailed", fake_download)
    monkeypatch.setattr(
        "backend.services.pdf_parser.extract_pdf_content",
        lambda p: SimpleNamespace(full_text="Methods... Results... " * 10),
    )

    out = await run_db_test("scopus")
    assert out["searchable"] is True
    assert out["downloadable"] is True
    assert "已真实下载完整论文" in out["message"]
    assert "删除临时文件" in out["message"]
    assert not pdf_path.exists()  # 临时文件已被删除


@pytest.mark.asyncio
async def test_paid_database_download_failure_keeps_reason(monkeypatch):
    """付费库下载失败：保留结构化原因（如 403 → HTTP_ACCESS_DENIED），不算通过。"""
    from backend.services.deep_research.pdf_downloader import DownloadResult

    _patch_search(monkeypatch, [{"title": "Paywalled", "pdf_url": "https://pub.org/x.pdf", "doi": ""}])

    async def fake_download(url, timeout=25.0, headers=None, max_redirect_depth=2):
        return DownloadResult(
            status="unavailable",
            attempts=[{"host": "pub.org", "stage": "response", "error_code": "HTTP_ACCESS_DENIED",
                       "http_status": 403, "retryable": False, "elapsed_ms": 9}],
        )

    monkeypatch.setattr(
        "backend.services.deep_research.pdf_downloader.download_pdf_detailed", fake_download)

    out = await run_db_test("ieee")
    assert out["searchable"] is True
    assert out["downloadable"] is False
    assert "HTTP_ACCESS_DENIED" in out["message"]


@pytest.mark.asyncio
async def test_paid_database_download_budget_timeout(monkeypatch):
    """完整下载超预算：按「可搜但完整下载失败（超时）」处理，不挂死测试。"""
    import asyncio

    import backend.services.findpapers_service as fps

    _patch_search(monkeypatch, [{"title": "Slow", "pdf_url": "", "doi": "10.1/slow"}])
    monkeypatch.setattr(
        "backend.services.paper_download.resolve_candidate_pdf_urls",
        AsyncMock(return_value=["https://slow.org/x.pdf"]),
    )
    monkeypatch.setattr(fps, "_REAL_DOWNLOAD_BUDGET_S", 0.05)

    async def slow_download(url, timeout=25.0, headers=None, max_redirect_depth=2):
        await asyncio.sleep(0.5)
        from backend.services.deep_research.pdf_downloader import DownloadResult
        return DownloadResult(status="unavailable")

    monkeypatch.setattr(
        "backend.services.deep_research.pdf_downloader.download_pdf_detailed", slow_download)

    out = await run_db_test("wos")
    assert out["searchable"] is True
    assert out["downloadable"] is False
    assert "超时" in out["message"]


@pytest.mark.asyncio
async def test_free_database_keeps_header_check(monkeypatch):
    """免费库（arXiv）：保持头部字节检查路径，不触发完整下载。"""
    _patch_search(monkeypatch, [{"title": "Free", "pdf_url": "https://arxiv.org/pdf/1706.03762", "doi": ""}])
    monkeypatch.setattr(
        "backend.services.findpapers_service._verify_pdf_downloadable",
        AsyncMock(return_value=(True, "已下载并验证 PDF 头")),
    )
    monkeypatch.setattr(
        "backend.services.deep_research.pdf_downloader.download_pdf_detailed",
        AsyncMock(side_effect=AssertionError("免费库不应触发完整下载")),
    )

    out = await run_db_test("arxiv")
    assert out["downloadable"] is True
    assert out["message"] == "可搜索 + 可下载 ✓"
