"""arXiv API 客户端

遵从 arXiv 自动化脚本访问规范：
- 请求间隔 ≥ 3 秒（串行，禁止并发）
- max_results 建议 ≤ 1000；超过需分页（start 参数）
- 携带描述性 User-Agent

文档: https://arxiv.org/help/api/user-manual
"""
import asyncio
from collections import OrderedDict
import time
import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_PDF_BASE = "https://arxiv.org/pdf/{}.pdf"
USER_AGENT = "AcademicPaperReader/1.0 (personal research use)"

_RATE_LIMIT = 3.0  # arXiv ToS: ≥ 3 秒
_last_req_time: float = 0.0
_req_lock: Optional[asyncio.Lock] = None
_client: Optional[httpx.AsyncClient] = None
_query_cache: OrderedDict[str, tuple[float, str]] = OrderedDict()


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
    async with _get_lock():
        hit = _query_cache.get(qs)
        if hit and time.monotonic() - hit[0] < 600:
            return hit[1]
        from ..rate_limiter import resilient_get
        resp = await resilient_get(ARXIV_API, params=dict(urllib.parse.parse_qsl(qs)),
                                   client=_get_client(), max_retries=2, timeout=20)
        _query_cache[qs] = (time.monotonic(), resp.text)
        _query_cache.move_to_end(qs)
        while len(_query_cache) > 128:
            _query_cache.popitem(last=False)
        return resp.text


async def search_topic(query: str, limit: int = 20) -> list[dict]:
    """Single bounded query returning both abstracts and official full-text links."""
    terms = re.findall(r"[\w-]+", query, re.UNICODE)
    if not terms:
        return []
    qs = urllib.parse.urlencode({"search_query": " AND ".join(f"all:{t}" for t in terms),
                                 "sortBy": "relevance", "sortOrder": "descending",
                                 "max_results": min(max(limit, 1), 40)})
    return _parse_entries(await _arxiv_request(qs), max_entries=min(max(limit, 1), 40))


# arXiv 扩展命名空间，<arxiv:primary_category term="cs.LG"/> 给出主学科分类
_ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _extract_category(entry) -> Optional[str]:
    """取 entry 的主学科分类（如 cs.LG）：优先 arxiv:primary_category，回退首个 atom:category。"""
    pc = entry.find("arxiv:primary_category", _ARXIV_NS)
    if pc is not None and pc.get("term"):
        return pc.get("term")
    cat = entry.find("atom:category", _ARXIV_NS)
    if cat is not None and cat.get("term"):
        return cat.get("term")
    return None


def _parse_entry(xml_text: str) -> Optional[dict]:
    """从 arXiv Atom feed 解析第一条 entry，返回 {arxiv_id, pdf_url, title, year, category}。"""
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

    authors = ", ".join(
        name_el.text.strip()
        for author_el in entry.findall("atom:author", ns)
        if (name_el := author_el.find("atom:name", ns)) is not None
        and name_el.text
        and name_el.text.strip()
    )

    return {
        "arxiv_id": arxiv_id,
        "pdf_url": pdf_url,
        "title": title,
        "authors": authors or None,
        "year": year,
        "category": _extract_category(entry),
        "doi": entry.findtext("arxiv:doi", default=None, namespaces=_ARXIV_NS),
        "venue": entry.findtext("arxiv:journal_ref", default=None, namespaces=_ARXIV_NS),
    }


_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "in", "on", "at", "to", "for", "of", "with", "and", "or", "not",
    "it", "this", "that", "from", "by", "as", "its", "we", "our", "via",
    "towards", "toward", "than", "into", "over", "under", "about",
})


def _normalize_title(t: str) -> str:
    """标题归一化：小写、去连字符、去多余空格。"""
    return re.sub(r"[\s\-–—:]+", " ", t.lower()).strip()


