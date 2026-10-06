"""Offline source retention, local repair and independent navigation checks."""

import asyncio
from copy import deepcopy
import re

import pytest
from sqlmodel import Session

from backend.models.exploration import ExplorationRun
from backend.models.research_core import ResearchAsset
from backend.services.deep_research.research_loop import intent_v20 as intent
from backend.services.deep_research.research_loop import worker
from backend.services.deep_research.research_loop.controller import Controller
from backend.services.deep_research.research_loop.repository import Repository
from backend.services.deep_research.research_loop.tools import Tools


QUESTION = "解释队列如何保持顺序。给出新部署的核查建议。只讨论单线程，不做性能排名。"


def specs(text=QUESTION, **changes):
    return [{"question_id": "main", "text": text, "required": True, "answer_criteria": [], **changes}]


class OfflinePlanner:
    """Responds only from the supplied sources; never invokes an LLM client."""
    def __init__(self, faults=None, repo=None):
        self.faults = faults or {}
        if repo is not None:
            self.repo = repo
        self.calls, self.inputs, self.systems, self.results = [], [], [], []

    async def model(self, system, data, **kwargs):
        stage = kwargs["stage"]
        self.calls.append(stage)
        self.inputs.append(deepcopy(data))
        self.systems.append(system)
        if stage.startswith("planning_queries"):
            result = {"questions": []}
            for question in data["questions"]:
                queries = ["bounded queue survey", "queue ordering mechanism", "queue deployment conditions"]
                result["questions"].append({"question_ref": question["question_ref"], "queries": [
                    {"query": query} for query in queries
                ]})
        elif stage.startswith("planning_check"):
            result = {"clauses": []}
            for source in data["clauses"]:
                result["clauses"].append({
                    "clause_id": source["clause_id"], "requests_complete": True,
                    "scope_complete": True, "classification_correct": True,
                    "named_targets_complete": True, "targets_explicit": True,
                    "unsupported_unit_ids": [], "missing_details": [],
                    "response_kinds": [{"unit_id": row["unit_id"], "response_kind":
                        "inference" if re.search(r"核查建议|新场景|new production line", row["requirement"], re.I) else "fact"}
                        for row in source["requests"]], "reason": "原句请求已保留；尚未检索，不要求领域事实答案",
                })
        else:
            result = {"clauses": []}
            for source in data["source_clauses"]:
                quote = source["source_quote"]
                scope, requests = [], [quote]
                if quote.startswith(("只讨论", "不做", "Only discuss")):
                    scope, requests = [quote], []
                elif "，只讨论" in quote:
                    request, constraint = quote.split("，", 1)
                    scope, requests = [constraint], [request]
                result["clauses"].append({
                    "clause_id": source["clause_id"], "kind": "request" if requests else "constraint",
                    "requests": requests, "scope": scope,
                    "targets": [target for target in ("AlphaNet", "BetaNet", "MVTecAD") if target in quote],
                })
        if stage in self.faults:
            self.faults[stage](result, data)
        self.results.append(deepcopy(result))
        return result


def run(planner, question_specs=None):
    return asyncio.run(intent.make_anchored_plan(planner, question_specs or specs()))


def test_normal_three_stage_plan_freezes_original_scope_and_defers_reading_navigation():
    planner = OfflinePlanner()
    plan = run(planner)
    assert planner.calls == ["planning", "planning_check", "planning_queries"]
    assert plan["reading_queries"] == [] and plan["targets"] == []
    assert plan["answer_units"] == ["解释队列如何保持顺序", "给出新部署的核查建议"]
    units = plan["requirement_ledger"]["main"]
    assert [unit["response_kind"] for unit in units] == ["fact", "inference"]
    assert plan["constraints"]["main"] == ["只讨论单线程，不做性能排名"]
    assert len(plan["queries"]) == 3
    assert plan["intent_coverage"]["model_stage_calls"] == 3
    assert plan["intent_coverage"]["complete"] is True
    assert "question_id" not in intent.ClauseDraft.model_fields
    assert "unit_id" not in intent.ClauseDraft.model_fields
    assert "不要求领域事实答案" in plan["intent_coverage"]["reviews"][0]["clauses"][0]["reason"]
    for unit in units:
        assert QUESTION[unit["source_start"]:unit["source_end"]] == unit["source_quote"]


