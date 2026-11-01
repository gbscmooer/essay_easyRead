"""Seed corpus adapter for the shared question-driven research workflow."""

from __future__ import annotations

from sqlmodel import Session, select

from ..models.paper import Paper
from ..models.workspace import Workspace, WorkspacePaper
from .paper_identity import normalize_title
from .research_exclusions import normalize_doi, normalize_arxiv_id, excluded


class SourceError(ValueError):
    def __init__(self, detail: str, status: int = 400):
        self.status = status
        super().__init__(detail)


def seed_options(session: Session, workspace_id: int | None, paper_ids: list[int] | None,
                 search_online: bool, exclude_dois: list[str] | None = None,
                 exclude_arxiv_ids: list[str] | None = None, exclude_titles: list[str] | None = None) -> dict:
    """Snapshot membership at creation; later workspace edits cannot widen a run."""
    if workspace_id is None:
        if paper_ids or not search_online:
            raise SourceError("不联网时请选择工作区母论文；指定论文也需要工作区")
        options = {"workspace_id": None, "seed_paper_ids": [], "search_online": True}
    else:
        workspace = session.get(Workspace, workspace_id)
        if not workspace:
            raise SourceError("工作区不存在", 404)
        available = list(session.exec(select(Paper.id).join(
            WorkspacePaper, WorkspacePaper.paper_id == Paper.id,
        ).where(WorkspacePaper.workspace_id == workspace_id).order_by(Paper.id)).all())
        selected = list(dict.fromkeys(paper_ids if paper_ids is not None else available))
        if not selected:
            raise SourceError("请至少选择一篇母论文")
        if not set(selected).issubset(available):
            raise SourceError("所选论文不属于该工作区")
        if len(selected) > 240:
            raise SourceError("单次研究最多选择 240 篇母论文")
        options = {"workspace_id": workspace_id, "workspace_name": workspace.name,
                   "seed_paper_ids": selected, "search_online": search_online}
    # 排除项随输入快照落盘；规范化后空串剔除
    dois = [normalize_doi(d) for d in (exclude_dois or [])]
    arxivs = [normalize_arxiv_id(a) for a in (exclude_arxiv_ids or [])]
    dois = sorted({d for d in dois if d})
    arxivs = sorted({a for a in arxivs if a})
    titles = sorted({t.strip() for t in (exclude_titles or []) if t.strip()})
    if dois or arxivs or titles:
        options["exclusions"] = {"dois": dois, "arxiv_ids": arxivs}
        if titles:
            options["exclusions"]["titles"] = titles
    return options


def load_seeds(session: Session, options: dict) -> tuple[list[dict], list[Paper]]:
    ids = options.get("seed_paper_ids") or []
    if not ids:
        return [], []
    rows = list(session.exec(select(Paper).where(Paper.id.in_(ids)).order_by(Paper.id)).all())
    if len(rows) != len(ids):
        raise SourceError("部分母论文已删除，请重新选择研究资料")
    papers = []
    for p in rows:
        if excluded(p.model_dump(), options):
            raise SourceError("母论文与排除论文冲突，请重新选择研究资料")
        papers.append({
            "key": f"local:{p.id}", "paper_id": p.id, "is_seed": True,
            "title": p.title or p.filename, "abstract": p.abstract or (p.full_text or "")[:3000],
            "year": p.year, "authors": p.authors, "doi": p.doi, "arxiv_id": p.arxiv_id,
            "citation_count": p.citation_count or 0, "url": f"/reader/{p.id}",
            "venue": {"name": p.venue} if p.venue else None,
            "source_query": "workspace_seed", "cluster": -1,
            # Durable input snapshot: the report does not depend on future PDF edits.
            "source_text": (p.full_text or "")[:200000],
            "source_kind": "full_text" if p.full_text else "abstract",
        })
    return papers, rows
