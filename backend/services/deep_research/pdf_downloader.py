"""
PDF Downloader — 统一多源 PDF 下载器

支持：
1. 经过集中限速器与弹性重试（防 429 / 503 / 504）
2. 自动识别出版商 HTML 着陆页并提取 citation_pdf_url 真实 PDF 链接
3. 魔数校验（%PDF-）与失败文件安全清理
4. download_pdf_detailed 返回逐跳结构化审计（stage/error_code/http_status），
   确定性地址阻断（DNS 阻断、非公网、排除身份）不重试、不发送 HTTP 请求
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Optional

import httpx

from ...config import settings
from ..rate_limiter import resilient_get

logger = logging.getLogger(__name__)

# 学术元数据 PDF 标签模式（Highwire, Google Scholar, Eprints, Bepress 等）
_HTML_PDF_PATTERNS = (
    r'<meta\s+[^>]*?name=[\'"](?:citation_pdf_url|bepress_citation_pdf_url|eprints\.document_url)[\'"][^>]*?content=[\'"]([^\'"]+)[\'"]',
    r'<meta\s+[^>]*?content=[\'"]([^\'"]+)[\'"][^>]*?name=[\'"](?:citation_pdf_url|bepress_citation_pdf_url|eprints\.document_url)[\'"]',
    r'<link\s+[^>]*?rel=[\'"]alternate[\'"][^>]*?type=[\'"]application/pdf[\'"][^>]*?href=[\'"]([^\'"]+)[\'"]',
    r'<link\s+[^>]*?type=[\'"]application/pdf[\'"][^>]*?href=[\'"]([^\'"]+)[\'"]',
    r'<link\s+[^>]*?href=[\'"]([^\'"]+)[\'"][^>]*?type=[\'"]application/pdf[\'"]',
)


def extract_pdf_url_from_html(html_text: str, base_url: str) -> Optional[str]:
    """从出版商/学术机构 HTML 着陆页提取真实 PDF 下载链接。"""
    if not html_text:
        return None
    for pattern in _HTML_PDF_PATTERNS:
        match = re.search(pattern, html_text, re.IGNORECASE)
        if match:
            raw_url = match.group(1).strip()
            if raw_url:
                # 实体转义符清理 &amp; -> &
                clean_url = raw_url.replace("&amp;", "&")
                return urllib.parse.urljoin(base_url, clean_url)
    return None


@dataclass
class DownloadResult:
    """单次下载的结构化结果。

    status: available（path 指向已落盘 PDF）/ unavailable（path 为空）。
    attempts 每跳一条：host/stage/error_code/http_status/retryable/elapsed_ms；
    error_code 取 url_security 与本模块常量，成功跳为 None。
    """

    status: str
    path: Optional[str] = None
    attempts: list[dict] = field(default_factory=list)


def _classify_http_status(status_code: Optional[int]) -> str:
    if status_code in (401, 403):
        return "HTTP_ACCESS_DENIED"
    if status_code in (404, 410):
        return "HTTP_NOT_FOUND"
    if status_code == 429:
        return "HTTP_RATE_LIMITED"
    return "HTTP_ERROR"


def _record_attempt(
    attempts: list[dict],
    host: str,
    stage: str,
    error_code: Optional[str],
    *,
    http_status: Optional[int] = None,
    retryable: bool = False,
    started: Optional[float] = None,
) -> None:
    attempts.append({
        "host": host,
        "stage": stage,
        "error_code": error_code,
        "http_status": http_status,
        "retryable": retryable,
        "elapsed_ms": round((time.monotonic() - started) * 1000) if started is not None else 0,
    })


def _request_headers(url: str, headers: dict | None) -> dict:
    req_headers = {"Accept": "application/pdf, application/octet-stream, text/html;q=0.9, */*;q=0.8"}
    if headers:
        req_headers.update(headers)

    # 针对 Elsevier API 的自动 Header 补齐
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme == "https" and parsed.hostname == "api.elsevier.com":
        req_headers["Accept"] = "application/pdf"
        els_key = settings.elsevier_api_key
        if not els_key:
            from ..ai_config import get_research_config
            els_key = get_research_config().get("elsevier_api_key", "")
        if els_key:
            req_headers["X-ELS-APIKey"] = els_key
    return req_headers


def _same_origin(left: str, right: str) -> bool:
    """Only send caller credentials to the exact origin that received them first."""
    try:
        first = urllib.parse.urlsplit(left)
        second = urllib.parse.urlsplit(right)
        return (first.scheme, first.hostname, first.port) == (
            second.scheme, second.hostname, second.port,
        )
    except ValueError:
        return False


async def download_pdf_detailed(
    url: str,
    timeout: float = 35.0,
    headers: dict | None = None,
    max_redirect_depth: int = 2,
) -> DownloadResult:
    """下载 PDF 并返回逐跳结构化结果。

    与旧 download_pdf 的语义差异：安全校验/DNS 阻断/HTTP 拒绝等不再被笼统吞成
    None，而是携带 error_code 返回；排除身份在首跳仍抛 ValueError（EXCLUDED），
    重定向跳中记 IDENTITY_EXCLUDED 并终止。阻断类失败不重试同一地址。
    """
    result = DownloadResult(status="unavailable")
    if not url or not str(url).startswith(("http://", "https://")):
        _record_attempt(result.attempts, "", "validate", "INVALID_URL")
        return result
    from ..research_exclusions import check_download_url
    check_download_url(url)

    from ..url_security import URLSecurityError, validate_public_http_url

    upload_dir = settings.resolved_upload_dir
    os.makedirs(upload_dir, exist_ok=True)
    fname = f"{uuid.uuid4()}.pdf"
    path = os.path.join(upload_dir, fname)

    current_url = url
    req_headers = _request_headers(url, headers)

    try:
        for depth in range(max_redirect_depth + 1):
            if not _same_origin(url, current_url):
                req_headers = {key: value for key, value in req_headers.items()
                               if key.lower() not in {"authorization", "x-els-apikey", "cookie", "proxy-authorization"}}
            hop_host = urllib.parse.urlparse(current_url).hostname or ""
            started = time.monotonic()
            try:
                check_download_url(current_url)
            except ValueError:
                _record_attempt(result.attempts, hop_host, "validate", "IDENTITY_EXCLUDED",
                                started=started)
                return result
            # 公网校验（含 DNS 全地址检查）：被拦即不发 HTTP 请求，也不重试同一地址
            try:
                await asyncio.to_thread(validate_public_http_url, current_url)
            except URLSecurityError as exc:
                _record_attempt(result.attempts, hop_host, exc.stage, exc.error_code,
                                started=started)
                logger.warning(f"Download blocked pre-HTTP for {current_url}: {exc.error_code} ({exc.detail})")
                return result

            try:
                resp = await resilient_get(
                    current_url,
                    headers=req_headers,
                    timeout=timeout,
                    validate_public_network=True,
                )
            except URLSecurityError as exc:
                # 重定向目标在限速器内被校验拒绝（下一跳请求前拦截）
                _record_attempt(result.attempts, hop_host, exc.stage, exc.error_code,
                                started=started)
                return result
            except ValueError:
                # 其他确定性拒绝（如重定向目标命中排除身份）：不重试
                _record_attempt(result.attempts, hop_host, "validate", "IDENTITY_EXCLUDED",
                                started=started)
                return result
            except httpx.TimeoutException:
                _record_attempt(result.attempts, hop_host, "connect", "NETWORK_TIMEOUT",
                                retryable=True, started=started)
                return result
            except httpx.NetworkError:
                _record_attempt(result.attempts, hop_host, "connect", "NETWORK_ERROR",
                                retryable=True, started=started)
                return result
            except httpx.HTTPStatusError as e:
                status = getattr(e.response, "status_code", None)
                _record_attempt(result.attempts, hop_host, "response",
                                _classify_http_status(status), http_status=status,
                                started=started)
                logger.warning(f"Download HTTP error for {current_url}: {status}")
                return result

            content = resp.content
            ctype = resp.headers.get("content-type", "").lower()
            if content[:5].startswith(b"%PDF"):
                with open(path, "wb") as f:
                    f.write(content)
                _record_attempt(result.attempts, hop_host, "response", None,
                                http_status=resp.status_code, started=started)
                result.status = "available"
                result.path = path
                logger.info(f"Successfully downloaded PDF ({len(content)} bytes) from {current_url}")
                return result

            # 若返回内容是 HTML 页面（着陆页），尝试提取真实 PDF 链接二次下载
            extracted_pdf_url = None
            if depth < max_redirect_depth and ("text/html" in ctype or b"<html" in content[:200].lower() or b"<!doctype html" in content[:200].lower()):
                try:
                    html_text = content.decode("utf-8", errors="ignore")
                except Exception:
                    html_text = ""
                extracted_pdf_url = extract_pdf_url_from_html(html_text, current_url)
            if extracted_pdf_url and extracted_pdf_url != current_url:
                _record_attempt(result.attempts, hop_host, "content", "NON_PDF_CONTENT",
                                http_status=resp.status_code, started=started)
                logger.info(f"Extracted PDF URL from landing page HTML: {extracted_pdf_url}")
                current_url = extracted_pdf_url
                continue

            status = getattr(resp, "status_code", None)
            _record_attempt(result.attempts, hop_host, "content", "NON_PDF_CONTENT",
                            http_status=status, started=started)
            logger.warning(
                f"Rejected non-PDF content from {current_url} (content-type={ctype!r}, magic={content[:10]!r})"
            )
            return result

        return result
    except Exception as e:
        logger.warning(f"Download failed for {url}: {e}")
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
        if not result.attempts:
            _record_attempt(result.attempts,
                            urllib.parse.urlparse(str(url)).hostname or "",
                            "connect", "NETWORK_ERROR", retryable=True)
        return result


async def download_pdf(
    url: str,
    timeout: float = 35.0,
    headers: dict | None = None,
    max_redirect_depth: int = 2,
) -> Optional[str]:
    """下载 PDF 到 uploads/，自动处理 HTML 着陆页跳转与反爬限速，返回本地路径或 None。

    兼容包装器：仅需路径的旧调用方保持不变；需要失败归因的调用方改用
    download_pdf_detailed。
    """
    result = await download_pdf_detailed(url, timeout=timeout, headers=headers,
                                         max_redirect_depth=max_redirect_depth)
    return result.path if result.status == "available" else None
