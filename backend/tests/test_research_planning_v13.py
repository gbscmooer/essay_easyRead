"""Grouped planning contracts, recorded fault shapes, and real offline workers."""

import asyncio
from copy import deepcopy

import pytest
from pydantic import ValidationError
from sqlmodel import Session

from backend.models.exploration import ExplorationRun
from backend.models.research_core import (
    ResearchAsset,
    ResearchEvent,
    ResearchTask,
    ResearchEvidence,
)
from backend.services.deep_research.research_loop.intent_v13 import (
    GroupedPlan,
    flatten,
    make_anchored_plan,
    PlanningRejected,
)
from backend.services.deep_research.research_loop import worker
from backend.services.deep_research.research_loop.repository import Repository
from backend.services.deep_research.research_loop.resources import ResourceExhausted
from backend.services.deep_research.research_loop.tools import Tools
from backend.services.deep_research.research_loop.controller import Controller
from backend.tests.test_research_intent_v9 import specs
from backend.tests.test_research_sources_v11 import (
    env11 as source_fixture,
    source_model,
)


class Planner:
    def __init__(self, fault=None, persistent=False):
        self.fault, self.persistent, self.calls, self.inputs = fault, persistent, [], []

    async def model(self, system, data, **kw):
        stage = kw["stage"]
        self.calls.append(stage)
        self.inputs.append(deepcopy(data))
        corrupt = len(self.calls) == 1 or self.persistent
        if stage in ("planning", "planning_repair"):
            groups = []
            for spec in data["questions"]:
                qid = spec["question_id"]
                sources = [c for c in data["source_clauses"] if c["question_id"] == qid]
                names = [
                    n
                    for n in ("AlphaNet", "BetaNet")
                    if any(n in c["source_quote"] for c in sources)
                ]
                requests = []
                for name in names:
                    clause = next(c for c in sources if name in c["source_quote"])
                    requests.append(
                        {
                            "clause_id": clause["clause_id"],
                            "requirement": f"说明{name}队列处理",
                            "subject": name,
                        }
                    )
                for clause in sources:
                    if not any(
                        r["clause_id"] == clause["clause_id"] for r in requests
                    ) and not (
                        "不做" in clause["source_quote"]
                        or "只研究" in clause["source_quote"]
                    ):
                        requests.append(
                            {
                                "clause_id": clause["clause_id"],
                                "requirement": clause["source_quote"],
                                "subject": "",
                            }
                        )
                groups.append(
                    {
                        "question_id": qid,
                        "queries": ["bounded queue management"],
                        "reading_role": "mechanism",
                        "targets": names,
                        "requests": requests,
                    }
                )
            result = {
                "question_plans": groups,
                "answer_units": ["说明机制"],
                "reading_queries": ["queue overflow mechanism"],
            }
            if corrupt:
                if self.fault == "alias":
                    groups[0]["requests"][0]["clause_id"] = "C404"
                elif self.fault == "role_array":
                    groups[0]["reading_role"] = [
                        "overview",
                        "mechanism",
                        "implementation",
                    ]
                elif self.fault == "query_count":
                    groups[0]["queries"] *= 3
                elif self.fault == "duplicate_question":
                    groups.append(deepcopy(groups[0]))
                elif self.fault == "unknown_question":
                    groups[0]["question_id"] = "unknown"
                elif self.fault == "missing_question":
                    groups.pop()
                elif self.fault == "cross_question":
                    groups[1]["requests"][0]["clause_id"] = groups[0]["requests"][0][
                        "clause_id"
                    ]
                elif self.fault == "missing":
                    groups[0]["requests"].pop()
                elif self.fault == "constraint_request":
                    groups[0]["requests"].append(
                        {
                            "clause_id": sources[-1]["clause_id"],
                            "requirement": sources[-1]["source_quote"],
                        }
                    )
                elif self.fault == "duplicate_navigation":
                    groups[0]["queries"] *= 2
                    groups[0]["targets"] *= 2
                elif self.fault == "budget":
                    raise ResourceExhausted("offline budget limit")
            return kw["schema"].model_validate(result).model_dump()
        result = {
            "coverage": [],
            "unsupported_unit_ids": [],
            "missing_requests": [],
            "target_checks": [
                {**t, "explicit": True, "reason": "User named it"}
                for t in data["targets"]
            ],
            "response_kinds": [
                {
                    "unit_id": u["unit_id"],
                    "response_kind": "fact",
                    "reason": "Existing mechanism",
                }
                for u in data["requirements"]
            ],
        }
        for clause in data["source_clauses"]:
            units = [
                u["unit_id"]
                for u in data["requirements"]
                if u["clause_id"] == clause["clause_id"]
            ]
            constraint = (
                "不做" in clause["source_quote"] or "只研究" in clause["source_quote"]
            )
            result["coverage"].append(
                {
                    "clause_id": clause["clause_id"],
                    "kind": "constraint" if constraint else "request",
                    "covered": bool(units) or constraint,
                    "unit_ids": [] if constraint else units,
                    "reason": "Original request and scope",
                }
            )
        if self.fault == "missing" and (stage == "planning_check" or self.persistent):
            result["coverage"][0]["covered"] = False
            result["missing_requests"] = [
                {
                    "question_id": "main",
                    "clause_id": data["source_clauses"][0]["clause_id"],
                    "requirement": "说明BetaNet队列处理",
                    "subject": "BetaNet",
                }
            ]
        if self.fault == "unknown_review" and (
            stage == "planning_check" or self.persistent
        ):
            result["coverage"][0]["unit_ids"] = ["R404"]
        return kw["schema"].model_validate(result).model_dump()


