"""
Semantic Scholar Provider — 包装现有 services.semantic_scholar
"""

from typing import Optional

from ...semantic_scholar import find_s2_paper_id, get_paper_references as _s2_refs


class SemanticScholarProvider:
    name = "semantic_scholar"

    def __init__(self, api_key: str = ""):
        # api_key 当前未在底层使用，保留参数以便后续接入 x-api-key header
        self.api_key = api_key

    async def find_paper_id(self, title: str, doi: str = "", arxiv_id: str = "") -> Optional[str]:
        return await find_s2_paper_id(title=title, doi=doi, arxiv_id=arxiv_id)

    async def get_paper_references(self, paper_id: str, limit: int = 50) -> list[dict]:
        raw = await _s2_refs(paper_id, limit=limit)
        # 标准化字段名（s2_id → ext_id）
        out = []
        for r in raw:
            out.append({
                "ext_id": r.get("s2_id") or "",
                "title": r.get("title") or "",
                "year": r.get("year"),
                "citation_count": r.get("citation_count") or 0,
                "doi": r.get("doi"),
                "arxiv_id": r.get("arxiv_id"),
                "pdf_url": r.get("pdf_url"),
            })
        return out