@pytest.mark.parametrize("text", [
    "说明AlphaNet无监督模式的正常样本训练和测试推理判据。",
    "Explain AlphaNet unsupervised training from normal examples and test-time scoring.",
    "解释AlphaNet有监督训练的异常标签作用和推理阶段冻结参数。",
    "说明AlphaNet的train phase如何构建正常参考及测试时怎样更新参数。",
])
def test_exact_source_retention_keeps_modes_data_roles_and_training_test_qualifiers(text):
    planner = OfflinePlanner()
    plan = run(planner, specs(text))
    unit = plan["requirement_ledger"]["main"][0]
    assert unit["requirement"] == unit["source_quote"] == text.rstrip("。")
    assert unit["response_kind"] == "fact"
    assert unit["subject"] == "AlphaNet"
    assert unit["mode"] == "" and unit["phase"] == "general"
    assert plan["targets"] == [{"question_id": "main", "name": "AlphaNet"}]
    assert "原句请求" in planner.inputs[1]["coverage_means"] or "用户请求" in planner.inputs[1]["coverage_means"]


def test_same_clause_range_is_frozen_without_becoming_a_literature_request():
    plan = run(OfflinePlanner(), specs("比较既有数据库事务隔离机制，只讨论单机部署。"))
    assert plan["requirement_ledger"]["main"][0]["requirement"] == "比较既有数据库事务隔离机制"
    assert plan["constraints"]["main"] == ["只讨论单机部署"]


def test_pronoun_clause_keeps_local_targets_empty_and_both_global_named_objects():
    text = "解释AlphaNet和BetaNet的正常输入与推理评分。比较两者的参考库构建方式。只讨论图像。"
    planner = OfflinePlanner()
    plan = run(planner, specs(text))
    assert planner.calls == ["planning", "planning_check", "planning_queries"]
    original = planner.results[0]["clauses"]
    assert original[0]["targets"] == ["AlphaNet", "BetaNet"]
    assert original[1]["targets"] == []
    assert original[1]["requests"] == ["比较两者的参考库构建方式"]
    assert plan["targets"] == [{"question_id": "main", "name": "AlphaNet"}, {"question_id": "main", "name": "BetaNet"}]
    review = planner.inputs[1]
    assert review["named_targets_by_question"] == {"main": ["AlphaNet", "BetaNet"]}
    pronoun = review["clauses"][1]
    assert pronoun["targets"] == [] and pronoun["requests"][0]["requirement"] == "比较两者的参考库构建方式"
    units = plan["requirement_ledger"]["main"]
    assert units[1]["requirement"] == units[1]["source_quote"] == "比较两者的参考库构建方式"
    assert plan["constraints"]["main"] == ["只讨论图像"]


def test_real_missing_qualifier_repairs_only_its_clause_and_keeps_other_clauses():
    question = "解释AlphaNet的正常图像训练与推理评分。给出新部署的核查建议。只讨论图像。"

    def lose(result, data):
        result["clauses"][0]["requests"] = ["解释AlphaNet的正常图像训练"]

    def diagnose(result, data):
        result["clauses"][0]["requests_complete"] = False
        result["clauses"][0]["missing_details"] = ["推理评分"]

    planner = OfflinePlanner({"planning": lose, "planning_check": diagnose})
    plan = run(planner, specs(question))
    assert planner.calls == ["planning", "planning_check", "planning_clause_repair", "planning_check_repair", "planning_queries"]
    patch_input = planner.inputs[2]
    assert patch_input["expected_source_refs"] == ["C1"]
    assert [source["clause_id"] for source in patch_input["source_clauses"]] == ["C1"]
    assert plan["requirement_ledger"]["main"][1]["requirement"] == "给出新部署的核查建议"
    assert plan["constraints"]["main"] == ["只讨论图像"]
    assert plan["intent_coverage"]["local_repairs"][0]["source_refs"] == ["C1"]
    assert plan["intent_coverage"]["reviews"][0]["clauses"][0]["requests_complete"] is False


