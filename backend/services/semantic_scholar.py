"""
Semantic Scholar API 服务

通过 Semantic Scholar 搜索真实的相关论文，替代 AI 生成的幻觉数据。
免费使用，无需 API key，速率限制约 1 req/sec。

API 文档: https://api.semanticscholar.org/api-docs/
"""

import asyncio
import logging
import time
from typing import Optional

import httpx

from .paper_identity import normalize_title

logger = logging.getLogger(__name__)

S2_BASE = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "title,authors,venue,year,citationCount,externalIds,url,openAccessPdf"

# 速率限制
_last_request_time = 0.0
_rate_lock = asyncio.Lock()

# 共享 httpx 客户端（避免每次请求创建新连接）
_http_client: httpx.AsyncClient | None = None

# 有 API key 时 S2 给专属 1 RPS；无 key 走共享池，保守用 1.5s
_RATE_WITH_KEY = 1.1    # seconds
_RATE_NO_KEY   = 1.5    # seconds


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


def _s2_api_key() -> str:
    """从 ai_config 读取 Semantic Scholar API key（可能为空）。"""
    try:
        from .ai_config import get_research_config
        return get_research_config().get("api_key", "") or ""
    except Exception as e:
        logger.debug(f"Failed to read S2 API key from config: {e}")
        return ""


async def _rate_limited_get(url: str, params: dict, timeout: float = 15.0) -> dict:
    """带速率限制和重试的 GET 请求。

    有 API key 时通过 x-api-key header 发送，享受专属 1 RPS 配额；
    无 key 时间隔 1.5s，走共享池保守请求。
    """
    global _last_request_time
    client = _get_http_client()
    response = None

    api_key = _s2_api_key()
    min_interval = _RATE_WITH_KEY if api_key else _RATE_NO_KEY
    headers = {"x-api-key": api_key} if api_key else {}

    for attempt in range(3):
        async with _rate_lock:
            now = time.monotonic()
            elapsed = now - _last_request_time
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)
            _last_request_time = time.monotonic()

        response = await client.get(url, params=params, headers=headers)
        if response.status_code == 429:
            wait = 3.0 * (attempt + 1)
            logger.warning(f"Semantic Scholar 429, retry {attempt+1}/3 in {wait}s...")
            await asyncio.sleep(wait)
            continue
        response.raise_for_status()
        return response.json()

    raise httpx.HTTPStatusError("Rate limited after 3 retries", request=None, response=response)


def _map_s2_paper(s2_paper: dict) -> dict:
    """将 S2 API 响应映射为 RelatedPaper 格式"""
    # 作者列表：取前 3 个，超过加 "et al."
    authors_raw = s2_paper.get("authors") or []
    author_names = [a.get("name", "") for a in authors_raw if a.get("name")]
    if len(author_names) > 3:
        authors = ", ".join(author_names[:3]) + " et al."
    else:
        authors = ", ".join(author_names)

    # DOI 和 URL
    external_ids = s2_paper.get("externalIds") or {}
    doi = external_ids.get("DOI") or ""

    paper_url = s2_paper.get("url") or ""
    if not paper_url and doi:
        paper_url = f"https://doi.org/{doi}"

    # 优先用 arXiv URL
    arxiv_id = external_ids.get("ArXiv")
    if arxiv_id and not paper_url:
        paper_url = f"https://arxiv.org/abs/{arxiv_id}"

    return {
        "title": s2_paper.get("title") or "",
        "authors": authors,
        "venue": s2_paper.get("venue") or "",
        "year": s2_paper.get("year") or 0,
        "doi": doi,
        "url": paper_url,
        "citation_count": s2_paper.get("citationCount") or 0,
        "relevance": "",  # 后续由 AI 填充
    }


async def search_related_papers(title: str, limit: int = 10) -> list[dict]:
    """
    通过 Semantic Scholar 搜索与给定标题相关的论文。

    Args:
        title: 论文标题
        limit: 返回结果数量（默认 10）

    Returns:
        RelatedPaper 格式的字典列表
    """
    if not title or not title.strip():
        return []

    try:
        data = await _rate_limited_get(
            f"{S2_BASE}/paper/search",
            params={
                "query": title,
                "fields": S2_FIELDS,
                "limit": limit,
            },
        )

        papers = data.get("data") or []
        if not papers:
            logger.info(f"No Semantic Scholar results for: {title}")
            return []

        results = []
        for p in papers:
            mapped = _map_s2_paper(p)
            # 过滤掉标题为空或与查询完全相同的结果
            if mapped["title"] and mapped["title"].lower() != title.lower():
                results.append(mapped)

        logger.info(f"Found {len(results)} related papers via Semantic Scholar")
        return results[:limit]

    except httpx.HTTPStatusError as e:
        logger.warning(f"Semantic Scholar HTTP error: {e.response.status_code}")
        return []
    except Exception as e:
        logger.warning(f"Semantic Scholar search failed: {e}")
        return []


