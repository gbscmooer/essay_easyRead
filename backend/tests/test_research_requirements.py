"""Offline obligation/relationship regression with original source isolation."""
import asyncio
from copy import deepcopy

import pytest
from sqlmodel import Session

from backend.models.exploration import ExplorationRun
from backend.models.research_core import ResearchAsset, ResearchEvidence, ResearchQuestion, ResearchRelation
from backend.services.deep_research.research_loop.contracts import Action, AtomicFact, LOOP_VERSION
from backend.services.deep_research.research_loop.repository import Repository
from backend.services.deep_research.research_loop.resources import Resources, limits_for
from backend.services.deep_research.research_loop.questions import initial_questions
from backend.services.deep_research.research_loop.tools import Tools
from backend.services.deep_research.research_loop.evidence import validated_evidence
from backend.services.deep_research.research_loop.requirements import checked_units, requirement_units, GENERIC_POLICY
from backend.services.deep_research.research_loop.relations import checked_relations
from backend.services.deep_research.research_loop.dossier import freeze
from backend.services.deep_research.research_loop.writer import build_report, save_report
from backend.services.deep_research.research_loop.controller import Controller


@pytest.fixture
def requirement_env(engine):
    with Session(engine) as session:
        run = ExplorationRun(question="比较 AlphaNet 和 BetaNet 队列满的处理，只看截至2025年的资料，不做性能排名。",
            depth="quick", workflow_version="academic_survey_v2", state_version=1,
            options={"search_online": False, "workflow": {"loop_revision": "question_loop_v7"}})
        session.add(run)
        session.commit()
        run_id = run.id
    repo = Repository(engine, run_id, "v7-worker")
    assert repo.acquire()
    limits = limits_for("quick")
    repo.initialize(initial_questions(repo.run().question, {"answer_criteria": ["说明队列满的处理", GENERIC_POLICY]}), limits)
    tools = Tools(repo, Resources(repo, limits), {})
    with repo.transaction() as session:
        for name, behavior in (("AlphaNet", "drops the oldest entry"), ("BetaNet", "rejects the newest entry")):
            quote = f"{name} stores a bounded queue and {behavior} when the queue is full."
            payload = tools.document_spec({"key": name, "title": name + ": Bounded queues", "year": 2023,
                "abstract": quote, "primary_targets": [name]})
            payload["inline_payload"]["source_kind"] = "full_text"
            # Hash must describe the actual immutable payload, including source kind.
            from backend.services.deep_research.research_loop.contracts import digest
            payload["content_hash"] = digest(payload["inline_payload"])
            session.add(ResearchAsset(run_id=run_id, **payload))
    evidence = []
    for asset in tools.documents():
        quote = asset.inline_payload["text"]
        name = asset.inline_payload["paper"]["primary_targets"][0]
        ev = validated_evidence(run_id, "main", asset, AtomicFact(fact=name + "在队列满时" + (
            "丢弃最旧条目。" if name == "AlphaNet" else "拒绝最新条目。"), quote=quote),
            {"supported": True, "relevant": True}, reviewer_version=LOOP_VERSION)
        assert ev
        evidence.append(ev)
    repo.complete_task(*repo.claim_task(Action(tool="read_documents", question_id="main",
        asset_ids=[a.asset_id for a in tools.documents()])), {"evidence": evidence})
    tools.save_plan({"strategy": "material_first_v3", "queries": [],
        "targets": [{"question_id": "main", "name": n} for n in ("AlphaNet", "BetaNet")]})
    return repo, tools


def inputs(tools):
    units = tools.plan()["requirement_ledger"]["main"]
    known = {e["evidence_id"]: e for e in tools.evidence("main")}
    papers = {eid: tools.repo.asset(e["asset_id"]).inline_payload["paper"] for eid, e in known.items()}
    answers = [{"unit_id": u["unit_id"], "state": "answered", "text": e["fact"], "evidence_ids": [eid]}
               for u in units for eid, e in known.items() if u["subject"] in papers[eid]["primary_targets"]]
    checks = [{"unit_id": u["unit_id"], "supported": True, "complete": True, "relevant": True, "reason": "Explicit original condition"} for u in units]
    return units, known, papers, answers, checks


@pytest.mark.parametrize("uncertain", ["参数更新未知。", "当前提取证据未覆盖训练条件。", "没有针对目标域更新参数的证据。", "The condition is not reported."])
def test_unknown_requirement_cannot_pass_even_positive_model_check(requirement_env, uncertain):
    _, tools = requirement_env
    units, known, papers, answers, checks = inputs(tools)
    answers[0]["text"] = uncertain
    ledger = checked_units(units, answers, checks, known, papers)
    assert not ledger[0]["resolved"] and ledger[0]["gap"]
    assert ledger[1]["resolved"]