def test_persistent_missing_request_fails_before_query_or_retrieval():
    def fault(result, data):
        result["clauses"][0]["requests_complete"] = False
        result["clauses"][0]["missing_details"] = ["保持顺序"]

    planner = OfflinePlanner({"planning_check": fault, "planning_check_repair": fault})
    with pytest.raises(intent.PlanningRejected, match="PLAN_INTENT_INCOMPLETE"):
        run(planner)
    assert "planning_queries" not in planner.calls


@pytest.mark.parametrize("fault,code", [("unknown", "PLAN_CHECK_UNKNOWN_UNIT"), ("cross", "PLAN_CHECK_CROSS_CLAUSE")])
def test_unknown_or_cross_clause_review_reference_is_never_normalized_away(fault, code):
    def corrupt(result, data):
        result["clauses"][0]["unsupported_unit_ids"] = ["R404" if fault == "unknown" else "R2"]

    planner = OfflinePlanner({"planning_check": corrupt, "planning_check_repair": corrupt})
    with pytest.raises(intent.PlanningRejected, match=code):
        run(planner)
    assert planner.calls == ["planning", "planning_check", "planning_check_repair"]


def test_invented_request_text_is_rejected_without_freezing_memory_fact():
    def corrupt(result, data):
        result["clauses"][0]["requests"] = ["队列用环形数组达到常数复杂度"]

    planner = OfflinePlanner({"planning": corrupt, "planning_repair": corrupt})
    with pytest.raises(intent.PlanningRejected, match="PLAN_SOURCE_TEXT"):
        run(planner)
    assert planner.calls == ["planning", "planning_repair"]


def test_source_bound_target_error_repairs_only_its_clause_and_preserves_legal_rows():
    def corrupt(result, data):
        result["clauses"][0]["targets"] = ["InventedNet"]

    planner = OfflinePlanner({"planning": corrupt})
    plan = run(planner)
    assert planner.calls == ["planning", "planning_repair", "planning_check", "planning_queries"]
    assert planner.inputs[1]["expected_source_refs"] == ["C1"]
    assert len(planner.inputs[1]["source_clauses"]) == 1
    assert plan["constraints"]["main"] == ["只讨论单线程，不做性能排名"]
    assert plan["requirement_ledger"]["main"][1]["requirement"] == "给出新部署的核查建议"


def test_literal_generic_topic_still_requires_independent_target_approval():
    def add_topic(result, data):
        result["clauses"][0]["targets"] = ["队列"]

    def reject_topic(result, data):
        result["clauses"][0]["targets_explicit"] = False

    planner = OfflinePlanner({"planning": add_topic, "planning_check": reject_topic})
    plan = run(planner)
    assert plan["targets"] == []
    assert planner.inputs[2]["expected_source_refs"] == ["C1"]


def test_query_field_failure_repairs_only_navigation_and_reports_exact_missing_path():
    def omit(result, data):
        del result["questions"][0]["queries"][0]["query"]

    planner = OfflinePlanner({"planning_queries": omit})
    plan = run(planner)
    assert planner.calls == ["planning", "planning_check", "planning_queries", "planning_queries_repair"]
    assert planner.inputs[3]["repair"]["field_errors"] == [{"path": "questions.0.queries.0.query", "type": "missing", "limits": {}}]
    assert plan["requirement_ledger"]["main"][0]["requirement"] == "解释队列如何保持顺序"
    assert "source_clauses" not in planner.inputs[3]


def test_bad_query_for_one_question_keeps_other_question_navigation_unchanged():
    def corrupt(result, data):
        result["questions"][1]["question_ref"] = "Q404"

    question_specs = [*specs("解释AlphaNet如何训练。"), {"question_id": "other", "text": "说明BetaNet推理判据。", "required": True, "answer_criteria": []}]
    planner = OfflinePlanner({"planning_queries": corrupt})
    plan = run(planner, question_specs)
    assert planner.inputs[-1]["expected_question_refs"] == ["Q2"]
    assert len(planner.inputs[-1]["questions"]) == 1
    original = [query["query"] for query in planner.results[2]["questions"][0]["queries"]]
    assert [query["query"] for query in plan["queries"] if query["question_id"] == "main"] == original
    assert {row["question_id"] for row in plan["queries"]} == {"main", "other"}


