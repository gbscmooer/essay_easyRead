"""
论文 PDF 解析与多源并发下载 — 与身份登记解耦。

支持：
1. 多源 PDF URL 解析瀑布流（涵盖理、工、农、医、文、经管全学科）
2. 高校校园网（如山东大学 IP）机构订阅直连：
   - Elsevier / ScienceDirect / EV (Engineering Village)
   - SpringerLink / Nature Portfolio
   - Wiley Online Library
   - ACS (American Chemical Society)
   - Taylor & Francis
   - Europe PMC / PubMed Central (生物医学/生命科学)
   - 通用 DOI 机构着陆页透传（doi.org -> citation_pdf_url 自动嗅探）
3. 容错回退机制（若机构直链受阻，自动回退到 Unpaywall / OpenAlex / Europe PMC / CrossRef / 网页嗅探）
4. 集中域名级限速与 429 退避，防止 OpenAlex / arXiv 等服务触发限流
5. 批量并发下载（Bounded Concurrency）
"""

from __future__ import annotations

import asyncio
import logging
import os
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from ..config import settings
from .ai_config import get_research_config
from .paper_identity import (
    PaperWork,
    first_author_match,
    normalize_work,
    paper_to_work,
    title_similarity,
    year_within_tolerance,
)
from .rate_limiter import resilient_get

if TYPE_CHECKING:
    from sqlmodel import Session

logger = logging.getLogger(__name__)


def _get_polite_email() -> str:
    """获取 OpenAlex / Unpaywall / CrossRef 礼貌请求池邮箱。"""
    email = settings.research_email.strip()
    if not email:
        email = (get_research_config().get("email") or "").strip()
    return email or "research-reader@sdu.edu.cn"


def _matches_title_candidate(
    work: PaperWork,
    *,
    title: str | None,
    authors: str | None,
    year: int | None,
) -> bool:
    """标题搜索结果必须同时通过标题、第一作者、年份三项弱匹配。"""
    return (
        title_similarity(work.title, title) >= 0.85
        and first_author_match(work.authors, authors)
        and year_within_tolerance(work.year, year)
    )


# ────────────────────────────────────────────────────────────────
# 跨学科数据库与出版商直接解析源（强身份 DOI 驱动）
# ────────────────────────────────────────────────────────────────

async def _resolve_europe_pmc_by_doi(doi: str) -> Optional[str]:
    """通过 Europe PMC / PubMed Central API 解析生物医学与生命科学全文 PDF。"""
    if not doi:
        return None
    clean_doi = doi.replace("https://doi.org/", "").strip()
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    try:
        resp = await resilient_get(
            url,
            params={"query": f'DOI:"{clean_doi}"', "format": "json", "resultType": "core"},
            timeout=15.0,
        )
        data = resp.json()
        results = data.get("resultList", {}).get("result", [])
        if results:
            for item in results:
                url_list = item.get("fullTextUrlList", {}).get("fullTextUrl", [])
                for u in url_list:
                    if u.get("documentStyle") == "pdf" and u.get("url"):
                        return u["url"]
                    if ".pdf" in u.get("url", "").lower():
                        return u["url"]
    except Exception as e:
        logger.debug("Europe PMC DOI lookup failed for %r: %s", clean_doi, e)
    return None


async def _resolve_unpaywall_by_doi(doi: str, email: str = "") -> Optional[str]:
    """通过 Unpaywall API 解析合法 Open Access PDF URL（全球最大的开放获取数据库）。"""
    if not doi:
        return None
    clean_doi = doi.replace("https://doi.org/", "").strip()
    clean_email = email or _get_polite_email()
    url = f"https://api.unpaywall.org/v2/{urllib.parse.quote(clean_doi, safe='')}"
    try:
        resp = await resilient_get(url, params={"email": clean_email}, timeout=15.0)
        data = resp.json()
        best_oa = data.get("best_oa_location") or {}
        if best_oa.get("url_for_pdf"):
            return best_oa["url_for_pdf"]
        if best_oa.get("url"):
            return best_oa["url"]
        for loc in data.get("oa_locations") or []:
            if loc.get("url_for_pdf"):
                return loc["url_for_pdf"]
    except Exception as e:
        logger.debug("Unpaywall DOI lookup failed for %r: %s", clean_doi, e)
    return None