@pytest.mark.parametrize("change", ["omit", "duplicate", "wrong_source", "unknown_id", "incomplete", "irrelevant", "unknown_state"])
def test_missing_or_cross_source_obligation_remains_gap(requirement_env, change):
    _, tools = requirement_env
    units, known, papers, answers, checks = inputs(tools)
    if change == "omit":
        answers.pop(0)
    elif change == "duplicate":
        answers.append(answers[0].copy())
    elif change == "wrong_source":
        answers[0]["evidence_ids"] = answers[1]["evidence_ids"]
    elif change == "unknown_id":
        answers[0]["evidence_ids"] = ["ev_not_this_question"]
    elif change == "unknown_state":
        answers[0]["state"] = "unknown"
    else:
        checks[0]["complete" if change == "incomplete" else "relevant"] = False
    assert not checked_units(units, answers, checks, known, papers)[0]["resolved"]


def proposal(tools, predicate="method_difference"):
    _, known, _, answers, _ = inputs(tools)
    left, right = (known[a["evidence_ids"][0]] for a in answers)
    return {"subject_asset_id": left["asset_id"], "object_asset_id": right["asset_id"], "predicate": predicate,
        "description": "AlphaNet 丢弃最旧条目，BetaNet 拒绝新条目。", "scope": "队列已满",
        "subject_evidence_ids": [left["evidence_id"]], "object_evidence_ids": [right["evidence_id"]]}


def relation_check(**changes):
    return {"index": 0, "supported": True, "relevant": True, "conditions_preserved": True,
        "explicit_reference": True, "reason": "Both original passages", **changes}


@pytest.mark.parametrize("predicate", ["uses", "extends", "contradicts"])
def test_two_independent_descriptions_cannot_prove_explicit_relation(requirement_env, predicate):
    _, tools = requirement_env
    _, known, papers, _, _ = inputs(tools)
    assert not checked_relations([proposal(tools, predicate)], [relation_check()], known, papers)


def test_explicit_relation_requires_original_pointer_and_semantic_verdict(requirement_env):
    _, tools = requirement_env
    _, known, papers, _, _ = inputs(tools)
    known = deepcopy(known)
    p = proposal(tools, "extends")
    known[p["subject_evidence_ids"][0]]["spans"][0]["quote"] += " Our AlphaNet extends BetaNet."
    assert checked_relations([p], [relation_check()], known, papers)
    assert not checked_relations([p], [relation_check(explicit_reference=False)], known, papers)


@pytest.mark.parametrize("fault", ["side", "scope", "same_paper", "missing", "duplicate_check"])
def test_relations_need_two_distinct_sources_and_preserved_scope(requirement_env, fault):
    _, tools = requirement_env
    _, known, papers, _, _ = inputs(tools)
    p, checks = proposal(tools), [relation_check()]
    if fault == "side":
        p["subject_evidence_ids"] = p["object_evidence_ids"]
    elif fault == "scope":
        checks[0]["conditions_preserved"] = False
    elif fault == "same_paper":
        p["object_asset_id"] = p["subject_asset_id"]
        p["object_evidence_ids"] = p["subject_evidence_ids"]
    elif fault == "missing":
        p["object_evidence_ids"] = ["ev_unknown"]
    else:
        checks.append(checks[0].copy())
    assert not checked_relations([p], checks, known, papers)


def install_model(tools, monkeypatch, *, unknown=False, irrelevant=False, global_rejected=False):
    units, _, _, answers, checks = inputs(tools)
    if unknown:
        answers[0]["text"] += "目标域参数更新未知。"
    async def model(system, data, **kwargs):
        stage = kwargs["stage"]
        if stage == "answer":
            assert data["requirements"] == units
            assert "日期截止仅是材料上界" in system
            return {"units": answers, "paragraphs": [{"criterion_index": 0, "title": "不相关实验",
                "text": "医学实验准确率提升可以保证工业部署效果。", "evidence_ids": answers[0]["evidence_ids"], "kind": "fact"}] if irrelevant else [],
                "conditions": [], "relations": [proposal(tools)]}
        assert stage == "answer_check"  # A complete ledger needs no extra free-form coverage call.
        return {"units": checks, "checks": [{"supported": False, "relevant": False, "reason": "Out of task scope"}] if irrelevant else [],
            "relations": [relation_check()], "conditions_preserved": not global_rejected, "conflicts": []}
    monkeypatch.setattr(tools, "model", model)


