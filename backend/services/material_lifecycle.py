"""Transactional cleanup for current library/workspace material, not reports."""

from sqlalchemy import delete, update
from sqlmodel import select
from ..models.paper import Paper
from ..models.workspace import WorkspacePaper
from ..models.research import ResearchGraph, ResearchRun, PaperReference
from ..models.analysis import AnalysisResult
from ..models.code_analysis import CodeAnalysis
from ..models.note import Note
from ..models.annotation import Annotation
from ..models.tag import PaperTag


class MaterialBusyError(RuntimeError):
    pass


def ensure_idle(session, workspace_ids=(), paper_id=None):
    from .material_activity import backfill_jobs, digest_jobs

    if any(
        backfill_jobs.get(ws, {}).get("status") == "running"
        or digest_jobs.get(ws, {}).get("running")
        for ws in workspace_ids
    ):
        raise MaterialBusyError("工作区引用回填正在执行，请等待完成后再移除资料")
    from ..models.exploration import ExplorationRun

    for run in session.exec(
        select(ExplorationRun).where(ExplorationRun.status.in_(["pending", "running"]))
    ):
        options = run.options or {}
        if options.get("workspace_id") in workspace_ids or (
            paper_id is not None and paper_id in (options.get("seed_paper_ids") or [])
        ):
            raise MaterialBusyError("资料正在被研究任务使用，请等待任务完成后再删除")
    if (
        workspace_ids
        and session.exec(
            select(ResearchRun.id).where(
                ResearchRun.workspace_id.in_(workspace_ids),
                ResearchRun.status.in_(["pending", "running"]),
            )
        ).first()
        is not None
    ):
        raise MaterialBusyError("工作区研究正在执行，请等待完成后再移除资料")
    if paper_id is not None:
        from .analysis_runner import get_job

        job = get_job(paper_id)
        paper = session.get(Paper, paper_id)
        if (job and not job.done) or (paper and paper.code_status == "cloning"):
            raise MaterialBusyError("论文分析或代码下载正在执行，请等待完成后再删除")


def delete_paper_records(session, paper):
    workspace_ids = list(
        session.exec(
            select(WorkspacePaper.workspace_id).where(
                WorkspacePaper.paper_id == paper.id
            )
        ).all()
    )
    ensure_idle(session, workspace_ids, paper.id)
    for model in (
        AnalysisResult,
        CodeAnalysis,
        Note,
        Annotation,
        PaperTag,
        WorkspacePaper,
    ):
        session.exec(delete(model).where(model.paper_id == paper.id))
    session.exec(
        delete(PaperReference).where(PaperReference.citing_paper_id == paper.id)
    )
    session.exec(
        update(PaperReference)
        .where(PaperReference.cited_paper_id == paper.id)
        .values(cited_paper_id=None)
    )
    session.exec(
        update(Paper)
        .where(Paper.parent_paper_id == paper.id)
        .values(parent_paper_id=None)
    )
    for ws_id in workspace_ids:
        invalidate_graph(session, ws_id)
    return workspace_ids


def invalidate_graph(session, workspace_id):
    # ResearchGraph is the CURRENT projection; immutable exploration reports stay intact.
    session.exec(
        delete(ResearchGraph).where(ResearchGraph.workspace_id == workspace_id)
    )


def cleanup_paper_indexes(workspace_ids, paper_id):
    from .deep_research.vector_store import delete_paper
    from .analysis_runner import clear_cache, forget_job

    for ws_id in workspace_ids:
        delete_paper(ws_id, paper_id)
    clear_cache(paper_id)
    forget_job(paper_id)


def workspace_paper_ids(session, workspace_id, requested=None):
    ids = list(
        session.exec(
            select(Paper.id)
            .join(WorkspacePaper, WorkspacePaper.paper_id == Paper.id)
            .where(WorkspacePaper.workspace_id == workspace_id)
        ).all()
    )
    return ids if requested is None else [pid for pid in ids if pid in set(requested)]