def test_missing_custom_criterion_is_not_dropped_or_marked_as_background():
    question_specs = specs("解释队列如何保持顺序。", answer_criteria=["说明内存不足时的行为"])

    def hide(result, data):
        result["clauses"][-1].update(kind="context", requests=[])

    def reject_hide(result, data):
        result["clauses"][-1]["classification_correct"] = False
        result["clauses"][-1]["requests_complete"] = False
        result["clauses"][-1]["missing_details"] = ["说明内存不足时的行为"]

    planner = OfflinePlanner({"planning": hide, "planning_check": reject_hide})
    plan = run(planner, question_specs)
    criteria = [unit for unit in plan["requirement_ledger"]["main"] if unit["source_origin"] == "criterion"]
    assert len(criteria) == 1 and criteria[0]["requirement"] == "说明内存不足时的行为"
    assert planner.inputs[2]["expected_source_refs"] == ["C2"]


def test_program_assigns_real_question_ownership_and_rejects_unknown_sources():
    def corrupt(result, data):
        result["clauses"][0]["clause_id"] = "C404"

    planner = OfflinePlanner({"planning": corrupt, "planning_repair": corrupt})
    with pytest.raises(intent.PlanningRejected, match="PLAN_SOURCE_ALIAS"):
        run(planner)


def test_normal_calls_use_bounded_outputs_and_cross_domain_positive_negative_examples():
    planner = OfflinePlanner()
    run(planner)
    assert 3 <= len(planner.inputs[0]["examples"]) <= 6
    assert "wrong" in planner.inputs[0]["examples"][0]
    assert "AlphaNet" in str(planner.inputs[0]["examples"])
    assert "数据库" in str(planner.inputs[0]["examples"])
    assert "当前没有检索或阅读论文" in planner.systems[1]
    assert "不预置领域技术路线名单" in planner.systems[2]


def test_original_failed_review_and_repaired_review_are_persisted_without_overwrite(engine):
    with Session(engine) as session:
        row = ExplorationRun(question=QUESTION, depth="quick", workflow_version="academic_survey_v2", state_version=1)
        session.add(row)
        session.commit()
        rid = row.id
    repo = Repository(engine, rid, "offline-planner")
    assert repo.acquire()

    def incomplete(result, data):
        result["clauses"][0]["scope_complete"] = False
        result["clauses"][0]["missing_details"] = ["保持顺序"]

    planner = OfflinePlanner({"planning_check": incomplete}, repo)
    plan = run(planner)
    assets = [asset.inline_payload for asset in repo.rows(ResearchAsset) if asset.content_kind == "planning_attempt"]
    reviews = [asset for asset in assets if asset["phase"].startswith("planning_check")]
    assert len(reviews) == 2
    assert reviews[0]["candidate"]["clauses"][0]["scope_complete"] is False
    assert reviews[1]["candidate"]["clauses"][0]["scope_complete"] is True
    assert plan["intent_coverage"]["complete"] is True
    assert len(assets) == 5


def test_explicit_global_stage_call_limit_is_fail_closed():
    calls = intent.Calls(OfflinePlanner())
    calls.used = 8
    with pytest.raises(intent.PlanningRejected, match="PLAN_CALL_BUDGET"):
        asyncio.run(calls.model("", {}, intent.IntentDraft, "planning", 4000))


def test_invalid_semantic_patch_fails_closed_and_does_not_start_query_stage():
    def incomplete(result, data):
        result["clauses"][0]["requests_complete"] = False

    def invalid_patch(result, data):
        result["clauses"][0]["requests"] = None

    planner = OfflinePlanner({"planning_check": incomplete, "planning_clause_repair": invalid_patch})
    with pytest.raises(intent.PlanningRejected, match="PLAN_STRUCTURED_OUTPUT"):
        run(planner)
    assert planner.calls == ["planning", "planning_check", "planning_clause_repair"]