async def generate_relevance_descriptions(
    paper_title: str,
    paper_one_sentence: str,
    related_papers: list[dict],
    api_key: str,
    base_url: str,
    model: str,
    *,
    provider: str = "",
    api_format: str = "",
    thinking_effort: str = "off",
    timeout_seconds: float = 150.0,
    proxy: dict | None = None,
) -> list[dict]:
    """
    用 AI 为每篇相关论文生成一句关联性说明。

    Args:
        paper_title: 当前论文标题
        paper_one_sentence: 当前论文一句话描述
        related_papers: S2 搜索结果列表
        api_key/base_url/model: AI 模型配置

    Returns:
        填充了 relevance 字段的 related_papers 列表
    """
    if not related_papers or not api_key:
        return related_papers

    # 构建论文列表描述
    papers_desc = "\n".join(
        f'{i+1}. "{p["title"]}" by {p["authors"]} ({p["year"]})'
        for i, p in enumerate(related_papers)
    )

    prompt = (
        f'论文："{paper_title}" — {paper_one_sentence}\n\n'
        f"以下是与该论文相关的论文，请为每篇写一句中文的关联性说明（说明它与当前论文的关系）。\n"
        f"只输出 JSON 数组，不要其他文字。\n\n"
        f"相关论文：\n{papers_desc}\n\n"
        f'期望输出格式：["说明1", "说明2", ...]'
    )

    try:
        from .llm import complete_json

        descriptions = await complete_json(
            {
                "provider": provider,
                "api_format": api_format,
                "api_key": api_key,
                "base_url": base_url,
                "model": model,
                "temperature": 0.3,
                "max_tokens": 1024,
                "thinking_effort": thinking_effort,
                "timeout_seconds": timeout_seconds,
                "proxy": proxy or {},
            },
            system="你是一个学术论文分析助手。只输出 JSON 数组，不要其他文字。",
            user=prompt,
            expected=list,
        )
        if isinstance(descriptions, list):
            for i, desc in enumerate(descriptions):
                if i < len(related_papers) and isinstance(desc, str):
                    related_papers[i]["relevance"] = desc

    except Exception as e:
        logger.warning(f"Failed to generate relevance descriptions: {e}")
        # relevance 保持空字符串，不影响使用

    return related_papers


# ============================================================
# Deep Research 扩展：论文 ID 解析 + 引用列表
# ============================================================

S2_REF_FIELDS = "title,year,citationCount,externalIds,openAccessPdf,authors,abstract"
S2_RECOMMEND_BASE = "https://api.semanticscholar.org/recommendations/v1"


async def _safe_rate_limited_get(url: str, params: dict, timeout: float = 15.0) -> dict | None:
    """带速率限制的 GET；429/超时/网络错误返回 None，不抛异常。"""
    try:
        return await _rate_limited_get(url, params, timeout=timeout)
    except httpx.HTTPStatusError as e:
        logger.warning(f"Semantic Scholar HTTP error: {e.response.status_code if e.response else 'unknown'}")
        return None
    except httpx.TimeoutException:
        logger.warning("Semantic Scholar request timed out")
        return None
    except Exception as e:
        logger.warning(f"Semantic Scholar request failed: {e}")
        return None


def _format_s2_authors(authors_raw: list[dict] | None) -> str | None:
    names = [a.get("name", "") for a in (authors_raw or []) if a.get("name")]
    if not names:
        return None
    if len(names) > 3:
        return ", ".join(names[:3]) + " et al."
    return ", ".join(names)


def _map_s2_recall_paper(s2_paper: dict) -> dict:
    """将 S2 论文对象映射为 recall 构建所需的扁平 dict。"""
    external_ids = s2_paper.get("externalIds") or {}
    oa = s2_paper.get("openAccessPdf") or {}
    return {
        "s2_id": s2_paper.get("paperId"),
        "title": s2_paper.get("title") or "",
        "year": s2_paper.get("year"),
        "citation_count": s2_paper.get("citationCount") or 0,
        "doi": external_ids.get("DOI"),
        "arxiv_id": external_ids.get("ArXiv"),
        "pdf_url": oa.get("url"),
        "is_open_access": bool(oa.get("url")),
        "authors": _format_s2_authors(s2_paper.get("authors")),
        "abstract": s2_paper.get("abstract"),
    }


