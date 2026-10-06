"""paper_download 下载状态测试。"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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


def test_elsevier_key_is_only_sent_to_exact_https_api_host(monkeypatch):
    from backend.config import settings
    from backend.services.deep_research.pdf_downloader import _request_headers

    monkeypatch.setattr(settings, "elsevier_api_key", "secret-elsevier-key")
    assert _request_headers("https://api.elsevier.com/content/article", None)["X-ELS-APIKey"] == "secret-elsevier-key"
    for url in (
        "https://api.elsevier.com.attacker.example/paper.pdf",
        "https://attacker.example/api.elsevier.com/paper.pdf",
        "http://api.elsevier.com/content/article",
    ):
        assert "X-ELS-APIKey" not in _request_headers(url, None)


@pytest.mark.asyncio
async def test_elsevier_landing_page_drops_credentials_on_cross_origin_pdf(monkeypatch, test_upload_dir):
    from backend.config import settings
    from backend.services.deep_research.pdf_downloader import download_pdf_detailed

    monkeypatch.setattr(settings, "elsevier_api_key", "secret-elsevier-key")
    monkeypatch.setattr(
        "backend.services.url_security.socket.getaddrinfo",
        _fake_dns({}),
    )
    landing = _pdf_response(
        b'<html><meta name="citation_pdf_url" content="https://publisher.example/paper.pdf"></html>',
        content_type="text/html",
    )
    http_get = AsyncMock(side_effect=[landing, _pdf_response(b"%PDF-1.7 credential-test")])
    monkeypatch.setattr("backend.services.deep_research.pdf_downloader.resilient_get", http_get)

    result = await download_pdf_detailed(
        "https://api.elsevier.com/content/article",
        headers={"Authorization": "Bearer private", "Cookie": "session=private"},
    )
    assert result.status == "available"
    first_headers = http_get.await_args_list[0].kwargs["headers"]
    second_headers = http_get.await_args_list[1].kwargs["headers"]
    assert first_headers["X-ELS-APIKey"] == "secret-elsevier-key"
    assert first_headers["Authorization"] == "Bearer private"
    assert all(key.lower() not in {"x-els-apikey", "authorization", "cookie"}
               for key in second_headers)


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


# ── download_pdf_detailed：结构化失败归因（PDF 下载 DNS 阻断专项） ─────────────

def _pdf_response(content: bytes, content_type: str = "application/pdf", status: int = 200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.headers = {"content-type": content_type}
    resp.content = content
    return resp


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.headers = {}
    request = MagicMock(spec=httpx.Request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=resp)


def _fake_dns(host_responses: dict[str, str]):
    def fake_getaddrinfo(host, *args, **kwargs):
        host = str(host)
        for suffix, address in host_responses.items():
            if host == suffix or host.endswith("." + suffix):
                return [(2, 1, 6, "", (address, 443))]
        return [(2, 1, 6, "", ("93.184.216.34", 443))]
    return fake_getaddrinfo


def _assert_no_new_files(test_upload_dir, before: set):
    """下载目录为 session 共享，断言本调用没有新增（临时）文件。"""
    assert set(os.listdir(test_upload_dir)) == before


@pytest.mark.asyncio
async def test_fake_ip_dns_blocked_before_http(monkeypatch, test_upload_dir):
    """域名解析到 198.18 fake-IP：不发目标 HTTP 请求，结构化返回非公网阻断。"""
    from backend.services.deep_research.pdf_downloader import (
        download_pdf,
        download_pdf_detailed,
    )
    from backend.services.url_security import ERROR_NON_PUBLIC_BLOCKED

    monkeypatch.setattr(
        "backend.services.url_security.socket.getaddrinfo",
        _fake_dns({"arxiv.org": "198.18.0.30"}),
    )
    http_get = AsyncMock(side_effect=AssertionError("被阻断后不得发起 HTTP 请求"))
    monkeypatch.setattr("backend.services.deep_research.pdf_downloader.resilient_get", http_get)
    files_before = set(os.listdir(test_upload_dir))

    result = await download_pdf_detailed("https://arxiv.org/pdf/1706.03762")
    assert result.status == "unavailable"
    assert result.path is None
    assert len(result.attempts) == 1
    attempt = result.attempts[0]
    assert attempt["host"] == "arxiv.org"
    assert attempt["stage"] == "validate"
    assert attempt["error_code"] == ERROR_NON_PUBLIC_BLOCKED
    assert attempt["retryable"] is False
    http_get.assert_not_awaited()
    # 兼容包装器保持 Optional[str] 语义
    assert await download_pdf("https://arxiv.org/pdf/1706.03762") is None
    # 失败不遗留临时文件
    _assert_no_new_files(test_upload_dir, files_before)


@pytest.mark.asyncio
async def test_mixed_dns_answers_block_download(monkeypatch, test_upload_dir):
    """同一域名解析同时含公网和非公网地址：整体拒绝，不挑公网结果放行。"""
    from backend.services.deep_research.pdf_downloader import download_pdf_detailed
    from backend.services.url_security import ERROR_NON_PUBLIC_BLOCKED

    monkeypatch.setattr(
        "backend.services.url_security.socket.getaddrinfo",
        lambda host, *args, **kwargs: [
            (2, 1, 6, "", ("151.101.131.42", 443)),
            (2, 1, 6, "", ("198.18.0.30", 443)),
        ],
    )
    http_get = AsyncMock()
    monkeypatch.setattr("backend.services.deep_research.pdf_downloader.resilient_get", http_get)

    result = await download_pdf_detailed("https://arxiv.org/pdf/1706.03762")
    assert result.attempts[-1]["error_code"] == ERROR_NON_PUBLIC_BLOCKED
    http_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_literal_fake_ip_url_rejected():
    """显式输入的 198.18 字面量 URL 继续拒绝（环境修复不放松安全边界）。"""
    from backend.services.deep_research.pdf_downloader import download_pdf_detailed
    from backend.services.url_security import ERROR_NON_PUBLIC_BLOCKED

    result = await download_pdf_detailed("http://198.18.0.30/paper.pdf")
    assert result.status == "unavailable"
    assert result.attempts[-1]["error_code"] == ERROR_NON_PUBLIC_BLOCKED


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected_code", [
    (403, "HTTP_ACCESS_DENIED"),
    (404, "HTTP_NOT_FOUND"),
    (429, "HTTP_RATE_LIMITED"),
    (500, "HTTP_ERROR"),
])
async def test_http_errors_classified_individually(monkeypatch, test_upload_dir, status, expected_code):
    """403/404/429 等记录真实原因，不统一归为 DNS 错误。"""
    from backend.services.deep_research.pdf_downloader import download_pdf_detailed

    # 本用例只验证 HTTP 分类，不应受宿主 VPN/fake-IP DNS 状态影响；URL 安全
    # 拦截另有独立用例覆盖。固定为真实公网地址后再注入目标 HTTP 错误。
    monkeypatch.setattr(
        "backend.services.url_security.socket.getaddrinfo",
        _fake_dns({"www.mdpi.com": "93.184.216.34"}),
    )
    monkeypatch.setattr(
        "backend.services.deep_research.pdf_downloader.resilient_get",
        AsyncMock(side_effect=_http_status_error(status)),
    )
    files_before = set(os.listdir(test_upload_dir))
    result = await download_pdf_detailed("https://www.mdpi.com/123/pdf")
    assert result.status == "unavailable"
    attempt = result.attempts[-1]
    assert attempt["stage"] == "response"
    assert attempt["error_code"] == expected_code
    assert attempt["http_status"] == status
    _assert_no_new_files(test_upload_dir, files_before)


@pytest.mark.asyncio
async def test_timeout_and_network_error_classified(monkeypatch, test_upload_dir):
    from backend.services.deep_research.pdf_downloader import download_pdf_detailed

    monkeypatch.setattr("backend.services.url_security.socket.getaddrinfo", _fake_dns({}))
    files_before = set(os.listdir(test_upload_dir))
    monkeypatch.setattr(
        "backend.services.deep_research.pdf_downloader.resilient_get",
        AsyncMock(side_effect=httpx.ConnectTimeout("timed out")),
    )
    result = await download_pdf_detailed("https://arxiv.org/pdf/1706.03762")
    assert result.attempts[-1]["error_code"] == "NETWORK_TIMEOUT"
    assert result.attempts[-1]["stage"] == "connect"
    assert result.attempts[-1]["retryable"] is True

    monkeypatch.setattr(
        "backend.services.deep_research.pdf_downloader.resilient_get",
        AsyncMock(side_effect=httpx.ConnectError("refused")),
    )
    result = await download_pdf_detailed("https://arxiv.org/pdf/1706.03762")
    assert result.attempts[-1]["error_code"] == "NETWORK_ERROR"
    _assert_no_new_files(test_upload_dir, files_before)


@pytest.mark.asyncio
async def test_landing_page_extracted_url_validated(monkeypatch, test_upload_dir):
    """HTML 中提取出的 PDF 地址指向非公网目标：下一跳请求前拒绝，不绕过 URL 校验。"""
    from backend.services.deep_research.pdf_downloader import download_pdf_detailed
    from backend.services.url_security import ERROR_NON_PUBLIC_BLOCKED

    landing = _pdf_response(
        b'<html><head><meta name="citation_pdf_url" content="https://cdn.mdpi.com/paper.pdf"></head></html>',
        content_type="text/html",
    )
    http_get = AsyncMock(return_value=landing)
    monkeypatch.setattr("backend.services.deep_research.pdf_downloader.resilient_get", http_get)
    monkeypatch.setattr(
        "backend.services.url_security.socket.getaddrinfo",
        _fake_dns({"cdn.mdpi.com": "198.18.0.31"}),  # 仅提取出的下一跳命中 fake-IP
    )

    result = await download_pdf_detailed("https://www.mdpi.com/article")
    assert [a["error_code"] for a in result.attempts] == [
        "NON_PDF_CONTENT",           # 着陆页本身非 PDF（记录该跳）
        ERROR_NON_PUBLIC_BLOCKED,    # 提取出的下一跳被校验拦下
    ]
    assert result.attempts[-1]["host"] == "cdn.mdpi.com"
    assert http_get.await_count == 1  # 第二跳未发出 HTTP 请求


@pytest.mark.asyncio
async def test_success_records_ok_attempt_and_wrapper_returns_path(monkeypatch, test_upload_dir):
    from backend.services.deep_research.pdf_downloader import (
        download_pdf,
        download_pdf_detailed,
    )

    monkeypatch.setattr("backend.services.url_security.socket.getaddrinfo", _fake_dns({}))
    monkeypatch.setattr(
        "backend.services.deep_research.pdf_downloader.resilient_get",
        AsyncMock(return_value=_pdf_response(b"%PDF-1.7 detailed-test")),
    )
    result = await download_pdf_detailed("https://arxiv.org/pdf/1706.03762")
    assert result.status == "available"
    assert result.path and os.path.isfile(result.path)
    attempt = result.attempts[-1]
    assert attempt["stage"] == "response"
    assert attempt["error_code"] is None
    assert attempt["http_status"] == 200
    # 兼容包装器返回可用物理路径（每次调用独立落盘，uuid 文件名不同）
    wrapper_path = await download_pdf("https://arxiv.org/pdf/1706.03762")
    assert wrapper_path and os.path.isfile(wrapper_path)
    os.remove(result.path)
    os.remove(wrapper_path)
