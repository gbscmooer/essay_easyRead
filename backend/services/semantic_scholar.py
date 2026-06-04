"""
Semantic Scholar API 服务

通过 Semantic Scholar 搜索真实的相关论文，替代 AI 生成的幻觉数据。
免费使用，无需 API key，速率限制约 1 req/sec。

API 文档: https://api.semanticscholar.org/api-docs/
"""

import asyncio
import json
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

S2_BASE = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "title,authors,venue,year,citationCount,externalIds,url,openAccessPdf"

# 速率限制
_last_request_time = 0.0
_rate_lock = asyncio.Lock()

# 共享 httpx 客户端（避免每次请求创建新连接）
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """获取共享的 httpx 异步客户端"""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


async def _rate_limited_get(url: str, params: dict, timeout: float = 15.0) -> dict:
    """带速率限制和重试的 GET 请求"""
    global _last_request_time
    client = _get_http_client()
    response = None

    for attempt in range(3):
        async with _rate_lock:
            now = time.monotonic()
            elapsed = now - _last_request_time
            if elapsed < 1.1:
                await asyncio.sleep(1.1 - elapsed)
            _last_request_time = time.monotonic()

        response = await client.get(url, params=params)
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
        from .ai_analyzer import _create_client

        client = _create_client(api_key, base_url)
        try:
            message = await client.messages.create(
                model=model,
                max_tokens=1024,
                system="你是一个学术论文分析助手。只输出 JSON 数组，不要其他文字。",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
        finally:
            await client.close()

        content = message.content[0].text.strip()
        # 提取可能包裹在 ```json ``` 中的内容
        if "```" in content:
            start = content.find("[")
            end = content.rfind("]") + 1
            if start >= 0 and end > start:
                content = content[start:end]

        descriptions = json.loads(content)
        if isinstance(descriptions, list):
            for i, desc in enumerate(descriptions):
                if i < len(related_papers) and isinstance(desc, str):
                    related_papers[i]["relevance"] = desc

    except Exception as e:
        logger.warning(f"Failed to generate relevance descriptions: {e}")
        # relevance 保持空字符串，不影响使用

    return related_papers
