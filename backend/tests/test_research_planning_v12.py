"""Offline planning faults through the real version dispatch and worker lifecycle."""

import asyncio
from copy import deepcopy

import pytest
from sqlmodel import Session

from backend.models.exploration import ExplorationRun
from backend.models.research_core import ResearchAsset, ResearchEvent, ResearchTask
from backend.models.research_core import ResearchEvidence, ResearchAnswerRevision
from backend.services.deep_research.research_loop import worker
from backend.services.deep_research.research_loop.intent_v12 import (
    make_anchored_plan,
    PlanningRejected,
)
from backend.services.deep_research.research_loop.repository import Repository
from backend.services.deep_research.research_loop.resources import ResourceExhausted
from backend.services.deep_research.research_loop.tools import Tools
from backend.tests.test_research_intent_v9 import specs, requests, approved
from backend.tests.test_research_sources_v11 import env11 as source_env_fixture, source_model
from backend.services.deep_research.research_loop.controller import Controller


class Planner:
    def __init__(self, fault=None, persistent=False):
        self.fault, self.persistent, self.calls = fault, persistent, []

    async def model(self, system, data, **kw):
        stage = kw["stage"]
        self.calls.append(stage)
        corrupt = len(self.calls) == 1 or self.persistent
        if stage in ("planning", "planning_repair"):
            result = {
                "queries": [
                    {"question_id": "main", "query": "bounded queue management"}
                ],
                "answer_units": ["说明机制"],
                "targets": [
                    {"question_id": "main", "name": n} for n in ("AlphaNet", "BetaNet")
                ],
                "reading_queries": ["queue overflow mechanism"],
                "requests": requests(data),
                "reading_roles": [{"question_id": "main", "role": "mechanism"}],
            }
            if corrupt:
                if self.fault == "alias":
                    result["requests"][0]["clause_id"] = "C404"
                elif self.fault == "subject":
                    result["requests"][0]["subject"] = "GammaNet"
                elif self.fault == "mode":
                    result["requests"][0]["mode"] = "零样本"
                elif self.fault == "target":
                    result["targets"][0]["name"] = "GammaNet"
                elif self.fault == "role":
                    result["reading_roles"].append(deepcopy(result["reading_roles"][0]))
                elif self.fault == "query":
                    result["queries"] = []
                elif self.fault == "missing":
                    result["requests"].pop()
                elif self.fault == "format":
                    result.pop("requests")
                elif self.fault == "budget":
                    raise ResourceExhausted("test exhausted")
            return kw["schema"].model_validate(result).model_dump()
        result = approved(data)
        result["response_kinds"] = [
            {
                "unit_id": u["unit_id"],
                "response_kind": "fact",
                "reason": "Existing method",
            }
            for u in data["requirements"]
        ]
        result["target_checks"] = [
            {**t, "explicit": True, "reason": "User explicitly named it"}
            for t in data["targets"]
        ]
        first_check = stage == "planning_check"
        if first_check or self.persistent:
            if self.fault == "missing" and len(data["requirements"]) == 1:
                result["coverage"][0]["covered"] = False
                result["missing_requests"] = requests(data)[1:]
            elif self.fault == "unknown_review":
                result["coverage"][0]["unit_ids"] = ["R404"]
            elif self.fault == "duplicate_review":
                result["coverage"].append(deepcopy(result["coverage"][0]))
            elif self.fault == "kind_omission":
                result["response_kinds"].pop()
            elif self.fault == "cross_clause":
                result["coverage"][1]["unit_ids"] = ["R1"]
            elif self.fault == "missing_bad_alias":
                result["missing_requests"] = [
                    {**requests(data)[0], "clause_id": "C404"}
                ]
            elif self.fault == "target_not_explicit":
                result["target_checks"][0]["explicit"] = False
            elif self.fault == "check_format":
                result.pop("response_kinds")
        return kw["schema"].model_validate(result).model_dump()


@pytest.mark.parametrize(
    "fault", ["alias", "subject", "mode", "target", "role", "query", "format"]
)
def test_generation_faults_get_one_source_anchored_repair(fault):
    planner = Planner(fault)
    plan = asyncio.run(make_anchored_plan(planner, specs()))
    assert planner.calls == ["planning", "planning_repair", "planning_check_repair"]
    assert plan["intent_coverage"]["repairs_used"] == 1
    for unit in plan["requirement_ledger"]["main"]:
        assert (
            specs()[0]["text"][unit["source_start"] : unit["source_end"]]
            == unit["source_quote"]
        )


def test_omitted_subject_requires_replanning_not_checker_invented_completion():
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
    assert plan["answer_units"] == ["说明机制"]
    assert plan["intent_coverage"]["complete"]


@pytest.mark.parametrize(
    "fault",
    [
        "unknown_review",
        "duplicate_review",
        "kind_omission",
        "cross_clause",
        "missing_bad_alias",
        "check_format",
    ],
)
def test_review_reference_fault_rechecks_same_candidate_once(fault):
    planner = Planner(fault)
    plan = asyncio.run(make_anchored_plan(planner, specs()))
    assert planner.calls == ["planning", "planning_check", "planning_check_repair"]
    assert len(plan["requirement_ledger"]["main"]) == 2


