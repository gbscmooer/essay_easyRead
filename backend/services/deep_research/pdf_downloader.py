"""
PDF Downloader — 统一多源 PDF 下载器

支持：
1. 经过集中限速器与弹性重试（防 429 / 503 / 504）
2. 自动识别出版商 HTML 着陆页并提取 citation_pdf_url 真实 PDF 链接
3. 魔数校验（%PDF-）与失败文件安全清理
"""

from __future__ import annotations

import logging
import os
import re
import urllib.parse
import uuid
from typing import Optional

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


async def download_pdf(
    url: str,
    timeout: float = 35.0,
    headers: dict | None = None,
    max_redirect_depth: int = 2,
) -> Optional[str]:
    """下载 PDF 到 uploads/，自动处理 HTML 着陆页跳转与反爬限速，返回本地路径或 None。"""
    if not url or not str(url).startswith(("http://", "https://")):
        return None

    upload_dir = settings.resolved_upload_dir
    os.makedirs(upload_dir, exist_ok=True)
    fname = f"{uuid.uuid4()}.pdf"
    path = os.path.join(upload_dir, fname)

    current_url = url
    req_headers = {"Accept": "application/pdf, application/octet-stream, text/html;q=0.9, */*;q=0.8"}
    if headers:
        req_headers.update(headers)

    # 针对 Elsevier API 的自动 Header 补齐
    if "api.elsevier.com" in current_url:
        req_headers["Accept"] = "application/pdf"
        els_key = settings.elsevier_api_key
        if not els_key:
            from ..ai_config import get_research_config
            els_key = get_research_config().get("elsevier_api_key", "")
        if els_key:
            req_headers["X-ELS-APIKey"] = els_key

    try:
        for depth in range(max_redirect_depth + 1):
            resp = await resilient_get(current_url, headers=req_headers, timeout=timeout)
            content = resp.content
            ctype = resp.headers.get("content-type", "").lower()
            is_pdf_ctype = ctype.startswith(("application/pdf", "application/octet-stream"))
            is_pdf_magic = content[:5].startswith(b"%PDF")

            if is_pdf_ctype or is_pdf_magic:
                with open(path, "wb") as f:
                    f.write(content)
                logger.info(f"Successfully downloaded PDF ({len(content)} bytes) from {current_url}")
                return path

            # 若返回内容是 HTML 页面（着陆页），尝试提取真实 PDF 链接二次下载
            if depth < max_redirect_depth and ("text/html" in ctype or b"<html" in content[:200].lower() or b"<!doctype html" in content[:200].lower()):
                try:
                    html_text = content.decode("utf-8", errors="ignore")
                except Exception:
                    html_text = ""
                extracted_pdf_url = extract_pdf_url_from_html(html_text, current_url)
                if extracted_pdf_url and extracted_pdf_url != current_url:
                    logger.info(f"Extracted PDF URL from landing page HTML: {extracted_pdf_url}")
                    current_url = extracted_pdf_url
                    continue

            logger.warning(
                f"Rejected non-PDF content from {current_url} (content-type={ctype!r}, magic={content[:10]!r})"
            )
            return None

        return None
    except Exception as e:
        logger.warning(f"Download failed for {url}: {e}")
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
        return None
