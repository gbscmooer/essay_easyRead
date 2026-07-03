"""论文自动标签服务。"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from sqlmodel import Session, select

from ..models.paper import Paper
from ..models.tag import PaperTag, Tag
from .auto_tagger import auto_tag_paper
from .database import get_engine

logger = logging.getLogger(__name__)

_TAG_COLORS = [
    "#3b82f6",
    "#10b981",
    "#f59e0b",
    "#8b5cf6",
    "#ef4444",
    "#06b6d4",
    "#ec4899",
    "#84cc16",
]


def _normalize_tag_name(name: str) -> str:
    return " ".join(str(name or "").strip().split())


def _tag_color(name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()
    idx = int(digest[:8], 16) % len(_TAG_COLORS)
    return _TAG_COLORS[idx]


async def apply_auto_tags_to_paper(paper_id: int, result: dict, cfg: dict) -> list[str]:
    """根据论文分析结果自动生成并落库标签。"""
    api_key = cfg.get("api_key", "")
    base_url = cfg.get("base_url", "")
    model = cfg.get("model", "")
    if not api_key or not base_url or not model:
        return []

    title = _normalize_tag_name(result.get("title", ""))
    one_sentence = _normalize_tag_name(result.get("one_sentence", ""))
    method = result.get("method", {})
    method_overview = ""
    if isinstance(method, dict):
        method_overview = _normalize_tag_name(method.get("overview", ""))
    venue = _normalize_tag_name(result.get("venue", ""))

    try:
        raw_tags = await auto_tag_paper(
            title=title,
            one_sentence=one_sentence,
            method_overview=method_overview,
            venue=venue,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
    except Exception as e:
        logger.warning(f"auto tag generation failed for paper {paper_id}: {e}")
        return []

    tags: list[str] = []
    seen: set[str] = set()
    for raw in raw_tags:
        clean = _normalize_tag_name(raw)
        key = clean.lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        tags.append(clean)

    if not tags:
        return []

    engine = get_engine()
    applied: list[str] = []
    with Session(engine) as session:
        paper = session.get(Paper, paper_id)
        if not paper:
            return []

        existing_tags = {
            (tag.name or "").lower(): tag
            for tag in session.exec(select(Tag)).all()
        }
        current_tag_names = {
            (tag.name or "").lower()
            for tag in session.exec(
                select(Tag)
                .join(PaperTag, PaperTag.tag_id == Tag.id)
                .where(PaperTag.paper_id == paper_id)
            ).all()
        }

        for name in tags:
            key = name.lower()
            if key in current_tag_names:
                continue

            tag = existing_tags.get(key)
            if tag is None:
                tag = Tag(name=name, color=_tag_color(name))
                session.add(tag)
                session.flush()
                session.refresh(tag)
                existing_tags[key] = tag

            session.add(PaperTag(paper_id=paper_id, tag_id=tag.id))
            current_tag_names.add(key)
            applied.append(tag.name)

        if applied:
            paper.updated_at = datetime.now(timezone.utc)
            session.add(paper)
            session.commit()

    if applied:
        logger.info(f"auto tag applied for paper {paper_id}: {applied}")
    return applied
