"""Natural-language scope retention, bounded repairs and real v16 dispatch."""

import asyncio
from copy import deepcopy
import pytest
from pydantic import ValidationError
from sqlmodel import Session

from backend.models.exploration import ExplorationRun
from backend.models.research_core import ResearchAsset, ResearchTask, ResearchEvidence
from backend.services.deep_research.research_loop.intent_v16 import (
    GroupedPlan,
    PlanCheck,
    make_anchored_plan,
    PlanningRejected,
)
from backend.services.deep_research.research_loop.intent_v15 import (
    GroupedPlan as OldPlan,
)
from backend.services.deep_research.research_loop import worker
from backend.services.deep_research.research_loop.tools import Tools
from backend.services.deep_research.research_loop.repository import Repository
from backend.services.deep_research.research_loop.controller import Controller
from backend.services.deep_research.research_loop.dossier import freeze
from backend.services.deep_research.research_loop.writer import build_report
from backend.tests.test_research_planning_v13 import Planner
from backend.tests.test_research_intent_v9 import specs
from backend.tests.test_research_sources_v11 import env11 as source_fixture
from backend.tests.test_research_elements_v15 import model15

env16 = source_fixture


def test_generation_and_missing_request_schema_remove_only_redundant_labels():
    for schema, definition in [
        (GroupedPlan, "LocalRequest"),
        (PlanCheck, "MissingRequest"),
    ]:
        props = schema.model_json_schema()["$defs"][definition]["properties"]
        assert "mode" not in props and "phase" not in props
        assert {"clause_id", "requirement", "subject", "response_kind"} <= props.keys()
    assert {"mode", "phase"} <= OldPlan.model_json_schema()["$defs"]["LocalRequest"][
        "properties"
    ].keys()


@pytest.mark.parametrize(
    "text",
    [
        "说明AlphaNet无监督模式的正常样本训练和测试推理判据。",
        "Explain AlphaNet unsupervised training from normal examples and test-time scoring.",
        "解释AlphaNet有监督训练的异常标签作用和推理阶段冻结参数。",
        "说明AlphaNet的train phase如何构建正常参考及测试时怎样更新参数。",
    ],
)
def test_modes_and_training_test_qualifiers_remain_in_text_and_original_sources(text):
    question_specs = [
        {"question_id": "main", "text": text, "required": True, "answer_criteria": []}
    ]
    # Same literal source is the requirement; fields no longer reinterpret stages.
    planner = ScopePlanner()
    plan = asyncio.run(make_anchored_plan(planner, question_specs))
    unit = plan["requirement_ledger"]["main"][0]
    assert unit["requirement"] == unit["source_quote"] == text.rstrip("。")
    assert unit["mode"] == "" and unit["phase"] == "general"
    checked = planner.inputs[1]["requirements"][0]
    assert "phase" not in checked and "mode" not in checked
    assert checked["requirement"] == unit["requirement"]


class ScopePlanner:
    def __init__(self, lose_scope=False, persistent=False, extra_label=False):
        self.lose_scope, self.persistent, self.extra_label = (
            lose_scope,
            persistent,
            extra_label,
        )
        self.calls, self.inputs = [], []

    async def model(self, system, data, **kw):
        stage = kw["stage"]
        self.calls.append(stage)
        self.inputs.append(deepcopy(data))
        if stage in ("planning", "planning_repair"):
            groups = []
            for q in data["questions"]:
                rows = [
                    c
                    for c in data["source_clauses"]
                    if c["question_id"] == q["question_id"]
                ]
                requests = [
                    {
                        "clause_id": c["clause_id"],
                        "requirement": c["source_quote"],
                        "subject": "",
                        "response_kind": "fact",
                    }
                    for c in rows
                ]
                if self.lose_scope and (stage == "planning" or self.persistent):
                    requests[0]["requirement"] = "只说明正常样本训练"
                if self.extra_label and (stage == "planning" or self.persistent):
                    requests[0]["mode"] = "unsupervised"
                    requests[0]["phase"] = "training"
                groups.append(
                    {
                        "question_id": q["question_id"],
                        "queries": ["bounded queue survey"],
                        "reading_role": "overview",
                        "targets": [],
                        "requests": requests,
                    }
                )
            result = {
                "question_plans": groups,
                "answer_units": ["解释机制"],
                "reading_queries": [],
            }
        else:
            result = {
                "coverage": [],
                "unsupported_unit_ids": [],
                "missing_requests": [],
                "target_checks": [],
                "response_kinds": [],
            }
            for c in data["source_clauses"]:
                units = [
                    u for u in data["requirements"] if u["clause_id"] == c["clause_id"]
                ]
                covered = all(
                    u["requirement"] == c["source_quote"] for u in units
                ) and bool(units)
                result["coverage"].append(
                    {
                        "clause_id": c["clause_id"],
                        "kind": "request",
                        "covered": covered,
                        "unit_ids": [u["unit_id"] for u in units],
                        "reason": "Mode, training and testing scope checked against the source.",
                    }
                )
                if not covered:
                    result["missing_requests"].append(
                        {
                            "question_id": c["question_id"],
                            "clause_id": c["clause_id"],
                            "requirement": c["source_quote"],
                            "subject": "",
                            "response_kind": "fact",
                        }
                    )
            result["response_kinds"] = [
                {
                    "unit_id": u["unit_id"],
                    "response_kind": "fact",
                    "reason": "Existing mechanism",
                }
                for u in data["requirements"]
            ]
        return kw["schema"].model_validate(result).model_dump()


