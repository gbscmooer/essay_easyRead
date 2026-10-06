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


def merge_corpus(seeds: list[dict], discovered: list[dict], cap: int) -> list[dict]:
    """Preserve all explicit seeds; DOI/arXiv/title duplicates enrich their row."""
    result: list[dict] = []
    aliases: dict[str, dict] = {}
    for paper in [*seeds, *discovered]:
        identifiers = [str(paper.get("key") or "")]
        for name in ("doi", "arxiv_id"):
            if paper.get(name):
                from .paper_identity import normalize_doi, normalize_arxiv_id
                value = normalize_doi(paper[name]) if name == "doi" else normalize_arxiv_id(paper[name])
                if value:
                    identifiers.append(name + ":" + value)
        if paper.get("title"):
            identifiers.append("title:" + normalize_title(paper["title"]))
        current = next((aliases[k] for k in identifiers if k and k in aliases), None)
        if current is None:
            current = dict(paper)
            result.append(current)
        else:
            for key, value in paper.items():
                if not current.get(key) and value:
                    current[key] = value
        for key in identifiers:
            if key:
                aliases[key] = current
    return result[:max(cap, len(seeds))]


async def recall_seed_neighbors(rows: list[Paper], depth: str) -> tuple[list[dict], dict]:
    """Reuse deep-research provider recall, without a second run or PDF downloads."""
    import asyncio
    from .deep_research.stages.expand import collect_online_recall
    from .deep_research.explorer import _dedupe_candidates
    from .deep_research.fusion_ranker import fuse_candidates

    limit = {"lite": 2, "quick": 3, "standard": 6, "deep": 10}.get(depth, 6)
    selected = rows[:limit]
    pools, stats = await asyncio.wait_for(collect_online_recall(selected, {"recall_candidate_limit": 40}), timeout=120)
    candidates = [item.representative for item in fuse_candidates(pools)]
    return _dedupe_candidates(candidates), {
        "expanded_seeds": len(selected), "total_seeds": len(rows),
        "candidate_count": len(candidates),
        "channels": list(pools),
    }
