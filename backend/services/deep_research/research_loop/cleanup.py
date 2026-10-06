"""Explicit removal of a stopped experimental run, with ownership fencing."""
from datetime import datetime, timezone

from sqlalchemy import delete, or_, update
from sqlmodel import Session, select

from ....models.exploration import ExplorationRun, ExplorationMessage
from ....models.research_core import (
    ResearchTask, ResearchTaskAttempt, ResearchAsset, ResearchEvidence, ResearchRelation,
    ResearchClaim, ResearchReportRevision, ResearchBudgetEvent, ResearchEvent,
    ResearchIdentityDecision, ResearchQuestion, ResearchAnswerRevision, ResearchDossierRevision,
)
from ...research_event_log import RunStateError


def remove(engine, run_id):
    with Session(engine) as session:
        updated = session.exec(update(ExplorationRun).where(
            ExplorationRun.id == run_id, ExplorationRun.status.notin_(("pending", "running")),
            or_(ExplorationRun.worker_owner.is_(None), ExplorationRun.worker_lease_expires_at < datetime.now(timezone.utc)),
        ).values(worker_owner=None, worker_lease_expires_at=None).execution_options(synchronize_session=False))
        if updated.rowcount != 1:
            raise RunStateError(409, "WORKER_ACTIVE", "请先停止研究并等待执行者退出")
        active_chat = session.exec(select(ExplorationMessage).where(ExplorationMessage.run_id == run_id,
            ExplorationMessage.status.in_(("pending", "running")))).first()
        if active_chat:
            raise RunStateError(409, "CHAT_RUNNING", "追问仍在运行")
        for model in (ExplorationMessage, ResearchTaskAttempt, ResearchReportRevision, ResearchClaim,
                      ResearchAnswerRevision, ResearchDossierRevision, ResearchQuestion, ResearchRelation,
                      ResearchEvidence, ResearchBudgetEvent, ResearchEvent, ResearchIdentityDecision,
                      ResearchTask, ResearchAsset):
            session.exec(delete(model).where(model.run_id == run_id))
        session.exec(delete(ExplorationRun).where(ExplorationRun.id == run_id))
        session.commit()
    return {"message": "已删除"}