def _extract_keywords(title: str) -> list[str]:
    """从标题中提取关键词用于 arXiv all: 搜索。"""
    words = re.findall(r"[a-zA-Z]+", title)
    return [w for w in words if w.lower() not in _STOP_WORDS and len(w) > 2]


def _parse_entries(xml_text: str, max_entries: int = 5) -> list[dict]:
    """解析多条 arXiv entry，返回 list[{arxiv_id, pdf_url, title, year}]。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    results = []
    for entry in root.findall("atom:entry", ns)[:max_entries]:
        id_el = entry.find("atom:id", ns)
        if id_el is None or not id_el.text:
            continue
        m = re.search(r"(\d{4}\.\d{4,5})", id_el.text)
        if not m:
            continue
        arxiv_id = m.group(1)
        pdf_url = ARXIV_PDF_BASE.format(arxiv_id)
        for link in entry.findall("atom:link", ns):
            if link.get("title") == "pdf" and link.get("href"):
                pdf_url = link.get("href")
                break
        title_el = entry.find("atom:title", ns)
        title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""
        pub_el = entry.find("atom:published", ns)
        year: Optional[int] = None
        if pub_el is not None and pub_el.text:
            try:
                year = int(pub_el.text[:4])
            except ValueError:
                pass
        authors = ", ".join(
            name_el.text.strip()
            for author_el in entry.findall("atom:author", ns)
            if (name_el := author_el.find("atom:name", ns)) is not None
            and name_el.text
            and name_el.text.strip()
        )
        results.append({
            "arxiv_id": arxiv_id,
            "pdf_url": pdf_url,
            "title": title,
            "authors": authors or None,
            "year": year,
            "category": _extract_category(entry),
            "abstract": " ".join(entry.findtext("atom:summary", default="", namespaces=ns).split()),
            "doi": entry.findtext("arxiv:doi", default=None, namespaces=_ARXIV_NS),
        })
    return results


async def search_by_title(title: str) -> Optional[dict]:
    """通过标题搜索 arXiv，返回 {arxiv_id, pdf_url, title, year} 或 None。

    策略：
    1. 精确标题短语搜索 (ti:"...")
    2. 关键词 AND 搜索 (all:kw1 AND all:kw2 ...)  + 模糊标题匹配
    """
    if not title or not title.strip():
        return None

    norm_query = _normalize_title(title)

    # ---- 策略 1：精确标题短语搜索 ----
    try:
        query = f'ti:"{title.strip()}"'
        qs = urllib.parse.urlencode({"search_query": query, "max_results": "3"})
        xml_text = await _arxiv_request(qs)
        entries = _parse_entries(xml_text)
        for entry in entries:
            if _normalize_title(entry["title"]) == norm_query:
                return entry
    except Exception as e:
        logger.debug(f"arXiv exact title search failed ('{title[:50]}'): {e}")

    # ---- 策略 2：关键词 AND 搜索 + 模糊匹配 ----
    try:
        keywords = _extract_keywords(title)
        if len(keywords) < 3:
            return None
        # 取最多 5 个关键词（arXiv 查询不宜太长）
        keywords = keywords[:5]
        query = " AND ".join(f"all:{kw}" for kw in keywords)
        qs = urllib.parse.urlencode({"search_query": query, "max_results": "5"})
        xml_text = await _arxiv_request(qs)
        entries = _parse_entries(xml_text)

        # 找最佳匹配：归一化后的标题应高度相似
        best = None
        best_score = 0.0
        for entry in entries:
            norm_entry = _normalize_title(entry["title"])
            score = _title_similarity(norm_query, norm_entry)
            if score > best_score:
                best_score = score
                best = entry

        if best and best_score >= 0.7:
            return best
    except Exception as e:
        logger.debug(f"arXiv keyword search failed ('{title[:50]}'): {e}")

    return None


def _title_similarity(a: str, b: str) -> float:
    """计算两个归一化标题的相似度 (0~1)，基于词集合 Jaccard。"""
    words_a = set(a.split())
    words_b = set(b.split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


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