async def find_s2_paper_id(title: str, doi: str = "", arxiv_id: str = "") -> Optional[str]:
    """通过 DOI/arXivId/标题 解析 S2 paperId"""
    if doi:
        try:
            data = await _rate_limited_get(f"{S2_BASE}/paper/DOI:{doi}", params={"fields": "paperId"})
            return data.get("paperId")
        except Exception as e:
            logger.debug(f"S2 DOI lookup failed for '{doi}': {e}")
    if arxiv_id:
        try:
            data = await _rate_limited_get(f"{S2_BASE}/paper/arXiv:{arxiv_id}", params={"fields": "paperId"})
            return data.get("paperId")
        except Exception as e:
            logger.debug(f"S2 arXiv lookup failed for '{arxiv_id}': {e}")
    if title:
        try:
            data = await _rate_limited_get(f"{S2_BASE}/paper/search",
                params={"query": title, "fields": "paperId,title", "limit": 1})
            arr = data.get("data") or []
            if arr and normalize_title(arr[0].get("title")) == normalize_title(title):
                return arr[0].get("paperId")
        except Exception as e:
            logger.debug(f"S2 title search failed for '{title}': {e}")
    return None


async def get_paper_metadata(s2_paper_id: str) -> dict | None:
    """获取单篇论文的规范元数据（标题修复 / 身份补齐用）。

    s2_paper_id 也接受 ``DOI:...`` / ``arXiv:...`` 形式的外部 ID 路径。
    """
    data = await _safe_rate_limited_get(
        f"{S2_BASE}/paper/{s2_paper_id}",
        params={"fields": "paperId,title,year,venue,externalIds,authors"},
    )
    if not data:
        return None
    ext = data.get("externalIds") or {}
    return {
        "s2_id": data.get("paperId"),
        "title": data.get("title"),
        "year": data.get("year"),
        "venue": data.get("venue"),
        "doi": ext.get("DOI"),
        "arxiv_id": ext.get("ArXiv"),
        "authors": _format_s2_authors(data.get("authors")),
    }


async def get_paper_references(s2_paper_id: str, limit: int = 50) -> list[dict]:
    """返回结构化 references 列表，包含 openAccessPdf 信息"""
    data = await _safe_rate_limited_get(
        f"{S2_BASE}/paper/{s2_paper_id}/references",
        params={"fields": f"citedPaper.{S2_REF_FIELDS}", "limit": limit},
    )
    if not data:
        return []
    results = []
    for item in data.get("data") or []:
        cited = item.get("citedPaper") or {}
        if not cited.get("title"):
            continue
        ext = cited.get("externalIds") or {}
        oa = cited.get("openAccessPdf") or {}
        results.append({
            "s2_id": cited.get("paperId"),
            "title": cited.get("title"),
            "year": cited.get("year"),
            "citation_count": cited.get("citationCount") or 0,
            "doi": ext.get("DOI"),
            "arxiv_id": ext.get("ArXiv"),
            "pdf_url": oa.get("url"),
        })
    return results


async def get_paper_citations(s2_paper_id: str, limit: int = 50) -> list[dict]:
    """返回 citing papers 列表（正向引文）。"""
    data = await _safe_rate_limited_get(
        f"{S2_BASE}/paper/{s2_paper_id}/citations",
        params={"fields": f"citingPaper.{S2_REF_FIELDS}", "limit": limit},
    )
    if not data:
        return []
    results = []
    for item in data.get("data") or []:
        citing = item.get("citingPaper") or {}
        mapped = _map_s2_recall_paper(citing)
        if mapped.get("title"):
            results.append(mapped)
    return results


async def get_paper_recommendations(s2_paper_id: str, limit: int = 50) -> list[dict]:
    """返回 S2 推荐论文列表。"""
    data = await _safe_rate_limited_get(
        f"{S2_RECOMMEND_BASE}/papers/forpaper/{s2_paper_id}",
        params={"fields": S2_REF_FIELDS, "limit": limit},
    )
    if not data:
        return []
    results = []
    for paper in data.get("recommendedPapers") or []:
        mapped = _map_s2_recall_paper(paper)
        if mapped.get("title"):
            results.append(mapped)
    return results


async def search_works(query: str, limit: int = 20) -> list[dict]:
    """关键词检索论文，返回 recall 扁平 dict 列表。"""
    if not query or not query.strip():
        return []
    data = await _safe_rate_limited_get(
        f"{S2_BASE}/paper/search",
        params={"query": query.strip(), "fields": S2_REF_FIELDS, "limit": limit},
    )
    if not data:
        return []
    results = []
    for paper in data.get("data") or []:
        mapped = _map_s2_recall_paper(paper)
        if mapped.get("title"):
            results.append(mapped)
    return results
