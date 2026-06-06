"""
Stage 1 — collect

收集工作区中已完成解析的论文。
"""

from sqlmodel import Session, create_engine, select
from ....config import settings
from ....models.paper import Paper
from ....models.workspace import WorkspacePaper

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})
    return _engine


async def stage_collect(ws_id: int, params: dict) -> list[Paper]:
    """收集工作区中 status=done 的论文"""
    pids = params.get("paper_ids")
    with Session(_get_engine()) as s:
        stmt = (select(Paper).join(WorkspacePaper, WorkspacePaper.paper_id == Paper.id)
                .where(WorkspacePaper.workspace_id == ws_id)
                .where(Paper.status == "done"))
        if pids:
            stmt = stmt.where(Paper.id.in_(pids))
        return s.exec(stmt).all()
