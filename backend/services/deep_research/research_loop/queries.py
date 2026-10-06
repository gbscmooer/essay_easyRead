"""Read projections; hidden evaluation labels are never imported by runtime code."""

from sqlmodel import select

from ....models.research_core import (
    ResearchQuestion,
    ResearchAnswerRevision,
    ResearchDossierRevision,
    ResearchAsset,
)
from .contracts import digest, SUPPORTED_LOOP_VERSIONS
from ....models.exploration import ExplorationRun


def questions(session, run_id):
    plans = session.exec(
        select(ResearchAsset).where(
            ResearchAsset.run_id == run_id,
            ResearchAsset.content_kind == "research_plan",
            ResearchAsset.status == "active",
        )
    ).all()
    # Project existing frozen plans; never recompute or upgrade their quality.
    run = session.get(ExplorationRun, run_id)
    protocol = (
        session.exec(
            select(ResearchAsset).where(
                ResearchAsset.run_id == run_id,
                ResearchAsset.asset_id == run.protocol_asset_id,
                ResearchAsset.content_kind == "protocol",
                ResearchAsset.status == "active",
            )
        ).first()
        if run
        else None
    )
    payload = (protocol.inline_payload or {}) if protocol else {}
    revision = payload.get("execution_revision")
    valid_protocol = bool(
        protocol
        and revision in SUPPORTED_LOOP_VERSIONS
        and protocol.content_hash
        == payload.get("hash")
        == digest({k: v for k, v in payload.items() if k != "hash"})
        and ((run.options or {}).get("workflow") or {}).get("loop_revision") == revision
    )
    first_versions = {
        "user_clause_coverage_v1": 8,
        "user_clause_coverage_v2": 9,
        "short_source_bounded_repair_v3": 12,
        "per_question_short_source_v4": 13,
        "source_text_scope_v5": 16,
        "source_request_navigation_v6": 20,
    }

    def valid(a):
        p = a.inline_payload or {}
        minimum = first_versions.get(p.get("intent_coverage", {}).get("method"))
        return bool(
            valid_protocol
            and minimum
            and int(revision.rsplit("v", 1)[1]) >= minimum
            and a.content_hash == digest(p)
            and (
                revision not in ("question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20")
                or (
                    p.get("protocol_hash") == payload["hash"]
                    and p.get("execution_revision") == revision
                )
            )
        )

    plan = next((a.inline_payload for a in plans if valid(a)), {})
    rows = []
    for question in session.exec(
        select(ResearchQuestion)
        .where(ResearchQuestion.run_id == run_id)
        .order_by(ResearchQuestion.id)
    ).all():
        rows.append(
            {
                **question.model_dump(exclude={"id", "run_id"}),
                "requirements": plan.get("requirement_ledger", {}).get(
                    question.question_id, []
                ),
                "scope_constraints": plan.get("constraints", {}).get(
                    question.question_id, []
                ),
                "reading_role": next(
                    (
                        r["role"]
                        for r in plan.get("reading_roles", [])
                        if r["question_id"] == question.question_id
                    ),
                    None,
                ),
                "planning_gaps": plan.get("intent_coverage", {})
                .get("gaps_by_question", {})
                .get(question.question_id, []),
            }
        )
    return rows


def revisions(session, model, run_id, before, limit):
    statement = select(model).where(model.run_id == run_id)
    if before is not None:
        statement = statement.where(model.id < before)
    rows = list(
        session.exec(statement.order_by(model.id.desc()).limit(limit + 1)).all()
    )
    more = len(rows) > limit
    return rows[:limit], rows[limit - 1].id if more else None