async def _resolve_openalex_by_doi(doi: str, email: str = "") -> Optional[str]:
    """通过全局共享的 OpenAlexProvider 查询 DOI 对应的 PDF URL。"""
    if not doi:
        return None
    try:
        from .deep_research.providers.openalex import get_shared_openalex_provider
        oa = get_shared_openalex_provider(email=email or _get_polite_email())
        return await oa.find_pdf_by_doi(doi)
    except Exception as e:
        logger.debug("OpenAlex DOI lookup failed for %r: %s", doi, e)
    return None


async def _resolve_crossref_by_doi(doi: str) -> Optional[str]:
    """通过 CrossRef API 查询开放获取/出版商 direct PDF。"""
    if not doi:
        return None
    try:
        from .deep_research.crossref_client import get_pdf_url_by_doi
        pdf_url = await get_pdf_url_by_doi(doi)
        if pdf_url and (pdf_url.endswith(".pdf") or "arxiv" in pdf_url or "download" in pdf_url):
            return pdf_url
    except Exception as e:
        logger.debug("CrossRef DOI lookup failed for %r: %s", doi, e)
    return None


# ────────────────────────────────────────────────────────────────
# 高校校园网机构订阅直接映射（Elsevier / Springer / Wiley / ACS / T&F）
# ────────────────────────────────────────────────────────────────

def _is_elsevier_doi(doi: str) -> bool:
    """判断 DOI 是否属于 Elsevier 旗下期刊（如 10.1016/...）。"""
    if not doi:
        return False
    norm = doi.lower().replace("https://doi.org/", "").strip()
    return norm.startswith("10.1016/") or "sciencedirect" in norm


def _get_elsevier_article_url(doi: str) -> str:
    """构造 Elsevier Article Retrieval API URL（校园网 IP 或配置 Key 即可下载）。"""
    clean_doi = doi.replace("https://doi.org/", "").strip()
    return f"https://api.elsevier.com/content/article/doi/{urllib.parse.quote(clean_doi, safe='')}"


def _get_institutional_publisher_pdf_url(doi: str) -> Optional[str]:
    """
    根据主流国际学术出版商的 DOI 前缀，直接构造机构订阅直链。
    在大学校园网（如山东大学校园网）IP 范围内发起请求，可直接下载 PDF。
    """
    if not doi:
        return None
    clean_doi = doi.replace("https://doi.org/", "").strip()
    norm = clean_doi.lower()

    # 1. Elsevier (ScienceDirect / Engineering Village)
    if norm.startswith("10.1016/"):
        return f"https://api.elsevier.com/content/article/doi/{urllib.parse.quote(clean_doi, safe='')}"

    # 2. Springer / Nature Portfolio（化学、物理、材料、医学、环境、综合科学）
    if (
        norm.startswith("10.1007/")
        or norm.startswith("10.1038/")
        or norm.startswith("10.1186/")
        or norm.startswith("10.1140/")
    ):
        return f"https://link.springer.com/content/pdf/{clean_doi}.pdf"

    # 3. Wiley Online Library（化学、材料、生命科学、医学、经管文科）
    if norm.startswith("10.1002/") or norm.startswith("10.1111/"):
        return f"https://onlinelibrary.wiley.com/doi/pdfdirect/{clean_doi}"

    # 4. ACS - American Chemical Society（顶级化学、材料、纳米）
    if norm.startswith("10.1021/"):
        return f"https://pubs.acs.org/doi/pdf/{clean_doi}"

    # 5. Taylor & Francis（工程、社会科学、人文经管）
    if norm.startswith("10.1080/"):
        return f"https://www.tandfonline.com/doi/pdf/{clean_doi}"

    return None


# ────────────────────────────────────────────────────────────────
# 核心解析流程：resolve_candidate_pdf_urls & resolve_pdf_url
# ────────────────────────────────────────────────────────────────