def scoped_specs():
    return [
        {
            "question_id": "main",
            "text": "说明无监督正常样本训练及测试推理评分。",
            "required": True,
            "answer_criteria": [],
        }
    ]


def test_training_only_plan_is_repaired_with_missing_test_scope_not_accepted():
    planner = ScopePlanner(lose_scope=True)
    plan = asyncio.run(make_anchored_plan(planner, scoped_specs()))
    assert planner.calls == [
        "planning",
        "planning_check",
        "planning_repair",
        "planning_check_repair",
    ]
    assert "测试推理评分" in plan["requirement_ledger"]["main"][0]["requirement"]
    assert (
        planner.inputs[2]["repair"]["review"]["missing_requests"][0]["requirement"]
        == "说明无监督正常样本训练及测试推理评分"
    )


def test_persistent_scope_loss_cannot_be_approved_by_empty_compatibility_mode():
    planner = ScopePlanner(lose_scope=True, persistent=True)
    with pytest.raises(PlanningRejected) as exc:
        asyncio.run(make_anchored_plan(planner, scoped_specs()))
    assert exc.value.code == "PLAN_INTENT_INCOMPLETE" and len(planner.calls) == 4


def test_obsolete_translated_label_gets_specific_schema_feedback_and_bounded_repair():
    planner = ScopePlanner(extra_label=True)
    plan = asyncio.run(make_anchored_plan(planner, scoped_specs()))
    assert plan["intent_coverage"]["complete"] and planner.calls == [
        "planning",
        "planning_repair",
        "planning_check_repair",
    ]
    errors = planner.inputs[1]["repair"]["field_errors"]
    assert {e["path"] for e in errors} == {
        "question_plans.0.requests.0.mode",
        "question_plans.0.requests.0.phase",
    }
    assert all(e["type"] == "extra_forbidden" for e in errors)


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "alias",
        "cross_question",
        "constraint_request",
        "unknown_review",
        "missing",
    ],
)
def test_existing_source_target_constraint_and_missing_request_gates_remain(fault):
    question_specs = [*specs(), {**specs()[0], "question_id": "q1"}]
    planner = Planner(fault)
    plan = asyncio.run(make_anchored_plan(planner, question_specs))
    for qid, units in plan["requirement_ledger"].items():
        assert len(units) == 2 and all(u["question_id"] == qid for u in units)
        assert all(u["mode"] == "" and u["phase"] == "general" for u in units)
        assert plan["constraints"][qid] == ["只研究有界队列，不做性能排名"]


@pytest.mark.parametrize(
    "fault,stop",
    [
        ("alias", "no_actionable_gap"),
        ("unknown_review", "planning_failed"),
        ("duplicate_question", "planning_failed"),
        ("budget", "resource_limit"),
    ],
)
def test_real_worker_selects_v16_and_stops_without_provider_or_lease_leak(
    engine, monkeypatch, fault, stop
):
    planner = Planner(fault, persistent=stop == "planning_failed")
    monkeypatch.setattr(worker, "get_llm_config", lambda: {})

    class OfflineTools(Tools):
        model = staticmethod(planner.model)

        async def search(self, *args, **kw):
            pytest.fail("No online provider in offline tests")

    with Session(engine) as s:
        row = ExplorationRun(
            question=specs()[0]["text"],
            depth="quick",
            workflow_version="academic_survey_v2",
            state_version=1,
            options={
                "search_online": False,
                "workflow": {"loop_revision": "question_loop_v16"},
            },
        )
        s.add(row)
        s.commit()
        rid = row.id
    asyncio.run(worker.run(rid, engine=engine, tools_factory=OfflineTools))
    repo = Repository(engine, rid, "inspection")
    assert repo.protocol()["execution_revision"] == "question_loop_v16"
    assert repo.run().worker_owner is None and repo.run().status == "partial"
    report = repo.run().result["survey_report"]
    assert report["method"]["stop_reason"] == stop
    if stop == "planning_failed":
        assert "规划" in report["summary"] and not repo.rows(ResearchTask)
    assert (
        any(a.content_kind == "planning_attempt" for a in repo.rows(ResearchAsset))
        or stop == "resource_limit"
    )


@pytest.mark.parametrize("env16", ["question_loop_v16"], indirect=True)
@pytest.mark.parametrize("fault", [None, "missing_score"])
def test_v16_keeps_real_v15_reader_element_checker_and_versioned_report(
    env16, monkeypatch, fault
):
    repo, tools = env16
    monkeypatch.setattr(tools, "model", model15(tools, fault))
    stop = asyncio.run(Controller(tools).run())
    assert repo.rows(ResearchEvidence)
    assert all(
        e.verification["reviewer_version"] == "question_loop_v16"
        and e.verification["source_attribution"] == "own"
        for e in repo.rows(ResearchEvidence)
    )
    report = build_report(tools, freeze(tools, stop))
    assert (
        report["workflow_revision"] == "question_loop_v16"
        and report["coverage"]["used"]
    )
    assert report["delivery"]["answer_status"] == ("partial" if fault else "complete")
