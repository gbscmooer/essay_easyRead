"""Recover explicit scope references without losing real requests or boundaries."""

import asyncio
from copy import deepcopy

import pytest
from sqlmodel import Session

from backend.models.exploration import ExplorationRun
from backend.models.research_core import ResearchAsset
from backend.services.deep_research.research_loop import worker
from backend.services.deep_research.research_loop.controller import Controller
from backend.services.deep_research.research_loop.intent_v16 import (
    PlanningRejected,
    make_anchored_plan,
)
from backend.services.deep_research.research_loop.repository import Repository
from backend.services.deep_research.research_loop.tools import Tools
from backend.tests.test_research_planning_v16 import ScopePlanner


QUESTION = "说明队列管理机制。提出部署核查建议。只讨论单线程队列，不做性能排名。"


def question_specs():
    return [{"question_id": "main", "text": QUESTION, "required": True, "answer_criteria": []}]


class ScopeReferencePlanner(ScopePlanner):
    def __init__(self, fault=None):
        super().__init__()
        self.fault = fault
        self.original_checks = []

    async def model(self, system, data, **kw):
        result = await super().model(system, data, **kw)
        if kw["stage"] not in ("planning", "planning_repair"):
            scope = result["coverage"][-1]
            scope["kind"] = "constraint"
            if self.fault == "unknown":
                scope["unit_ids"] = ["R404"]
            elif self.fault == "cross_clause":
                scope["unit_ids"] = result["coverage"][0]["unit_ids"]
            elif self.fault == "context":
                scope["kind"] = "context"
            elif self.fault == "uncovered":
                scope["covered"] = False
            elif self.fault == "missing_request":
                result["coverage"][0]["covered"] = False
            self.original_checks.append(deepcopy(result))
        return result


def test_known_same_clause_scope_is_frozen_and_not_a_literature_request():
    planner = ScopeReferencePlanner()
    plan = asyncio.run(make_anchored_plan(planner, question_specs(), normalize_constraint_refs=True))
    assert planner.calls == ["planning", "planning_check"]
    assert all(data["workflow_phase"] == "before_retrieval_requirement_validation" for data in planner.inputs)
    assert len(plan["requirement_ledger"]["main"]) == 2
    assert plan["constraints"]["main"] == ["只讨论单线程队列，不做性能排名"]
    assert plan["intent_coverage"]["complete"]
    assert plan["intent_coverage"]["constraint_reference_normalizations"] == [
        {"source_ref": "C3", "removed_unit_refs": ["R3"]}
    ]
    assert planner.original_checks[0]["coverage"][-1]["unit_ids"] == ["R3"]
    for unit in plan["requirement_ledger"]["main"]:
        assert QUESTION[unit["source_start"]:unit["source_end"]] == unit["source_quote"]


@pytest.mark.parametrize("fault,code", [
    ("unknown", "PLAN_CHECK_UNKNOWN_UNIT"),
    ("cross_clause", "PLAN_CHECK_CROSS_CLAUSE"),
    ("context", "PLAN_CHECK_CONSTRAINT_REFERENCES"),
    ("uncovered", "PLAN_CHECK_CONSTRAINT_REFERENCES"),
    ("missing_request", "PLAN_INTENT_INCOMPLETE"),
])
def test_recovery_does_not_approve_bad_references_or_missing_user_requests(fault, code):
    planner = ScopeReferencePlanner(fault)
    with pytest.raises(PlanningRejected) as exc:
        asyncio.run(make_anchored_plan(planner, question_specs(), normalize_constraint_refs=True))
    assert exc.value.code == code
    assert len(planner.calls) <= 4


def test_historical_default_keeps_its_original_rejection():
    with pytest.raises(PlanningRejected) as exc:
        asyncio.run(make_anchored_plan(ScopeReferencePlanner(), question_specs()))
    assert exc.value.code == "PLAN_CHECK_CONSTRAINT_REFERENCES"


def test_real_v19_worker_persists_original_review_and_normalized_scope(engine, monkeypatch):
    planner = ScopeReferencePlanner()
    monkeypatch.setattr(worker, "get_llm_config", lambda: {})

    class OfflineTools(Tools):
        model = staticmethod(planner.model)

    async def inspect_planned_controller(self):
        plan = self.tools.plan()
        assert len(plan["requirement_ledger"]["main"]) == 2
        assert plan["constraints"]["main"] == ["只讨论单线程队列，不做性能排名"]
        return "no_actionable_gap"

    monkeypatch.setattr(Controller, "run", inspect_planned_controller)
    with Session(engine) as session:
        row = ExplorationRun(question=QUESTION, depth="quick", workflow_version="academic_survey_v2",
            state_version=1, options={"search_online": False, "workflow": {"loop_revision": "question_loop_v19"}})
        session.add(row)
        session.commit()
        rid = row.id
    asyncio.run(worker.run(rid, engine=engine, tools_factory=OfflineTools))
    repo = Repository(engine, rid, "inspection")
    assert repo.run().worker_owner is None
    assert repo.run().result["survey_report"]["method"]["stop_reason"] != "planning_failed"
    attempts = [a.inline_payload for a in repo.rows(ResearchAsset) if a.content_kind == "planning_attempt"]
    review = next(a["candidate"] for a in attempts if a["phase"] == "planning_check")
    assert review["coverage"][-1]["unit_ids"] == ["R3"]
    plan = next(a.inline_payload for a in repo.rows(ResearchAsset) if a.content_kind == "research_plan")
    assert plan["intent_coverage"]["constraint_reference_normalizations"]
