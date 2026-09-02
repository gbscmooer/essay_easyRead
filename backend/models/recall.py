"""
深度研究召回候选 — 统一 schema

Provider 层将 OpenAlex / S2 等异构响应映射为 RecallCandidate，
canonical_key 遵循 paper_identity.build_canonical_id 规则。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from ..services.paper_identity import (
    build_canonical_id,
    normalize_arxiv_id,
    normalize_doi,
    normalize_openalex_id,
    normalize_s2_id,
)


class RecallSource(str, Enum):
    """候选来源类型。"""

    BACKWARD_REF = "backward_ref"
    FORWARD_CIT = "forward_cit"
    RECOMMEND = "recommend"
    SEARCH = "search"
    RELATED = "related"
    SEED = "seed"


class RecallCandidate(BaseModel):
    """统一召回候选。"""

    canonical_key: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    s2_id: str | None = None

    title: str
    abstract: str | None = None
    year: int | None = None
    authors: str | None = None

    source: RecallSource
    source_rank: int = 0
    raw_score: float | None = None

    seed_paper_id: str | None = None
    seed_relation: str | None = None

    pdf_url: str | None = None
    is_open_access: bool | None = None
    citation_count: int | None = None

    provenance: list[str] = Field(default_factory=list)
    # 指标富化用的元数据透传（venue 身份 / counts_by_year / keywords 等）：
    # 各 provider 把自家特有的统计字段塞这里，探索侧 _dedupe_candidates 展平进论文 dict；
    # 深度研究扩展流水线不读它，保持零影响
    extra: dict[str, Any] = Field(default_factory=dict)


def format_authors(names: list[str], *, max_shown: int = 3) -> str | None:
    """将作者名列表格式化为逗号分隔字符串。"""
    cleaned = [n.strip() for n in names if n and str(n).strip()]
    if not cleaned:
        return None
    if len(cleaned) > max_shown:
        return ", ".join(cleaned[:max_shown]) + " et al."
    return ", ".join(cleaned)


def build_recall_candidate(
    *,
    title: str,
    source: RecallSource,
    source_rank: int = 0,
    doi: str | None = None,
    arxiv_id: str | None = None,
    openalex_id: str | None = None,
    s2_id: str | None = None,
    abstract: str | None = None,
    year: int | None = None,
    authors: str | None = None,
    raw_score: float | None = None,
    seed_paper_id: str | None = None,
    seed_relation: str | None = None,
    pdf_url: str | None = None,
    is_open_access: bool | None = None,
    citation_count: int | None = None,
    provenance: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> RecallCandidate | None:
    """从规范化字段构建 RecallCandidate；无 title 时返回 None。"""
    title = (title or "").strip()
    if not title:
        return None

    ndoi = normalize_doi(doi)
    narxiv = normalize_arxiv_id(arxiv_id)
    noa = normalize_openalex_id(openalex_id)
    ns2 = normalize_s2_id(s2_id)

    canonical_key = build_canonical_id(
        doi=ndoi,
        arxiv_id=narxiv,
        openalex_id=noa,
        s2_id=ns2,
        title=title,
    )

    return RecallCandidate(
        canonical_key=canonical_key,
        doi=ndoi,
        arxiv_id=narxiv,
        openalex_id=noa,
        s2_id=ns2,
        title=title,
        abstract=(abstract or "").strip() or None,
        year=year,
        authors=(authors or "").strip() or None,
        source=source,
        source_rank=source_rank,
        raw_score=raw_score,
        seed_paper_id=seed_paper_id,
        seed_relation=seed_relation,
        pdf_url=(pdf_url or "").strip() or None,
        is_open_access=is_open_access,
        citation_count=citation_count,
        provenance=list(provenance or []),
        extra=dict(extra or {}),
    )


def candidate_from_legacy_reference(
    raw: dict[str, Any],
    *,
    source: RecallSource,
    source_rank: int,
    provider_name: str,
    seed_paper_id: str | None = None,
    seed_relation: str | None = None,
    extra_provenance: list[str] | None = None,
) -> RecallCandidate | None:
    """
    将旧版 get_paper_references dict 映射为 RecallCandidate。

    兼容 ext_id / s2_id 字段及 OpenAlex / S2 混合格式。
    """
    ext_id = raw.get("ext_id") or raw.get("s2_id") or ""
    openalex_id = raw.get("openalex_id")
    s2_id = raw.get("s2_id")

    if not openalex_id and isinstance(ext_id, str) and ext_id.upper().startswith("W"):
        openalex_id = ext_id
    if not s2_id and ext_id and not (isinstance(ext_id, str) and ext_id.upper().startswith("W")):
        s2_id = ext_id

    provenance = [f"provider:{provider_name}", f"method:{source.value}"]
    if extra_provenance:
        provenance.extend(extra_provenance)

    pdf_url = raw.get("pdf_url")
    is_open_access = bool(pdf_url) if pdf_url is not None else raw.get("is_open_access")

    return build_recall_candidate(
        title=raw.get("title") or "",
        doi=raw.get("doi"),
        arxiv_id=raw.get("arxiv_id"),
        openalex_id=openalex_id,
        s2_id=s2_id,
        abstract=raw.get("abstract"),
        year=raw.get("year"),
        authors=raw.get("authors"),
        source=source,
        source_rank=source_rank,
        raw_score=raw.get("raw_score") or raw.get("citation_count"),
        seed_paper_id=seed_paper_id,
        seed_relation=seed_relation,
        pdf_url=pdf_url,
        is_open_access=is_open_access,
        citation_count=raw.get("citation_count"),
        provenance=provenance,
    )
