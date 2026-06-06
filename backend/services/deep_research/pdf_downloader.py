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
            if not r.headers.get("content-type", "").lower().startswith(("application/pdf", "application/octet-stream")):
                logger.warning(f"Non-PDF content from {url}")
            with open(path, "wb") as f:
                f.write(r.content)
        return path
    except Exception as e:
        logger.warning(f"Download failed for {url}: {e}")
        try:
            os.remove(path)
        except OSError:
            pass
        return None
