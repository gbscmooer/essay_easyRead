"""
PDF Downloader — 从 openAccessPdf URL 下载 PDF

下载到 uploads/ 目录，与上传论文使用同一目录。
"""

import os
import uuid
import httpx
import logging
from typing import Optional
from ...config import settings

logger = logging.getLogger(__name__)


async def download_pdf(url: str, timeout: float = 30.0) -> Optional[str]:
    """下载 PDF 到 uploads/，返回本地路径或 None"""
    upload_dir = settings.resolved_upload_dir
    os.makedirs(upload_dir, exist_ok=True)
    fname = f"{uuid.uuid4()}.pdf"
    path = os.path.join(upload_dir, fname)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            content = r.content
            ctype = r.headers.get("content-type", "").lower()
            is_pdf_ctype = ctype.startswith(("application/pdf", "application/octet-stream"))
            # 兼容 content-type 不规范的服务器：以 %PDF 魔数兜底校验
            is_pdf_magic = content[:5].startswith(b"%PDF")
            if not is_pdf_ctype and not is_pdf_magic:
                # 多为 DOI/出版商 HTML 着陆页，拒绝写盘以免污染论文库
                logger.warning(
                    f"Rejected non-PDF content from {url} (content-type={ctype!r})"
                )
                return None
            with open(path, "wb") as f:
                f.write(content)
        return path
    except Exception as e:
        logger.warning(f"Download failed for {url}: {e}")
        try:
            os.remove(path)
        except OSError:
            pass
        return None