async def resolve_candidate_pdf_urls(work: PaperWork) -> list[str]:
    """
    全学科多源级联解析候选 PDF URL 列表（按优先级排序）：
    1. arXiv ID 直链
    2. 明确的 Candidate URL
    3. 机构订阅直连 / Europe PMC / Unpaywall / OpenAlex / CrossRef / S2 / DOI 着陆页
    4. 弱匹配标题搜索（arXiv / OpenAlex / CrossRef / S2）
    """
    work = normalize_work(work)
    title = (work.title or "").strip()
    doi = (work.doi or "").strip()
    email = _get_polite_email()

    urls: list[str] = []
    seen: set[str] = set()

    def _add_url(u: str | None):
        if u and str(u).startswith(("http://", "https://")) and u not in seen:
            seen.add(u)
            urls.append(u)

    # 1. arXiv ID 直接构造；同时有正式发表版的机构直链时，正式版优先、预印本后备
    if work.arxiv_id:
        from .deep_research.arxiv_client import ARXIV_PDF_BASE
        inst_url = _get_institutional_publisher_pdf_url(doi) if doi else None
        return [u for u in (inst_url, ARXIV_PDF_BASE.format(work.arxiv_id)) if u]

    # 2. Provider 已明确挂到该候选的 PDF URL
    if work.url and str(work.url).startswith(("http://", "https://")):
        return [str(work.url)]

    # 3. DOI 强身份多源解析
    if doi:
        clean_doi = doi.replace("https://doi.org/", "").strip()

        # 3.1 校园网机构订阅直链
        inst_url = _get_institutional_publisher_pdf_url(clean_doi)
        _add_url(inst_url)

        # 3.2 Europe PMC
        epmc_pdf = await _resolve_europe_pmc_by_doi(clean_doi)
        _add_url(epmc_pdf)

        # 3.3 Unpaywall
        oa_pdf = await _resolve_unpaywall_by_doi(clean_doi, email=email)
        _add_url(oa_pdf)

        # 3.4 OpenAlex DOI 直查
        oa_pdf2 = await _resolve_openalex_by_doi(clean_doi, email=email)
        _add_url(oa_pdf2)

        # 3.5 CrossRef 开放获取
        cr_pdf = await _resolve_crossref_by_doi(clean_doi)
        _add_url(cr_pdf)

        # 3.6 Semantic Scholar（仅在前序源未命中候选时作为最后兜底）
        if len(urls) <= 1:
            try:
                from .semantic_scholar import S2_BASE, _rate_limited_get
                data = await _rate_limited_get(
                    f"{S2_BASE}/paper/DOI:{clean_doi}",
                    params={"fields": "openAccessPdf,url"},
                )
                s2_pdf = (data.get("openAccessPdf") or {}).get("url")
                _add_url(s2_pdf)
            except Exception as e:
                logger.debug("S2 DOI lookup failed for %r: %s", clean_doi, e)

        # 3.7 通用 DOI 机构着陆页（自动嗅探 meta citation_pdf_url）
        _add_url(f"https://doi.org/{clean_doi}")

    # 4. 弱匹配标题搜索
    if title and work.authors and work.year is not None:
        # 4.1 arXiv 标题搜索
        try:
            from .deep_research.arxiv_client import search_by_title
            result = await search_by_title(title)
            if (
                result
                and result.get("pdf_url")
                and _matches_title_candidate(
                    work,
                    title=result.get("title"),
                    authors=result.get("authors"),
                    year=result.get("year"),
                )
            ):
                _add_url(result["pdf_url"])
        except Exception as e:
            logger.debug("arXiv search_by_title failed for %r: %s", title, e)

        # 4.2 OpenAlex 标题搜索
        try:
            from .deep_research.arxiv_client import ARXIV_PDF_BASE
            from .deep_research.providers.openalex import (
                _extract_arxiv,
                _extract_pdf,
                get_shared_openalex_provider,
            )

            oa = get_shared_openalex_provider(email=email)
            data = await oa._safe_get(
                "/works",
                {
                    "search": title,
                    "per-page": 3,
                    "select": "id,title,doi,ids,publication_year,authorships,open_access,primary_location,locations",
                },
            )
            for item in (data or {}).get("results") or []:
                candidate_authors = ", ".join(
                    (authorship.get("author") or {}).get("display_name", "")
                    for authorship in (item.get("authorships") or [])
                    if (authorship.get("author") or {}).get("display_name")
                )
                if _matches_title_candidate(
                    work,
                    title=item.get("title"),
                    authors=candidate_authors,
                    year=item.get("publication_year"),
                ):
                    arxiv_id = _extract_arxiv(item)
                    if arxiv_id:
                        _add_url(ARXIV_PDF_BASE.format(arxiv_id))
                    pdf = _extract_pdf(item)
                    if pdf:
                        _add_url(pdf)
        except Exception as e:
            logger.debug("OpenAlex title search failed for %r: %s", title, e)

        # 4.3 CrossRef 标题搜索
        try:
            from .deep_research.crossref_client import search_by_title as cr_search
            cr_result = await cr_search(title)
            if cr_result and cr_result.get("pdf_url") and _matches_title_candidate(
                work,
                title=cr_result.get("title"),
                authors=cr_result.get("authors"),
                year=cr_result.get("year"),
            ):
                _add_url(cr_result["pdf_url"])
        except Exception as e:
            logger.debug("CrossRef title search failed for %r: %s", title, e)

        # 4.4 Semantic Scholar 标题搜索
        try:
            from .semantic_scholar import S2_BASE, _rate_limited_get
            data = await _rate_limited_get(
                f"{S2_BASE}/paper/search",
                params={"query": title, "limit": 3, "fields": "title,authors,year,openAccessPdf"},
            )
            for item in (data or {}).get("data") or []:
                candidate_authors = ", ".join(
                    author.get("name", "")
                    for author in (item.get("authors") or [])
                    if author.get("name")
                )
                if _matches_title_candidate(
                    work,
                    title=item.get("title"),
                    authors=candidate_authors,
                    year=item.get("year"),
                ):
                    oa_pdf = (item.get("openAccessPdf") or {}).get("url")
                    if oa_pdf:
                        _add_url(oa_pdf)
        except Exception as e:
            logger.debug("S2 title search failed for %r: %s", title, e)

    return urls