@pytest.mark.parametrize(
    "fault,code",
    [
        ("alias", "PLAN_SOURCE_ALIAS"),
        ("unknown_review", "PLAN_CHECK_UNKNOWN_UNIT"),
        ("target_not_explicit", "PLAN_INTENT_INCOMPLETE"),
        ("check_format", "PLAN_STRUCTURED_OUTPUT"),
    ],
)
def test_persistent_fault_cannot_freeze_complete_or_expand_retry_budget(fault, code):
    planner = Planner(fault, persistent=True)
    with pytest.raises(PlanningRejected, match=code):
        asyncio.run(make_anchored_plan(planner, specs()))
    assert len(planner.calls) <= 4
    assert sum(s in ("planning", "planning_repair") for s in planner.calls) <= 2


def test_generation_and_review_faults_share_one_repair():
    planner = Planner("alias")
    original = planner.model

    async def model(system, data, **kw):
        result = await original(system, data, **kw)
        if kw["stage"] == "planning_check_repair":
            result["coverage"].pop()
        return result

    planner.model = model
    with pytest.raises(PlanningRejected, match="PLAN_CHECK_CLAUSE_COVERAGE"):
        asyncio.run(make_anchored_plan(planner, specs()))
    assert planner.calls == ["planning", "planning_repair", "planning_check_repair"]


def test_method_test_phase_is_fact_and_new_scenario_advice_is_inference():
    planner = Planner()
    original = planner.model

    async def model(system, data, **kw):
        result = await original(system, data, **kw)
        if kw["stage"] == "planning":
            result["requests"][0].update(phase="inference", response_kind="inference")
        else:
            result["response_kinds"][1]["response_kind"] = "inference"
        return result

    planner.model = model
    plan = asyncio.run(make_anchored_plan(planner, specs()))
    units = plan["requirement_ledger"]["main"]
    assert units[0]["phase"] == "inference" and units[0]["response_kind"] == "fact"
    assert units[1]["response_kind"] == "inference"


@pytest.mark.parametrize(
    "fault,persistent,stop",
    [
        ("alias", True, "planning_failed"),
        ("unknown_review", True, "planning_failed"),
        ("budget", False, "resource_limit"),
        ("alias", False, "no_actionable_gap"),
        ("missing", False, "no_actionable_gap"),
    ],
)
def test_real_worker_dispatch_freezes_truthful_status_and_releases_lease(
    engine, monkeypatch, fault, persistent, stop
):
    monkeypatch.setattr(worker, "get_llm_config", lambda: {})
    planner = Planner(fault, persistent)

    class TestTools(Tools):
        model = staticmethod(planner.model)

        async def search(self, *args, **kwargs):
            pytest.fail("Offline test must never query providers")

    with Session(engine) as session:
        row = ExplorationRun(
            question=specs()[0]["text"],
            depth="quick",
            workflow_version="academic_survey_v2",
            state_version=1,
            options={
                "search_online": False,
                "workflow": {"loop_revision": "question_loop_v12"},
            },
        )
        session.add(row)
        session.commit()
        rid = row.id
    asyncio.run(worker.run(rid, engine=engine, tools_factory=TestTools))
    repo = Repository(engine, rid, "inspection")
    run = repo.run()
    assert run.status == "partial" and run.worker_owner is None
    assert repo.protocol()["execution_revision"] == "question_loop_v12"
    assert run.result["survey_report"]["method"]["stop_reason"] == stop
    assert run.result["survey_report"]["delivery"]["delivery_status"] == "ready"
    attempts = [
        a for a in repo.rows(ResearchAsset) if a.content_kind == "planning_attempt"
    ]
    events = [e for e in repo.rows(ResearchEvent) if e.type == "planning_rejected"]
    if stop == "planning_failed":
        assert attempts and len(events) == 2 and not repo.rows(ResearchTask)
        assert run.result["survey_report"]["status"] == "partial"
        assert "规划" in run.result["survey_report"]["summary"]
        assert set(events[-1].payload) == {"stage", "code", "attempt", "issues"}
    elif stop != "resource_limit":
        assert (
            len(
                next(
                    a.inline_payload
                    for a in repo.rows(ResearchAsset)
                    if a.content_kind == "research_plan"
                )["requirement_ledger"]["main"]
            )
            == 2
        )
        assert len(events) == 1


def test_cross_question_clause_never_rebound():
    planner = Planner(persistent=True)
    original = planner.model

    async def model(system, data, **kw):
        result = await original(system, data, **kw)
        result["requests"][0]["question_id"] = "q1"
        result["queries"].append(
            {"question_id": "q1", "query": "bounded queue mechanism"}
        )
        result["reading_roles"].append({"question_id": "q1", "role": "mechanism"})
        result["targets"].append({"question_id": "q1", "name": "AlphaNet"})
        return result

    planner.model = model
    with pytest.raises(PlanningRejected, match="PLAN_SOURCE_QUESTION"):
        asyncio.run(
            make_anchored_plan(planner, [*specs(), {**specs()[0], "question_id": "q1"}])
        )
    assert len(planner.calls) == 2


env12 = source_env_fixture


@pytest.mark.parametrize("env12", ["question_loop_v12"], indirect=True)
def test_v12_reuses_reading_and_claim_gates_with_own_revision(env12, monkeypatch):
    repo, tools = env12
    monkeypatch.setattr(tools, "model", source_model(tools))
    stop = asyncio.run(Controller(tools).run())
    assert stop == "required_questions_supported"
    evidence = repo.rows(ResearchEvidence)
    answer = repo.rows(ResearchAnswerRevision)[0]
    assert evidence and all(e.verification["reviewer_version"] == "question_loop_v12" for e in evidence)
    assert answer.status == "supported" and answer.assessment["requirement_ledger"]
