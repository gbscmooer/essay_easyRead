"""
findpapers 多源学术搜索服务

提供统一的论文搜索和 PDF 下载接口，基于 findpapers 库。
支持 8 个数据库：arXiv、CrossRef、IEEE、OpenAlex、PubMed、Scopus、Semantic Scholar、Web of Science。

当 findpapers 在设置中启用时，替代 Semantic Scholar 进行：
1. 相关论文搜索（多源并行 + 自动去重）
2. PDF URL 解析（三层回退：直接 URL → 着陆页 meta 标签 → 出版商 URL 模式）
"""

import asyncio
import importlib.util
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# findpapers 支持的搜索数据库（crossref 仅用于 enrichment，不可搜索）
_VALID_SEARCH_DBS = {"arxiv", "ieee", "openalex", "pubmed", "scopus", "semantic_scholar", "wos"}

# 各数据库的元信息：(展示名, 是否需要 API key)。crossref 仅用于 enrichment，不可单独搜索。
_DB_META: dict[str, tuple[str, bool]] = {
    "arxiv":            ("arXiv",            False),
    "openalex":         ("OpenAlex",         False),
    "pubmed":           ("PubMed",           False),
    "semantic_scholar": ("Semantic Scholar", False),
    "ieee":             ("IEEE Xplore",      True),
    "scopus":           ("Scopus",           True),
    "wos":              ("Web of Science",   True),
    "crossref":         ("CrossRef",         False),
}

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
    import os
    from ..config import settings
    from .ai_config import get_research_config

    key = settings.elsevier_api_key or get_research_config().get("elsevier_api_key", "")
    if key:
        os.environ["FINDPAPERS_SCOPUS_API_TOKEN"] = key
        os.environ["SCOPUS_API_TOKEN"] = key
        os.environ["SCOPUS_API_KEY"] = key

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

        # venue：findpapers 的 paper.source 是 Source 对象，取其 title（刊物名）
        src = getattr(paper, "source", None)
        venue = (getattr(src, "title", "") or "") if src is not None else ""

        papers.append({
            "title": paper.title or "",
            "authors": authors,
            "venue": venue,
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


# ============================================================
# 数据库功能检测（逐库实测：能否成功「搜索」+「下载 PDF」）
# ============================================================
# 不是简单 ping/HTTP 探测，而是真的用 findpapers 搜该库 → 拿一条结果的 PDF →
# 流式下载头部字节确认是真 PDF。这样能区分：库通但搜不到 / 搜得到但下不了 / 全通。

# 功能检测用的通用查询（findpapers 布尔语法，单个常见词足够命中各库）
_TEST_QUERY = "[machine learning]"


def is_findpapers_installed() -> bool:
    """findpapers 库是否已安装（未装则无法搜索/下载）。"""
    return importlib.util.find_spec("findpapers") is not None


def _test_search_one_sync(db: str, query: str, max_n: int = 2) -> dict:
    """同步：用 findpapers 搜单个库，返回 {ok, papers:[{title,pdf_url,doi}], error}。"""
    try:
        engine = _get_engine()
        result = engine.search(
            query, databases=[db], max_papers_per_database=max_n,
            num_workers=1, show_progress=False,
        )
    except Exception as e:
        return {"ok": False, "papers": [], "error": str(e)[:200]}
    papers = []
    for p in (result.papers if result else []):
        papers.append({
            "title": p.title or "",
            "pdf_url": getattr(p, "pdf_url", "") or "",
            "doi": p.doi or "",
        })
    return {"ok": len(papers) > 0, "papers": papers, "error": ""}


async def _verify_pdf_downloadable(client: httpx.AsyncClient, url: str) -> tuple[bool, str]:
    """流式拉取 PDF 头部字节，确认确实是可下载的 PDF（而非着陆页/HTML/404）。"""
    if not url:
        return False, ""
    try:
        async with client.stream("GET", url) as r:
            if r.status_code >= 400:
                return False, f"下载请求 HTTP {r.status_code}"
            ctype = (r.headers.get("content-type") or "").lower()
            head = b""
            async for chunk in r.aiter_bytes():
                head += chunk
                if len(head) >= 2048:
                    break
            if head[:5] == b"%PDF-":
                return True, "已下载并验证 PDF 头"
            if "application/pdf" in ctype:
                return True, "content-type 为 PDF"
            return False, f"返回的不是 PDF（{ctype or '未知类型'}）"
    except httpx.TimeoutException:
        return False, "下载超时"
    except Exception as e:
        return False, f"下载失败（{type(e).__name__}）"


async def test_database(db: str, *, query: str = _TEST_QUERY, timeout: float = 30.0) -> dict:
    """实测单个数据库：能否搜索 + 能否下载到真实 PDF。

    Returns:
        {database, label, needs_key, searchable, downloadable, latency_ms, message}
    """
    key = (db or "").strip().lower()
    meta = _DB_META.get(key)
    label, needs_key = (meta[0], meta[1]) if meta else (key, False)
    out = {
        "database": key, "label": label, "needs_key": needs_key,
        "searchable": False, "downloadable": False, "latency_ms": None, "message": "",
    }
    if meta is None:
        out["message"] = "未知数据库"
        return out
    if not is_findpapers_installed():
        out["message"] = "findpapers 库未安装（pip install findpapers）"
        return out

    t0 = time.monotonic()
    try:
        search = await asyncio.wait_for(
            asyncio.to_thread(_test_search_one_sync, key, query), timeout=timeout
        )
    except asyncio.TimeoutError:
        out["latency_ms"] = int((time.monotonic() - t0) * 1000)
        out["message"] = f"搜索超时（>{int(timeout)}s）"
        return out

    out["searchable"] = search["ok"]
    if not search["ok"]:
        out["latency_ms"] = int((time.monotonic() - t0) * 1000)
        if search["error"]:
            out["message"] = f"搜索失败：{search['error']}"
        elif needs_key:
            out["message"] = "搜索无结果（多半是缺 API key / 未授权）"
        else:
            out["message"] = "搜索无结果（库暂不可用或配额耗尽）"
        return out

    # 取第一条有 pdf_url 的结果验证下载
    pdf_url = next((p["pdf_url"] for p in search["papers"] if p["pdf_url"]), "")
    if not pdf_url:
        out["latency_ms"] = int((time.monotonic() - t0) * 1000)
        out["message"] = "可搜索，但结果未提供可下载 PDF（多为闭源/需订阅）"
        return out

    headers = {"User-Agent": "essay-read/1.0 (findpapers download test)"}
    async with httpx.AsyncClient(follow_redirects=True, timeout=min(timeout, 20.0), headers=headers) as client:
        dl_ok, dl_msg = await _verify_pdf_downloadable(client, pdf_url)
    out["downloadable"] = dl_ok
    out["latency_ms"] = int((time.monotonic() - t0) * 1000)
    out["message"] = "可搜索 + 可下载 ✓" if dl_ok else f"可搜索；下载：{dl_msg}"
    return out


async def test_databases(databases: list[str]) -> list[dict]:
    """并行实测一组数据库（用于「测试全部」）。逐库 searchable / downloadable。

    单库异常被降级为一条 failed 结果，绝不让整批测试 500（return_exceptions=True）。
    """
    keys = list(dict.fromkeys(d.strip().lower() for d in (databases or []) if d and d.strip()))
    if not keys:
        return []
    settled = await asyncio.gather(*[test_database(k) for k in keys], return_exceptions=True)
    out: list[dict] = []
    for k, r in zip(keys, settled):
        if isinstance(r, Exception):
            meta = _DB_META.get(k)
            label, needs_key = (meta[0], meta[1]) if meta else (k, False)
            logger.warning(f"test_database({k}) crashed: {r!r}")
            out.append({
                "database": k, "label": label, "needs_key": needs_key,
                "searchable": False, "downloadable": False, "latency_ms": None,
                "message": f"测试异常：{type(r).__name__}",
            })
        else:
            out.append(r)
    return out