@pytest.mark.parametrize(
    "fault",
    ["alias", "role_array", "query_count", "duplicate_question", "unknown_question"],
)
def test_generator_fault_repaired_with_specific_contract_once(fault):
    planner = Planner(fault)
    plan = asyncio.run(make_anchored_plan(planner, specs()))
    assert planner.calls == ["planning", "planning_repair", "planning_check_repair"]
    repair = planner.inputs[1]["repair"]
    assert repair["instruction"] and repair["expected_question_ids"] == ["main"]
    if fault in ("role_array", "query_count"):
        assert repair["field_errors"]
    assert len(plan["reading_roles"]) == 1 and len(plan["queries"]) <= 2
    assert plan["intent_coverage"]["repairs_used"] == 1


def test_missing_subject_is_replanned_and_source_anchored():
    planner = Planner("missing")
    plan = asyncio.run(make_anchored_plan(planner, specs()))
    assert planner.calls == [
        "planning",
        "planning_check",
        "planning_repair",
        "planning_check_repair",
    ]
    assert {u["subject"] for u in plan["requirement_ledger"]["main"]} == {
        "AlphaNet",
        "BetaNet",
    }
    for unit in plan["requirement_ledger"]["main"]:
        assert (
            specs()[0]["text"][unit["source_start"] : unit["source_end"]]
            == unit["source_quote"]
        )


def test_check_fault_rechecks_same_generation_not_replanning():
    planner = Planner("unknown_review")
    asyncio.run(make_anchored_plan(planner, specs()))
    assert planner.calls == ["planning", "planning_check", "planning_check_repair"]
    assert planner.inputs[2]["repair"]["allowed_unit_refs"] == ["R1", "R2"]


@pytest.mark.parametrize(
    "fault", ["alias", "role_array", "duplicate_question", "missing", "unknown_review"]
)
def test_persistent_error_stops_without_claiming_complete(fault):
    planner = Planner(fault, True)
    with pytest.raises(PlanningRejected):
        asyncio.run(make_anchored_plan(planner, specs()))
    assert len(planner.calls) <= 4


def test_navigation_duplicates_are_program_normalized_without_changing_requirements():
    planner = Planner("duplicate_navigation")
    plan = asyncio.run(make_anchored_plan(planner, specs()))
    assert planner.calls == ["planning", "planning_check"]
    assert len(plan["queries"]) == 1 and len(plan["targets"]) == 2
    assert len(plan["requirement_ledger"]["main"]) == 2


def test_constraint_is_frozen_as_scope_not_a_paper_fact_request():
    plan = asyncio.run(make_anchored_plan(Planner("constraint_request"), specs()))
    assert len(plan["requirement_ledger"]["main"]) == 2
    assert plan["constraints"]["main"] == ["只研究有界队列，不做性能排名"]


@pytest.mark.parametrize("fault", [None, "cross_question", "missing_question"])
def test_multiple_questions_keep_own_source_and_scalar_role(fault):
    question_specs = [*specs(), {**specs()[0], "question_id": "q1"}]
    planner = Planner(fault)
    plan = asyncio.run(make_anchored_plan(planner, question_specs))
    assert {r["question_id"] for r in plan["reading_roles"]} == {"main", "q1"}
    for qid, units in plan["requirement_ledger"].items():
        assert units and all(u["question_id"] == qid for u in units)
    assert len(plan["queries"]) == 2


