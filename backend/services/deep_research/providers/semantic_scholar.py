"""
Semantic Scholar Provider — 包装现有 services.semantic_scholar
"""

import time
from typing import Optional

import httpx

from ....models.recall import RecallCandidate, RecallSource, build_recall_candidate
from ...semantic_scholar import (
    find_s2_paper_id,
    get_paper_citations as _s2_citations,
    get_paper_recommendations as _s2_recommendations,
    get_paper_references as _s2_refs,
    search_works as _s2_search,
)


class SemanticScholarProvider:
    name = "semantic_scholar"

    def __init__(self, api_key: str = ""):
        # api_key 当前未在底层使用，保留参数以便后续接入 x-api-key header
        self.api_key = api_key

    async def find_paper_id(self, title: str, doi: str = "", arxiv_id: str = "") -> Optional[str]:
        return await find_s2_paper_id(title=title, doi=doi, arxiv_id=arxiv_id)

    async def get_paper_references(self, paper_id: str, limit: int = 50) -> list[dict]:
        try:
            raw = await _s2_refs(paper_id, limit=limit)
        except Exception:
            return []
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

    async def get_paper_citations(self, paper_id: str, limit: int = 50) -> list[RecallCandidate]:
        try:
            raw = await _s2_citations(paper_id, limit=limit)
        except Exception:
            return []
        return self._map_raw_list(
            raw,
            source=RecallSource.FORWARD_CIT,
            seed_paper_id=paper_id,
            seed_relation="cited_by",
        )

    async def get_recommendations(self, paper_id: str, limit: int = 50) -> list[RecallCandidate]:
        try:
            raw = await _s2_recommendations(paper_id, limit=limit)
        except Exception:
            return []
        return self._map_raw_list(
            raw,
            source=RecallSource.RECOMMEND,
            seed_paper_id=paper_id,
            seed_relation="recommended",
        )

    async def search_works(self, query: str, limit: int = 20, **kwargs) -> list[RecallCandidate]:
        # kwargs 兼容 FailoverProvider 的透传（如 academic_only；S2 暂不支持类型过滤）
        try:
            raw = await _s2_search(query, limit=limit)
        except Exception:
            return []
        return self._map_raw_list(
            raw,
            source=RecallSource.SEARCH,
            seed_relation="search_match",
        )

    async def health_check(self, *, timeout: float = 10.0) -> dict:
        """权限体检：最小真实 paper/search 查询（公共接口限速严，需区分 429 与断网）。"""
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(
                    "https://api.semanticscholar.org/graph/v1/paper/search",
                    params={"query": "retrieval", "limit": 1, "fields": "paperId"},
                )
        except httpx.HTTPError as e:
            return {"name": self.name, "status": "network_error",
                    "detail": f"连接失败（{type(e).__name__}）",
                    "latency_ms": None, "http_status": None}
        latency = int((time.monotonic() - start) * 1000)
        base = {"name": self.name, "latency_ms": latency, "http_status": r.status_code}
        if r.status_code == 200:
            return {**base, "status": "ok", "detail": "检索服务正常"}
        if r.status_code in (401, 403):
            return {**base, "status": "auth_error", "detail": f"HTTP {r.status_code}：无访问权限"}
        if r.status_code == 429:
            return {**base, "status": "rate_limited",
                    "detail": "HTTP 429：公共接口限速（权限仍在）"}
        return {**base, "status": "error", "detail": f"HTTP {r.status_code}"}

    def _map_raw_list(
        self,
        raw_list: list[dict],
        *,
        source: RecallSource,
        seed_paper_id: str | None = None,
        seed_relation: str | None = None,
    ) -> list[RecallCandidate]:
        out: list[RecallCandidate] = []
        for rank, raw in enumerate(raw_list):
            cand = _s2_raw_to_candidate(
                raw,
                source=source,
                source_rank=rank,
                seed_paper_id=seed_paper_id,
                seed_relation=seed_relation,
            )
            if cand:
                out.append(cand)
        return out


def _s2_raw_to_candidate(
    raw: dict,
    *,
    source: RecallSource,
    source_rank: int,
    seed_paper_id: str | None = None,
    seed_relation: str | None = None,
) -> RecallCandidate | None:
    return build_recall_candidate(
        title=raw.get("title") or "",
        doi=raw.get("doi"),
        arxiv_id=raw.get("arxiv_id"),
        s2_id=raw.get("s2_id"),
        abstract=raw.get("abstract"),
        year=raw.get("year"),
        authors=raw.get("authors"),
        source=source,
        source_rank=source_rank,
        raw_score=float(raw.get("citation_count") or 0),
        seed_paper_id=seed_paper_id,
        seed_relation=seed_relation,
        pdf_url=raw.get("pdf_url"),
        is_open_access=raw.get("is_open_access"),
        citation_count=raw.get("citation_count"),
        provenance=["provider:semantic_scholar", f"method:{source.value}"],
    )
