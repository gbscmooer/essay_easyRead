"""
findpapers 多源学术搜索服务

提供统一的论文搜索和 PDF 下载接口，基于 findpapers 库。
支持 8 个数据库：arXiv、CrossRef、IEEE、OpenAlex、PubMed、Scopus、Semantic Scholar、Web of Science。

当 findpapers 在设置中启用时，替代 Semantic Scholar 进行：
1. 相关论文搜索（多源并行 + 自动去重）
2. PDF URL 解析（三层回退：直接 URL → 着陆页 meta 标签 → 出版商 URL 模式）
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# findpapers 支持的搜索数据库（crossref 仅用于 enrichment，不可搜索）
_VALID_SEARCH_DBS = {"arxiv", "ieee", "openalex", "pubmed", "scopus", "semantic_scholar", "wos"}

# 停用词（构建布尔查询时过滤）
_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "in", "on", "at", "to", "for", "of", "with", "and", "or", "not",
    "it", "this", "that", "from", "by", "as", "its", "we", "our", "via",
    "towards", "toward", "than", "into", "over", "under", "about",
})


def _title_to_query(title: str) -> str:
    """将论文标题转换为 findpapers 布尔查询。

    例：'Last layer retraining is sufficient for robustness to spurious correlations'
    → '[retraining] AND [robustness] AND [spurious correlations]'
    """
    import re
    words = [w for w in re.findall(r"[a-zA-Z]+", title)
             if w.lower() not in _STOP_WORDS and len(w) > 2]
    # 取最多 5 个关键词
    words = words[:5]
    if not words:
        return f"[{title}]"
    return " AND ".join(f"[{w}]" for w in words)


def _get_engine():
    """创建 findpapers Engine 实例。"""
    import findpapers
    return findpapers.Engine()


def _search_sync(title: str, databases: list[str], max_results: int) -> list[dict]:
    """同步搜索（在 asyncio.to_thread 中调用）。

    返回与 RelatedPaper 模型兼容的 dict 列表。
    """
    import findpapers

    engine = _get_engine()
    query = _title_to_query(title)
    valid_dbs = [db for db in databases if db.strip().lower() in _VALID_SEARCH_DBS]
    if not valid_dbs:
        valid_dbs = ["arxiv", "openalex"]

    try:
        result = engine.search(
            query,
            databases=valid_dbs,
            max_papers_per_database=max_results,
            num_workers=min(len(valid_dbs), 4),
            show_progress=False,
        )
    except Exception as e:
        logger.warning(f"findpapers search failed: {e}")
        return []

    papers = []
    for paper in (result.papers if result else []):
        # 跳过与查询完全相同的论文
        if paper.title and paper.title.strip().lower() == title.strip().lower():
            continue

        # 构建 URL：优先 arXiv > DOI > 其他
        url = ""
        if paper.pdf_url and "arxiv.org" in paper.pdf_url:
            url = paper.pdf_url.replace("/pdf/", "/abs/").replace(".pdf", "")
        elif paper.doi:
            url = f"https://doi.org/{paper.doi}"
        elif hasattr(paper, "url") and paper.url:
            url = paper.url

        # 作者列表
        authors_list = paper.authors or []
        # findpapers 返回 Author 对象，需要转 str
        author_names = [str(a) for a in authors_list if a]
        if len(author_names) > 3:
            authors = ", ".join(author_names[:3]) + " et al."
        else:
            authors = ", ".join(author_names) if author_names else ""

        papers.append({
            "title": paper.title or "",
            "authors": authors,
            "venue": getattr(paper, "source", "") or "",
            "year": paper.publication_date.year if getattr(paper, "publication_date", None) else 0,
            "doi": paper.doi or "",
            "url": url,
            "citation_count": (paper.citations if isinstance(getattr(paper, "citations", None), int) else 0),
            "relevance": "",
        })

        if len(papers) >= max_results:
            break

    return papers


def _resolve_pdf_sync(title: str, databases: list[str]) -> Optional[str]:
    """同步解析 PDF URL（在 asyncio.to_thread 中调用）。

    通过 findpapers 搜索论文并获取可下载的 PDF URL。
    利用 findpapers 的三层回退机制：直接 URL → 着陆页 → 出版商模式。
    """
    import findpapers

    engine = _get_engine()
    query = _title_to_query(title)
    valid_dbs = [db for db in databases if db.strip().lower() in _VALID_SEARCH_DBS]
    if not valid_dbs:
        valid_dbs = ["arxiv", "openalex"]

    try:
        result = engine.search(
            query,
            databases=valid_dbs,
            max_papers_per_database=3,
            num_workers=min(len(valid_dbs), 4),
            show_progress=False,
        )
    except Exception as e:
        logger.warning(f"findpapers PDF resolve search failed: {e}")
        return None

    if not result or not result.papers:
        return None

    # 找到标题最匹配的论文
    best = None
    best_score = 0.0
    title_lower = title.strip().lower()
    for paper in result.papers:
        if not paper.title:
            continue
        p_title = paper.title.strip().lower()
        # 简单匹配：前 40 字符相同
        if p_title[:40] == title_lower[:40]:
            # 检查 PDF URL
            if paper.pdf_url:
                return paper.pdf_url
            best = paper
            best_score = 1.0

    # 如果没有精确匹配，用第一个有 PDF URL 的
    if best is None:
        for paper in result.papers:
            if paper.pdf_url:
                return paper.pdf_url

    # 尝试通过 download runner 解析 URL
    if best and best.doi:
        try:
            from findpapers.runners.download_runner import DownloadRunner
            runner = DownloadRunner(engine)
            pdf_url = runner._resolve_url(best)
            if pdf_url:
                return pdf_url
        except Exception as e:
            logger.debug(f"findpapers download runner failed: {e}")

    return None


async def search_related_papers(title: str, config: dict) -> list[dict]:
    """异步搜索相关论文（findpapers 版）。

    Args:
        title: 论文标题
        config: findpapers 配置 dict（enabled, databases, max_results）

    Returns:
        RelatedPaper 兼容的 dict 列表
    """
    databases = config.get("databases", ["arxiv", "crossref", "openalex", "pubmed"])
    max_results = config.get("max_results", 10)

    return await asyncio.to_thread(
        _search_sync, title, databases, max_results
    )


async def resolve_pdf_url(title: str, config: dict) -> Optional[str]:
    """异步解析论文 PDF URL（findpapers 版）。

    Args:
        title: 论文标题
        config: findpapers 配置 dict

    Returns:
        PDF URL 或 None
    """
    databases = config.get("databases", ["arxiv", "crossref", "openalex", "pubmed"])

    return await asyncio.to_thread(
        _resolve_pdf_sync, title, databases
    )
