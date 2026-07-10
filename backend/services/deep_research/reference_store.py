"""
引用关系持久化 — PaperReference 表的 upsert / 查询 / 解析。

parent_paper_id 表示发现/下载谱系；本模块维护真实的 citing→cited 引用边。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, select

from ...models.research import PaperReference
from ..paper_identity import (
    PaperWork,
    match_local,
    normalize_arxiv_id,
    normalize_doi,
    normalize_title,
)


@dataclass
class ReferenceInput:
    """单条待写入的引用记录。"""

    cited_doi: str | None = None
    cited_arxiv_id: str | None = None
    cited_title: str | None = None
    cited_paper_id: int | None = None
    frequency: int = 1
    contexts: list[dict[str, Any]] = field(default_factory=list)
    source: str = "grobid"
    confidence: float = 1.0


def _normalize_ref_fields(
    *,
    cited_doi: str | None,
    cited_arxiv_id: str | None,
    cited_title: str | None,
) -> tuple[str | None, str | None, str | None]:
    ndoi = normalize_doi(cited_doi) if cited_doi else None
    narxiv = normalize_arxiv_id(cited_arxiv_id) if cited_arxiv_id else None
    ntitle = (cited_title or "").strip() or None
    return ndoi, narxiv, ntitle


def _derive_intents(contexts: list[dict]) -> list[str]:
    seen: list[str] = []
    for ctx in contexts:
        intent = ctx.get("intent") or "background"
        if intent not in seen:
            seen.append(intent)
    return seen or ["background"]


def _merge_contexts(existing: list[dict], new: list[dict], *, limit: int = 20) -> list[dict]:
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for ctx in list(existing) + list(new):
        key = (ctx.get("section", ""), ctx.get("context", ""))
        if key in seen:
            continue
        seen.add(key)
        merged.append({
            "section": ctx.get("section", ""),
            "context": ctx.get("context", ""),
            "intent": ctx.get("intent", "background"),
            "ref_id": ctx.get("ref_id"),
            "ref_index": ctx.get("ref_index"),
            "arxiv_id": ctx.get("arxiv_id"),
            "doi": ctx.get("doi"),
            "title": ctx.get("title"),
        })
        if len(merged) >= limit:
            break
    return merged


def _coerce_input(item: ReferenceInput | dict[str, Any]) -> ReferenceInput:
    if isinstance(item, ReferenceInput):
        return item
    return ReferenceInput(
        cited_doi=item.get("cited_doi") or item.get("doi"),
        cited_arxiv_id=item.get("cited_arxiv_id") or item.get("arxiv_id"),
        cited_title=item.get("cited_title") or item.get("title"),
        cited_paper_id=item.get("cited_paper_id"),
        frequency=int(item.get("frequency") or item.get("freq") or 1),
        contexts=list(item.get("contexts") or []),
        source=item.get("source") or "grobid",
        confidence=float(item.get("confidence", 1.0)),
    )


def _find_existing(
    session: Session,
    citing_paper_id: int,
    *,
    cited_paper_id: int | None,
    cited_doi: str | None,
    cited_arxiv_id: str | None,
    cited_title: str | None,
) -> PaperReference | None:
    if cited_paper_id is not None:
        hit = session.exec(
            select(PaperReference)
            .where(PaperReference.citing_paper_id == citing_paper_id)
            .where(PaperReference.cited_paper_id == cited_paper_id)
        ).first()
        if hit:
            return hit

    if cited_doi:
        hit = session.exec(
            select(PaperReference)
            .where(PaperReference.citing_paper_id == citing_paper_id)
            .where(PaperReference.cited_doi == cited_doi)
        ).first()
        if hit:
            return hit

    if cited_arxiv_id:
        hit = session.exec(
            select(PaperReference)
            .where(PaperReference.citing_paper_id == citing_paper_id)
            .where(PaperReference.cited_arxiv_id == cited_arxiv_id)
        ).first()
        if hit:
            return hit

    if cited_title:
        normalized = normalize_title(cited_title)
        candidates = session.exec(
            select(PaperReference)
            .where(PaperReference.citing_paper_id == citing_paper_id)
            .where(PaperReference.cited_title.is_not(None))  # type: ignore[union-attr]
        ).all()
        for hit in candidates:
            if normalize_title(hit.cited_title) == normalized:
                return hit

    return None


def upsert_references(
    session: Session,
    citing_paper_id: int,
    refs: list[ReferenceInput | dict[str, Any]],
) -> list[PaperReference]:
    """批量 upsert 引用记录；按 citing + (cited_paper_id|doi|arxiv|title) 去重合并。"""
    saved: list[PaperReference] = []
    now = datetime.now(timezone.utc)

    for raw in refs:
        item = _coerce_input(raw)
        ndoi, narxiv, ntitle = _normalize_ref_fields(
            cited_doi=item.cited_doi,
            cited_arxiv_id=item.cited_arxiv_id,
            cited_title=item.cited_title,
        )
        if not item.cited_paper_id and not ndoi and not narxiv and not ntitle:
            continue

        existing = _find_existing(
            session,
            citing_paper_id,
            cited_paper_id=item.cited_paper_id,
            cited_doi=ndoi,
            cited_arxiv_id=narxiv,
            cited_title=ntitle,
        )

        merged_contexts = _merge_contexts(
            existing.contexts if existing else [],
            item.contexts,
        )
        merged_intents = _derive_intents(merged_contexts)

        if existing:
            existing.frequency = max(existing.frequency, item.frequency)
            existing.contexts = merged_contexts
            existing.intents = merged_intents
            existing.source = item.source or existing.source
            existing.confidence = max(existing.confidence, item.confidence)
            if item.cited_paper_id and not existing.cited_paper_id:
                existing.cited_paper_id = item.cited_paper_id
            if ndoi and not existing.cited_doi:
                existing.cited_doi = ndoi
            if narxiv and not existing.cited_arxiv_id:
                existing.cited_arxiv_id = narxiv
            if ntitle and not existing.cited_title:
                existing.cited_title = ntitle
            existing.updated_at = now
            session.add(existing)
            saved.append(existing)
            continue

        row = PaperReference(
            citing_paper_id=citing_paper_id,
            cited_paper_id=item.cited_paper_id,
            cited_doi=ndoi,
            cited_arxiv_id=narxiv,
            cited_title=ntitle,
            frequency=item.frequency,
            contexts=merged_contexts,
            intents=merged_intents,
            source=item.source,
            confidence=item.confidence,
            updated_at=now,
        )
        session.add(row)
        saved.append(row)

    session.commit()
    for row in saved:
        session.refresh(row)
    return saved


def list_by_citing(session: Session, citing_paper_id: int) -> list[PaperReference]:
    """列出某篇论文发出的全部引用记录。"""
    return list(
        session.exec(
            select(PaperReference)
            .where(PaperReference.citing_paper_id == citing_paper_id)
            .order_by(PaperReference.frequency.desc())  # type: ignore[attr-defined]
        ).all()
    )


def list_for_papers(session: Session, paper_ids: set[int] | list[int]) -> list[PaperReference]:
    """批量加载多篇 citing 论文的引用记录。"""
    if not paper_ids:
        return []
    ids = list(paper_ids)
    return list(
        session.exec(
            select(PaperReference).where(PaperReference.citing_paper_id.in_(ids))  # type: ignore[attr-defined]
        ).all()
    )


def resolve_cited_links(
    session: Session,
    refs: list[PaperReference] | None = None,
    *,
    citing_paper_id: int | None = None,
) -> int:
    """用 paper_identity 将 cited_paper_id 解析到库中已有 Paper。返回更新条数。"""
    if refs is None:
        if citing_paper_id is None:
            return 0
        refs = list_by_citing(session, citing_paper_id)

    updated = 0
    now = datetime.now(timezone.utc)

    for ref in refs:
        if ref.cited_paper_id:
            continue
        # 自动链接只接受 DOI/arXiv 等强身份。标题-only 引用保留为 metadata，
        # 避免 GROBID 噪声标题经 fuzzy 匹配错误挂到真实引用边。
        if not ref.cited_doi and not ref.cited_arxiv_id:
            continue
        work = PaperWork(
            doi=ref.cited_doi,
            arxiv_id=ref.cited_arxiv_id,
            title=ref.cited_title,
        )
        result = match_local(session, work)
        if result.matched and result.paper and result.method != "fuzzy":
            ref.cited_paper_id = result.paper.id
            ref.confidence = max(ref.confidence, result.confidence)
            ref.updated_at = now
            session.add(ref)
            updated += 1

    if updated:
        session.commit()
    return updated