def test_named_target_in_explicit_criterion_preserved():
    question_specs = [
        {
            "question_id": "main",
            "text": "说明队列管理机制。",
            "required": True,
            "answer_criteria": ["解释AlphaNet队列处理"],
        }
    ]
    plan = asyncio.run(make_anchored_plan(Planner(), question_specs))
    assert plan["targets"] == [{"question_id": "main", "name": "AlphaNet"}]
    unit = next(
        u
        for u in plan["requirement_ledger"]["main"]
        if u["source_origin"] == "criterion"
    )
    assert (
        unit["source_origin"] == "criterion"
        and unit["source_quote"] == "解释AlphaNet队列处理"
    )


def test_recorded_previous_bad_navigation_shape_cannot_be_represented_as_new_role():
    # Recorded run41 shape, with generic names and no domain answer or gold.
    previous_roles = ["overview", "mechanism", "implementation"]
    payload = {
        "question_plans": [
            {
                "question_id": "main",
                "queries": ["bounded queue"],
                "reading_role": previous_roles,
                "targets": [],
                "requests": [{"clause_id": "C1", "requirement": "解释队列机制"}],
            }
        ],
        "answer_units": ["解释机制"],
        "reading_queries": [],
    }
    with pytest.raises(ValidationError) as exc:
        GroupedPlan.model_validate(payload)
    assert exc.value.errors()[0]["loc"] == ("question_plans", 0, "reading_role")
    payload["question_plans"][0]["reading_role"] = "overview"
    raw = flatten(GroupedPlan.model_validate(payload).model_dump(), specs())
    assert raw["reading_roles"] == [{"question_id": "main", "role": "overview"}]


@pytest.mark.parametrize(
    "fault,persistent,stop",
    [
        ("alias", False, "no_actionable_gap"),
        ("missing", False, "no_actionable_gap"),
        ("duplicate_question", True, "planning_failed"),
        ("unknown_review", True, "planning_failed"),
        ("budget", False, "resource_limit"),
    ],
)
def test_real_v13_worker_dispatch_stops_truthfully_and_releases_lease(
    engine, monkeypatch, fault, persistent, stop
):
    planner = Planner(fault, persistent)
    monkeypatch.setattr(worker, "get_llm_config", lambda: {})

    class TestTools(Tools):
        model = staticmethod(planner.model)

        async def search(self, *args, **kw):
            pytest.fail("Offline providers are forbidden")

    with Session(engine) as session:
        row = ExplorationRun(
            question=specs()[0]["text"],
            depth="quick",
            workflow_version="academic_survey_v2",
            state_version=1,
            options={
                "search_online": False,
                "workflow": {"loop_revision": "question_loop_v13"},
            },
        )
        session.add(row)
        session.commit()
        rid = row.id
    asyncio.run(worker.run(rid, engine=engine, tools_factory=TestTools))
    repo = Repository(engine, rid, "inspection")
    assert repo.run().status == "partial" and repo.run().worker_owner is None
    assert repo.protocol()["execution_revision"] == "question_loop_v13"
    report = repo.run().result["survey_report"]
    assert report["method"]["stop_reason"] == stop
    if stop == "planning_failed":
        assert report["status"] == "partial" and "规划" in report["summary"]
        assert not repo.rows(ResearchTask)
        assert (
            len([e for e in repo.rows(ResearchEvent) if e.type == "planning_rejected"])
            == 2
        )
    elif stop != "resource_limit":
        plan = next(
            a.inline_payload
            for a in repo.rows(ResearchAsset)
            if a.content_kind == "research_plan"
        )
        assert len(plan["requirement_ledger"]["main"]) == 2


env13 = source_fixture


@pytest.mark.parametrize("env13", ["question_loop_v13"], indirect=True)
def test_v13_reading_and_answering_keep_source_gates_and_revision(env13, monkeypatch):
    repo, tools = env13
    monkeypatch.setattr(tools, "model", source_model(tools))
    assert asyncio.run(Controller(tools).run()) == "required_questions_supported"
    assert all(
        e.verification["reviewer_version"] == "question_loop_v13"
        for e in repo.rows(ResearchEvidence)
    )
