"""
论文 PDF 解析与下载 — 与身份登记解耦。

发现（register stub）与下载分离：下载失败时元数据仍保留。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .paper_identity import (
    PaperWork,
    first_author_match,
    normalize_work,
    paper_to_work,
    title_similarity,
    year_within_tolerance,
)

if TYPE_CHECKING:
    from sqlmodel import Session

logger = logging.getLogger(__name__)


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


async def resolve_pdf_url(work: PaperWork) -> str | None:
    """解析可下载 PDF URL（arXiv 优先，再 OpenAlex / S2 / 标题搜索）。"""
    work = normalize_work(work)
    title = (work.title or "").strip()
    if not title and not work.doi and not work.arxiv_id:
        return None

    # arXiv ID 直接构造
    if work.arxiv_id:
        from .deep_research.arxiv_client import ARXIV_PDF_BASE
        return ARXIV_PDF_BASE.format(work.arxiv_id)

    # Provider 已明确挂到该候选的 PDF URL，无需再次做标题 top-1 搜索。
    if work.url and str(work.url).startswith(("http://", "https://")):
        return str(work.url)

    # DOI 是强身份，优先走 Semantic Scholar 精确查询。
    if work.doi:
        try:
            from .semantic_scholar import _rate_limited_get, S2_BASE
            data = await _rate_limited_get(
                f"{S2_BASE}/paper/DOI:{work.doi}",
                params={"fields": "openAccessPdf,url"},
            )
            oa_pdf = data.get("openAccessPdf") or {}
            if oa_pdf.get("url"):
                return oa_pdf["url"]
        except Exception as e:
            logger.debug("S2 DOI lookup failed for %r: %s", work.doi, e)

    # 以下均为弱匹配路径；缺作者或年份时不自动绑定 PDF。
    if not title or not work.authors or work.year is None:
        return None

    # arXiv 标题搜索
    if title:
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
                return result["pdf_url"]
        except Exception as e:
            logger.debug("arXiv search_by_title failed for %r: %s", title, e)

    # OpenAlex
    if title:
        try:
            from .deep_research.providers.openalex import (
                OpenAlexProvider,
                _extract_arxiv,
                _extract_pdf,
            )
            from .deep_research.arxiv_client import ARXIV_PDF_BASE

            oa = OpenAlexProvider()
            try:
                data = await oa._get(
                    "/works",
                    {
                        "search": title,
                        "per-page": 3,
                        "select": "id,title,doi,ids,publication_year,authorships,open_access,primary_location,locations",
                    },
                )
                for item in data.get("results") or []:
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
                            return ARXIV_PDF_BASE.format(arxiv_id)
                        pdf = _extract_pdf(item)
                        if pdf:
                            return pdf
            finally:
                await oa.aclose()
        except Exception as e:
            logger.debug("OpenAlex search failed for %r: %s", title, e)

    # Semantic Scholar 标题搜索
    try:
        from .semantic_scholar import _rate_limited_get, S2_BASE
    except Exception:
        return None

    if title:
        try:
            data = await _rate_limited_get(
                f"{S2_BASE}/paper/search",
                params={"query": title, "limit": 3, "fields": "title,authors,year,openAccessPdf"},
            )
            for item in data.get("data") or []:
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
                    oa_pdf = item.get("openAccessPdf") or {}
                    if oa_pdf.get("url"):
                        return oa_pdf["url"]
        except Exception as e:
            logger.debug("S2 title search failed for %r: %s", title, e)

    return None


async def download_for_paper(session: Session, paper_id: int) -> str | None:
    """
    为已有 Paper 尝试下载 PDF，更新 download_status：
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

    work = paper_to_work(paper)
    url = await resolve_pdf_url(work)
    if not url:
        paper.download_status = "unavailable"
        paper.updated_at = datetime.now(timezone.utc)
        session.add(paper)
        session.commit()
        return None

    from .deep_research.pdf_downloader import download_pdf

    local_path = await download_pdf(url)
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