async def resolve_pdf_url(work: PaperWork) -> str | None:
    """解析首选可下载 PDF URL（兼容旧调用签名）。"""
    urls = await resolve_candidate_pdf_urls(work)
    return urls[0] if urls else None


async def download_for_paper(session: Session, paper_id: int) -> str | None:
    """
    为已有 Paper 尝试多源下载 PDF（具有级联回退容错）：
    pending → available | failed | unavailable
    """
    from ..models.paper import Paper

    paper = session.get(Paper, paper_id)
    if paper is None:
        return None

    if (
        paper.download_status == "available"
        and paper.pdf_path
        and Path(paper.pdf_path).is_file()
    ):
        return paper.pdf_path

    paper.download_status = "pending"
    paper.updated_at = datetime.now(timezone.utc)
    session.add(paper)
    session.commit()

    try:
        work = paper_to_work(paper)
        url = await resolve_pdf_url(work)
        if not url:
            paper.download_status = "unavailable"
            paper.updated_at = datetime.now(timezone.utc)
            session.add(paper)
            session.commit()
            return None

        from .deep_research.pdf_downloader import download_pdf

        async def _safe_download(candidate_url):
            try:
                return await download_pdf(candidate_url)
            except Exception:
                logger.info("PDF candidate could not be downloaded for paper %s", paper_id)
                return None

        local_path = await _safe_download(url)
        if local_path and Path(local_path).is_file():
            paper.pdf_path = local_path
            paper.file_size = os.path.getsize(local_path)
            paper.download_status = "available"
            paper.updated_at = datetime.now(timezone.utc)
            session.add(paper)
            session.commit()
            return local_path

        # 若初选 URL 下载失败且存在 DOI 备选，尝试其他级联源
        if work.doi:
            candidate_urls = await resolve_candidate_pdf_urls(work)
            for cand_url in candidate_urls:
                if cand_url == url:
                    continue
                local_path = await _safe_download(cand_url)
                if local_path and Path(local_path).is_file():
                    paper.pdf_path = local_path
                    paper.file_size = os.path.getsize(local_path)
                    paper.download_status = "available"
                    paper.updated_at = datetime.now(timezone.utc)
                    session.add(paper)
                    session.commit()
                    return local_path

        paper.download_status = "failed"
        paper.updated_at = datetime.now(timezone.utc)
        session.add(paper)
        session.commit()
        return None

    except Exception:
        logger.exception("PDF resolution failed for paper %s", paper_id)
        session.rollback()
        paper = session.get(Paper, paper_id)
        if paper is not None:
            paper.download_status = "failed"
            paper.updated_at = datetime.now(timezone.utc)
            session.add(paper)
            session.commit()
        return None


async def download_papers_batch(
    session_factory: Callable[[], Session],
    paper_ids: list[int],
    max_concurrency: int = 4,
) -> dict[int, str | None]:
    """
    批量多任务并发下载论文 PDF（内置受控并发 Semaphore 与全局域名限速）。
    """
    sem = asyncio.Semaphore(max_concurrency)
    results: dict[int, str | None] = {}

    async def _download_one(pid: int):
        async with sem:
            with session_factory() as session:
                path = await download_for_paper(session, pid)
                results[pid] = path

    await asyncio.gather(*[_download_one(pid) for pid in paper_ids], return_exceptions=True)
    return results
