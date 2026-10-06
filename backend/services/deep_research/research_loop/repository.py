"""Transactional persistence. Every worker write is fenced by its live run lease."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, update
from sqlmodel import Session, select

from ....models.exploration import ExplorationRun
from ....models.research_core import (
    ResearchAsset, ResearchEvidence, ResearchQuestion, ResearchAnswerRevision,
    ResearchDossierRevision, ResearchTask, ResearchTaskAttempt, ResearchBudgetEvent,
    ResearchRelation,
)
from ...research_event_log import append_event
from .contracts import Action, digest

LEASE_SECONDS = 180


def now():
    return datetime.now(timezone.utc)


class LeaseLost(RuntimeError):
    pass


class Repository:
    def __init__(self, engine, run_id: int, owner: str):
        self.engine, self.run_id, self.owner = engine, run_id, owner

    def run(self) -> ExplorationRun:
        with Session(self.engine) as session:
            run = session.get(ExplorationRun, self.run_id)
            if run is None:
                raise LeaseLost("研究记录不存在")
            return run

    def acquire(self) -> bool:
        with Session(self.engine) as session:
            run = session.get(ExplorationRun, self.run_id)
            if run is None or run.status not in ("pending", "running"):
                return False
            version = run.state_version
            result = session.exec(update(ExplorationRun).where(
                ExplorationRun.id == self.run_id,
                ExplorationRun.state_version == version,
                ExplorationRun.status.in_(("pending", "running")),
                or_(ExplorationRun.worker_owner.is_(None), ExplorationRun.worker_lease_expires_at < now()),
            ).values(worker_owner=self.owner, worker_lease_expires_at=now() + timedelta(seconds=LEASE_SECONDS),
                     status="running", state_version=version + 1, error_message=None).execution_options(synchronize_session=False))
            if result.rowcount != 1:
                session.rollback()
                return False
            # A crash cannot turn an unknown dispatched attempt into zero cost.
            rows = session.exec(select(ResearchBudgetEvent).where(ResearchBudgetEvent.run_id == self.run_id)).all()
            balances = {}
            for row in rows:
                key = (row.resource, row.task_id, row.attempt_id)
                balances[key] = balances.get(key, 0) + row.delta_reserved - row.delta_used - row.delta_refunded
            for (resource, task_id, attempt_id), amount in balances.items():
                if amount > 0:
                    session.add(ResearchBudgetEvent(run_id=self.run_id, resource=resource,
                        task_id=task_id, attempt_id=attempt_id, delta_used=amount,
                        usage_known=False, note="crash_recovery_upper_bound"))
            for task in session.exec(select(ResearchTask).where(
                ResearchTask.run_id == self.run_id, ResearchTask.state == "running"
            )).all():
                task.state, task.lease_owner, task.lease_expires_at = "pending", None, None
                session.add(task)
            append_event(session, run_id=self.run_id, type="run_started",
                         payload={"status": "running", "state_version": version + 1})
            session.commit()
            return True

    def fence(self, session: Session, *, accounting=False):
        statuses = ("running", "paused", "cancelled", "completed", "partial", "failed") if accounting else ("running",)
        result = session.exec(update(ExplorationRun).where(
            ExplorationRun.id == self.run_id, ExplorationRun.worker_owner == self.owner,
            ExplorationRun.worker_lease_expires_at > now(), ExplorationRun.status.in_(statuses),
        ).values(updated_at=now()))
        if result.rowcount != 1:
            raise LeaseLost("worker 所有权或运行状态已变化")

    @contextmanager
    def transaction(self, *, accounting=False):
        with Session(self.engine, expire_on_commit=False) as session:
            self.fence(session, accounting=accounting)
            yield session
            session.commit()

    def heartbeat(self, seconds: float):
        with self.transaction(accounting=True) as session:
            session.exec(update(ExplorationRun).where(ExplorationRun.id == self.run_id).values(
                worker_lease_expires_at=now() + timedelta(seconds=LEASE_SECONDS),
                active_seconds=ExplorationRun.active_seconds + max(0.0, seconds)))

    def release(self):
        with Session(self.engine) as session:
            session.exec(update(ExplorationRun).where(
                ExplorationRun.id == self.run_id, ExplorationRun.worker_owner == self.owner,
            ).values(worker_owner=None, worker_lease_expires_at=None))
            session.commit()

    def rows(self, model):
        with Session(self.engine) as session:
            return list(session.exec(select(model).where(model.run_id == self.run_id).order_by(model.id)).all())

    def asset(self, asset_id: str, *, readable=False) -> ResearchAsset:
        with Session(self.engine) as session:
            row = session.exec(select(ResearchAsset).where(
                ResearchAsset.run_id == self.run_id, ResearchAsset.asset_id == asset_id,
                ResearchAsset.status.in_(("active", "superseded") if readable else ("active",)),
            )).first()
            if row is None:
                raise ValueError("资产不可用")
            return row

    def protocol(self) -> dict | None:
        run = self.run()
        return self.asset(run.protocol_asset_id).inline_payload if run.protocol_asset_id else None

    def initialize(self, specs: list[dict], budget: dict):
        if self.protocol():
            return
        run = self.run()
        workflow = (run.options or {}).get("workflow") or {}
        protocol = {"version": 1, "question": run.question,
                    "subquestions": [{"subquestion_id": x["question_id"], "question": x["text"], **x} for x in specs],
                    "budget": budget, "change_reason": "initial_freeze", "loop_schema_version": 2,
                    "execution_revision": workflow.get("loop_revision"),
                    "eligibility": {"exclusions": (run.options or {}).get("exclusions", {})},
                    "content_cutoff": workflow.get("content_cutoff"), "search_time": now().isoformat(),
                    "source_policy": {"search_online": (run.options or {}).get("search_online", True)},
                    "stop_rules": [{"no_progress_limit": 6, "actions": budget["actions"]}]}
        protocol["hash"] = digest(protocol)
        with self.transaction() as session:
            asset = ResearchAsset(run_id=self.run_id, content_kind="protocol", inline_payload=protocol,
                                  content_hash=protocol["hash"], parse_status="parsed")
            session.add(asset)
            session.flush()
            run = session.get(ExplorationRun, self.run_id)
            run.protocol_asset_id = asset.asset_id
            session.add(run)
            for spec in specs:
                session.add(ResearchQuestion(run_id=self.run_id, **spec))
            append_event(session, run_id=self.run_id, type="note", payload={"message": "研究问题与资源上限已冻结"})

    def claim_task(self, action: Action):
        key = action.key(self.protocol()["hash"])
        with self.transaction() as session:
            task = session.exec(select(ResearchTask).where(
                ResearchTask.run_id == self.run_id, ResearchTask.idempotency_key == key)).first()
            if task and task.state == "succeeded":
                return None
            if task is None:
                task = ResearchTask(run_id=self.run_id, kind=action.tool, subquestion_id=action.question_id,
                                    payload=action.model_dump(), idempotency_key=key)
            task.state, task.lease_owner = "running", self.owner
            task.lease_expires_at = now() + timedelta(seconds=LEASE_SECONDS)
            session.add(task)
            session.flush()
            attempt = ResearchTaskAttempt(run_id=self.run_id, task_id=task.task_id, stage=action.tool,
                                         started_at=now(), input_hash=key)
            session.add(attempt)
            session.flush()
            append_event(session, run_id=self.run_id, type="task_state_changed", task_id=task.task_id,
                         payload={"state": "running", "tool": action.tool, "question_id": action.question_id, "reason": action.reason})
            return task.task_id, attempt.attempt_id

    def complete_task(self, task_id: str, attempt_id: str, result: dict):
        with self.transaction() as session:
            task = session.exec(select(ResearchTask).where(ResearchTask.task_id == task_id)).one()
            if task.lease_owner != self.owner or task.state != "running":
                raise LeaseLost("任务所有权已变化")
            created = []
            made_progress = False
            for spec in result.get("assets", []):
                existing = session.exec(select(ResearchAsset).where(
                    ResearchAsset.run_id == self.run_id, ResearchAsset.content_kind == spec["content_kind"],
                    ResearchAsset.content_hash == spec["content_hash"], ResearchAsset.status == "active")).first()
                asset = existing or ResearchAsset(run_id=self.run_id, **spec)
                made_progress |= existing is None and spec["content_kind"] == "document"
                session.add(asset)
                session.flush()
                created.append(asset.asset_id)
            for spec in result.get("evidence", []):
                exists = session.exec(select(ResearchEvidence).where(ResearchEvidence.evidence_id == spec["evidence_id"])).first()
                if not exists:
                    session.add(ResearchEvidence(run_id=self.run_id, **spec))
                    made_progress = True
            if result.get("answer"):
                spec = result["answer"]
                answer = ResearchAnswerRevision(run_id=self.run_id, question_id=task.subquestion_id,
                                               protocol_version=1, content_hash=digest(spec), **spec)
                session.add(answer)
                session.flush()
                for relation in result.get("relations", []):
                    conditions = {**relation["conditions"], "answer_revision_id": answer.revision_id}
                    session.add(ResearchRelation(run_id=self.run_id, subquestion_id=task.subquestion_id,
                        **{**relation, "relation_id": "rel_" + digest({"relation": relation["relation_id"],
                            "answer_revision_id": answer.revision_id})[:24], "conditions": conditions}))
                question = session.exec(select(ResearchQuestion).where(
                    ResearchQuestion.run_id == self.run_id, ResearchQuestion.question_id == task.subquestion_id,
                    ResearchQuestion.protocol_version == 1)).one()
                question.status, question.gaps, question.answer_revision_id = answer.status, answer.gaps, answer.revision_id
                made_progress |= answer.status == "supported"
                session.add(question)
            task.result_asset_ids = created
            task.state = "failed" if result.get("error_code") else "succeeded"
            task.error_code, task.error_detail = result.get("error_code"), result.get("error_detail")
            task.lease_owner, task.lease_expires_at = None, None
            task.updated_at = now()
            session.add(task)
            attempt = session.exec(select(ResearchTaskAttempt).where(ResearchTaskAttempt.attempt_id == attempt_id)).one()
            attempt.ended_at, attempt.error_code = now(), task.error_code
            attempt.usage = result.get("usage", {})
            session.add(attempt)
            run = session.get(ExplorationRun, self.run_id)
            previous = (run.checkpoint or {}).get("no_progress", 0)
            run.checkpoint = {**(run.checkpoint or {}), "loop_schema_version": 1, "no_progress": 0 if made_progress else previous + 1}
            session.add(run)
            append_event(session, run_id=self.run_id, task_id=task_id, type="task_state_changed",
                         payload={"state": task.state, "question_id": task.subquestion_id, "tool": task.kind,
                                  "evidence_count": len(result.get("evidence", [])), "error_code": task.error_code})

    def skip_task(self, task_id: str, code: str):
        with self.transaction() as session:
            task = session.exec(select(ResearchTask).where(ResearchTask.task_id == task_id)).one()
            task.state, task.error_code = "skipped", code
            task.lease_owner, task.lease_expires_at = None, None
            task.updated_at = now()
            session.add(task)
            append_event(session, run_id=self.run_id, task_id=task_id, type="task_state_changed",
                         payload={"state": "skipped", "code": code, "tool": task.kind})

    def abandon_task(self, task_id: str):
        with self.transaction(accounting=True) as session:
            task = session.exec(select(ResearchTask).where(ResearchTask.task_id == task_id)).one()
            if task.lease_owner == self.owner:
                status = session.get(ExplorationRun, self.run_id).status
                task.state = "cancelled" if status == "cancelled" else "pending"
                task.lease_owner, task.lease_expires_at = None, None
                session.add(task)
