"""arXiv API 客户端

遵从 arXiv 自动化脚本访问规范：
- 请求间隔 ≥ 3 秒（串行，禁止并发）
- max_results 建议 ≤ 1000；超过需分页（start 参数）
- 携带描述性 User-Agent

文档: https://arxiv.org/help/api/user-manual
"""
import asyncio
import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_PDF_BASE = "https://arxiv.org/pdf/{}.pdf"
USER_AGENT = (
    "EasyEssay/1.4 (academic paper reading assistant; "
    "https://github.com/gbscmooer/essay_easyRead)"
)

_RATE_LIMIT = 3.0  # arXiv ToS: ≥ 3 秒
_last_req_time: float = 0.0
_req_lock: Optional[asyncio.Lock] = None
_client: Optional[httpx.AsyncClient] = None


def _get_lock() -> asyncio.Lock:
    global _req_lock
    if _req_lock is None:
        _req_lock = asyncio.Lock()
    return _req_lock


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=20.0,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
    return _client


async def _arxiv_request(qs: str) -> str:
    """限速 GET，返回响应文本。串行调用，不可并发。"""
    global _last_req_time
    async with _get_lock():
        elapsed = asyncio.get_event_loop().time() - _last_req_time
        if elapsed < _RATE_LIMIT:
            await asyncio.sleep(_RATE_LIMIT - elapsed)
        _last_req_time = asyncio.get_event_loop().time()
    url = f"{ARXIV_API}?{qs}"
    resp = await _get_client().get(url)
    resp.raise_for_status()
    return resp.text


def _parse_entry(xml_text: str) -> Optional[dict]:
    """从 arXiv Atom feed 解析第一条 entry，返回 {arxiv_id, pdf_url, title, year}。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning(f"arXiv XML parse error: {e}")
        return None
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entry = root.find("atom:entry", ns)
    if entry is None:
        return None

    # <id> 形如 http://arxiv.org/abs/2303.08774v2
    id_el = entry.find("atom:id", ns)
    if id_el is None or not id_el.text:
        return None
    m = re.search(r"(\d{4}\.\d{4,5})", id_el.text)
    if not m:
        return None
    arxiv_id = m.group(1)

    # PDF 链接：优先用 feed 中的官方链接
    pdf_url = ARXIV_PDF_BASE.format(arxiv_id)
    for link in entry.findall("atom:link", ns):
        if link.get("title") == "pdf" and link.get("href"):
            pdf_url = link.get("href")
            break

    # 标题
    title_el = entry.find("atom:title", ns)
    title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""

    # 年份
    pub_el = entry.find("atom:published", ns)
    year: Optional[int] = None
    if pub_el is not None and pub_el.text:
        try:
            year = int(pub_el.text[:4])
        except ValueError:
            pass

    return {"arxiv_id": arxiv_id, "pdf_url": pdf_url, "title": title, "year": year}


async def search_by_title(title: str) -> Optional[dict]:
    """通过精确标题短语搜索 arXiv。

    返回 {arxiv_id, pdf_url, title, year} 或 None。
    """
    if not title or not title.strip():
        return None
    # 双引号包裹防止分词
    query = f'ti:"{title.strip()}"'
    qs = urllib.parse.urlencode({"search_query": query, "max_results": "1"})
    try:
        xml_text = await _arxiv_request(qs)
        return _parse_entry(xml_text)
    except Exception as e:
        logger.warning(f"arXiv title search failed ('{title[:50]}'): {e}")
        return None


async def get_metadata(arxiv_id: str) -> Optional[dict]:
    """通过已知 arXiv ID 获取元数据（标题、年份、官方 PDF 链接）。

    返回 {arxiv_id, pdf_url, title, year} 或 None。
    """
    qs = urllib.parse.urlencode({"id_list": arxiv_id, "max_results": "1"})
    try:
        xml_text = await _arxiv_request(qs)
        return _parse_entry(xml_text)
    except Exception as e:
        logger.warning(f"arXiv metadata fetch failed ({arxiv_id}): {e}")
        return None