def test_v7_controller_report_and_relations_are_bound_to_answer_revision(requirement_env, monkeypatch):
    repo, tools = requirement_env
    install_model(tools, monkeypatch)
    assert asyncio.run(Controller(tools).run()) == "required_questions_supported"
    relation = repo.rows(ResearchRelation)[0]
    first_revision = repo.rows(ResearchQuestion)[0].answer_revision_id
    assert relation.conditions["answer_revision_id"] == first_revision
    first = freeze(tools, "required_questions_supported")
    report = build_report(tools, first)
    assert report["workflow_revision"] == "question_loop_v7"
    assert "2 / 2" in report["summary"]
    assert len(report["references"]) == 2
    assert "方法差异" in report["chapters"][0]["text"]
    assert "\n\n**适用条件**\n\n- " in report["chapters"][0]["text"]
    assert len(report["chapters"][0]["relations"]) == 1
    assert all(u["resolved"] for u in report["chapters"][0]["requirement_ledger"])
    # Another answer uses the same material but cannot overwrite the old frozen relation.
    action = Controller(tools).assessment_action("main").model_copy(update={"query": "second-version"})
    result = asyncio.run(tools.assess(action))
    repo.complete_task(*repo.claim_task(action), result)
    second = freeze(tools, "required_questions_supported")
    assert len(repo.rows(ResearchRelation)) == 2
    assert len(second.manifest["relations"]) == 1
    assert second.manifest["relations"][0]["relation_id"] != first.manifest["relations"][0]["relation_id"]
    assert build_report(tools, first)["chapters"][0]["relations"] == report["chapters"][0]["relations"]
    save_report(tools, second, build_report(tools, second))
    assert repo.run().status == "completed"


def test_v7_false_complete_is_partial_and_preserves_other_answers(requirement_env, monkeypatch):
    _, tools = requirement_env
    install_model(tools, monkeypatch, unknown=True)
    result = asyncio.run(tools.assess(Controller(tools).assessment_action("main")))
    answer = result["answer"]
    assert answer["status"] == "insufficient"
    ledger = answer["assessment"]["requirement_ledger"]
    assert [r["resolved"] for r in ledger] == [False, True]
    assert "BetaNet" in answer["conclusion"] and "目标域参数更新未知" not in answer["conclusion"]
    assert answer["gaps"] and not answer["assessment"]["criteria_met"]


def test_v7_unrelated_experiment_rejected_without_erasing_valid_units(requirement_env, monkeypatch):
    _, tools = requirement_env
    install_model(tools, monkeypatch, irrelevant=True)
    result = asyncio.run(tools.assess(Controller(tools).assessment_action("main")))
    assert result["answer"]["status"] == "supported"
    assert "医学实验" not in result["answer"]["conclusion"]


def test_v7_global_condition_rejection_preserves_only_atomic_partial(requirement_env, monkeypatch):
    _, tools = requirement_env
    install_model(tools, monkeypatch, global_rejected=True)
    result = asyncio.run(tools.assess(Controller(tools).assessment_action("main")))
    assert result["answer"]["status"] == "insufficient"
    assert not result["relations"]
    assert not any(r["resolved"] for r in result["answer"]["assessment"]["requirement_ledger"])


def test_relation_dependency_quarantine_blocks_frozen_report(requirement_env, monkeypatch):
    repo, tools = requirement_env
    install_model(tools, monkeypatch)
    action = Controller(tools).assessment_action("main")
    repo.complete_task(*repo.claim_task(action), asyncio.run(tools.assess(action)))
    dossier = freeze(tools, "required_questions_supported")
    with repo.transaction() as session:
        asset = session.get(ResearchAsset, tools.documents()[0].id)
        asset.status = "quarantined"
        session.add(asset)
    with pytest.raises(ValueError, match="资产不可用"):
        build_report(tools, dossier)
    current = freeze(tools, "no_progress")
    assert not current.manifest["relations"]


def test_empty_evidence_keeps_all_requirements_unresolved(requirement_env):
    repo, tools = requirement_env
    with repo.transaction() as session:
        for e in repo.rows(ResearchEvidence):
            session.delete(session.get(ResearchEvidence, e.id))
    result = asyncio.run(tools.assess(Controller(tools).assessment_action("main")))
    assert result["answer"]["status"] == "insufficient"
    assert len(result["answer"]["assessment"]["requirement_ledger"]) == 2


def test_requirements_follow_named_criteria_without_forcing_other_method(requirement_env):
    repo, tools = requirement_env
    q = repo.rows(ResearchQuestion)[0].model_copy(update={"answer_criteria": ["说明 AlphaNet 的两种模式", "说明各方法机制", GENERIC_POLICY]})
    units = requirement_units(q, tools.plan())
    assert [(u["criterion_index"], u["subject"]) for u in units] == [(0, "AlphaNet"), (1, "AlphaNet"), (1, "BetaNet")]
    assert all("2025" not in u["requirement"] for u in units)