def test_v20_worker_accepts_complete_contract_and_keeps_source_audit(engine, monkeypatch):
    planner = OfflinePlanner()
    monkeypatch.setattr(worker, "get_llm_config", lambda: {})

    class OfflineTools(Tools):
        model = staticmethod(planner.model)

        async def make_plan(self, question_specs):
            return await intent.make_anchored_plan(self, question_specs)

    async def inspect(self):
        plan = self.tools.plan()
        assert plan["intent_coverage"]["method"] == "source_request_navigation_v6"
        assert len(plan["requirement_ledger"]["main"]) == 2
        assert plan["constraints"]["main"] == ["只讨论单线程，不做性能排名"]
        assert plan["reading_queries"] == []
        return "no_actionable_gap"

    monkeypatch.setattr(Controller, "run", inspect)
    with Session(engine) as session:
        row = ExplorationRun(question=QUESTION, depth="quick", workflow_version="academic_survey_v2", state_version=1,
            options={"search_online": False, "workflow": {"loop_revision": "question_loop_v20"}})
        session.add(row)
        session.commit()
        rid = row.id
    asyncio.run(worker.run(rid, engine=engine, tools_factory=OfflineTools))
    repo = Repository(engine, rid, "inspection")
    assert repo.run().worker_owner is None
    assert repo.run().result["survey_report"]["method"]["stop_reason"] != "planning_failed"
    assets = repo.rows(ResearchAsset)
    plan = next(asset.inline_payload for asset in assets if asset.content_kind == "research_plan")
    assert plan["intent_coverage"]["complete"]
    audits = [asset.inline_payload for asset in assets if asset.content_kind == "planning_attempt"]
    assert [audit["phase"] for audit in audits] == ["planning", "planning_check", "planning_queries"]


def test_mixed_fact_and_advice_repairs_only_compound_clause_and_freezes_two_kinds():
    text = "解释队列的顺序机制，并给出新部署的核查建议。只讨论单线程。"
    def mixed(result, data):
        result["clauses"][0]["response_kinds"][0]["response_kind"] = "mixed"
    def split(result, data):
        assert data["expected_source_refs"] == ["C1"]
        result["clauses"][0]["requests"] = ["解释队列的顺序机制", "给出新部署的核查建议"]
    planner = OfflinePlanner({"planning_check": mixed, "planning_clause_repair": split})
    plan = run(planner, specs(text))
    assert planner.calls == ["planning", "planning_check", "planning_clause_repair", "planning_check_repair", "planning_queries"]
    assert [(r["requirement"],r["response_kind"]) for r in plan["requirement_ledger"]["main"]] == [("解释队列的顺序机制","fact"),("给出新部署的核查建议","inference")]
    assert plan["constraints"]["main"] == ["只讨论单线程"]
    assert all(r["response_kind"] != "mixed" for r in plan["requests"])


def test_unknown_subnumber_repair_receives_exact_per_clause_allowed_refs():
    def unknown(result, data):
        result["clauses"][0]["response_kinds"][0]["unit_id"] = "R1a"
    planner = OfflinePlanner({"planning_check": unknown})
    plan = run(planner)
    repair = planner.inputs[planner.calls.index("planning_check_repair")]
    assert repair["unit_refs_by_clause"] == {"C1":["R1"],"C2":["R2"],"C3":[]}
    assert repair["repair"]["issues"][0]["allowed_unit_refs"] == ["R1"]
    assert len(plan["requests"]) == 2


def test_queries_bind_all_current_question_requests_programmatically_without_llm_clause_ids():
    planner = OfflinePlanner()
    plan = run(planner)
    assert "source_ref" not in intent.SearchQuery.model_fields
    refs = plan["intent_coverage"]["query_source_refs_by_question"]["main"]
    assert refs == [row["clause_id"] for row in plan["requirement_ledger"]["main"]]
    assert all(ref != plan["intent_coverage"]["source_aliases"]["C3"] for ref in refs)


def test_context_only_optional_question_cannot_create_a_research_query():
    planner = OfflinePlanner()
    with pytest.raises(intent.PlanningRejected, match="PLAN_QUERY_SOURCE"):
        run(planner, [*specs(), {"question_id":"scope", "text":"只讨论单线程。", "required":False,"answer_criteria":[]}])
