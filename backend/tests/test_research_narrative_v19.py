"""Unified checked prose, reusable support, complementary material and history guards."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.models.research_core import ResearchAsset, ResearchAnswerRevision
from backend.services.deep_research.research_loop.contracts import Action, LOOP_VERSION, SUPPORTED_LOOP_VERSIONS
from backend.services.deep_research.research_loop.coverage_v19 import element_complete, reusable_ids, render_units
from backend.services.deep_research.research_loop.answering_v15 import element_complete as previous_complete
from backend.services.deep_research.research_loop.answer_budget_v19 import envelope
from backend.services.deep_research.research_loop.controller import Controller
from backend.services.deep_research.research_loop.dossier import freeze
from backend.services.deep_research.research_loop.resources import limits_for
from backend.services.deep_research.research_loop.selection_v19 import bind_choices, review_choices
from backend.services.deep_research.research_loop.portfolio_v17 import bind_choices as previous_choices
from backend.services.deep_research.research_loop.writer import build_report, render_html
from backend.tests.test_research_sources_v11 import env11 as source_fixture, source_model
from backend.tests.test_research_portfolio_v17 import document, choice

env19 = source_fixture


def coverage_data():
    quote = "AlphaNet receives normal events, stores a bounded queue, and tests overflow by comparing count with capacity."
    known = {"ev_a": {"asset_id": "doc", "evidence_kind": "mechanism", "verification": {"source_attribution": "own", "mechanism_slot": "operation"}}}
    accepted = {"local": {"kind": "fact", "evidence_ids": ["ev_a"], "check": {"supported_by": [{"evidence_id": "ev_a", "quote": quote}]}},
                "other_unit": {"unit_id": "u2", "kind": "fact", "evidence_ids": ["ev_a"], "check": {"supported_by": [{"evidence_id": "ev_a", "quote": quote}]}}}
    review = {"scoring_chain_required": True, "missing_elements": [], "elements": [
        {"document_id": "doc", "kind": slot, "covered": True, "claim_ids": ["local" if slot != "scoring" else "other_unit"], "support_quote": quote}
        for slot in ("input", "operation", "scoring")
    ]}
    return known, accepted, review


def test_supported_content_reused_visibly_without_exclusive_navigation_slot():
    known, accepted, review = coverage_data()
    ids = reusable_ids(review, ["local"], accepted, {"response_kind": "fact"})
    assert ids == ["local", "other_unit"]
    assert element_complete(review, ids, accepted, known)
    assert not previous_complete(review, ["local"], accepted, known)


@pytest.mark.parametrize("fault", ["empty_quote", "invented_quote", "uncited", "wrong_document", "background", "unsupported", "missing_score", "missing_part"])
def test_complete_boolean_cannot_override_missing_local_original_support(fault):
    known, accepted, review = coverage_data()
    if fault == "empty_quote":
        review["elements"][-1]["support_quote"] = ""
    elif fault == "invented_quote":
        review["elements"][-1]["support_quote"] = "An invented test algorithm."
    elif fault == "uncited":
        review["elements"][-1]["claim_ids"] = ["unknown"]
    elif fault == "wrong_document":
        review["elements"][-1]["document_id"] = "other_doc"
    elif fault == "background":
        known["ev_a"]["verification"]["source_attribution"] = "background"
    elif fault == "unsupported":
        del accepted["other_unit"]
    elif fault == "missing_score":
        review["elements"].pop()
    else:
        review["elements"][-1]["covered"] = False
    ids = reusable_ids(review, ["local"], accepted, {"response_kind": "fact"})
    assert not element_complete(review, ids, accepted, known)


def test_reuse_does_not_turn_inference_or_unchecked_relations_into_facts():
    known, accepted, review = coverage_data()
    accepted["other_unit"]["kind"] = "inference"
    assert reusable_ids(review, ["local"], accepted, {"response_kind": "fact"}) == ["local"]
    accepted["other_unit"].update(kind="fact", relation_index=0)
    assert reusable_ids(review, ["local"], accepted, {"response_kind": "fact"}) == ["local"]


def test_representative_reading_allocation_changes_only_new_policy():
    docs = {f"D{i}": document(f"method{i}") for i in range(1, 7)}
    raw = {"representatives": [choice(ref) for ref in docs]}
    new, rejects = bind_choices(raw, docs, {"U1": "req"}, "after_coverage_search", [])
    old, old_rejects = previous_choices(raw, docs, {"U1": "req"}, "after_coverage_search", [])
    assert len(new) == 6 and not rejects
    assert len(old) == 4 and len(old_rejects) == 2


@pytest.mark.parametrize("fault", ["incompatible", "invented_quote", "duplicate_review", "missing_review"])
def test_independent_selection_scope_review_keeps_legal_representative(fault):
    docs = {"D1": document("normal"), "D2": document("mixed")}
    docs["D1"].inline_payload["paper"]["abstract"] = "AlphaNet manages queues of normal events."
    docs["D2"].inline_payload["paper"]["abstract"] = "BetaLog requires unrelated geometry inputs."
    choices, _ = bind_choices({"representatives": [choice("D1"), choice("D2")]}, docs, {"U1": "req"}, "after_coverage_search", [])

    async def model(system, data, **kw):
        rows = [{"document_ref": "D1", "eligible": True, "scope_quote": docs["D1"].inline_payload["paper"]["abstract"], "reason": "actual queue task"},
                {"document_ref": "D2", "eligible": fault != "incompatible", "scope_quote": docs["D2"].inline_payload["paper"]["abstract"], "reason": "requires excluded inputs"}]
        if fault == "invented_quote":
            rows[1]["scope_quote"] = "A fabricated task-specific description."
        elif fault == "duplicate_review":
            rows.append(deepcopy(rows[1]))
        elif fault == "missing_review":
            rows.pop()
        return kw["schema"].model_validate({"checks": rows}).model_dump()

    kept, rejected = asyncio.run(review_choices(SimpleNamespace(model=model), SimpleNamespace(text="Only queue methods"), {"scope_constraints": ["Only queue methods"]}, docs, choices))
    assert len(kept) == 1 and kept[0]["asset_id"] == "normal"
    assert rejected[0]["code"] == "PORTFOLIO_SCOPE_NOT_VERIFIED"


def model19(tools):
    base = source_model(tools)

    async def model(system, data, **kw):
        result = await base(system, data, **kw)
        if kw["stage"] == "extraction_batch":
            for f in result["facts"]:
                f.update(attribution="own", subject="AlphaNet", slot="operation")
        elif kw["stage"] == "evidence_check_batch":
            for row in result["checks"]:
                row.update(negative_explicit=False, attribution_preserved=True, slot_preserved=True)
        elif kw["stage"] == "answer_check":
            result.pop("conditions_preserved")
            result["scope_checks"] = []
            for row in result["claims"]:
                row["negative_explicit"] = False
            for unit in result["units"]:
                ids = [c["claim_id"] for c in data["claims"] if c.get("unit_id") == unit["unit_id"]]
                unit.update(scoring_chain_required=False, missing_elements=[], elements=[{"document_id": "", "kind": "other", "covered": True, "claim_ids": ids, "support_quote": data["sources"][0]["quote"]}])
        return kw["schema"].model_validate(result).model_dump()

    return model


@pytest.mark.parametrize("env19", ["question_loop_v19"], indirect=True)
def test_actual_controller_one_checked_body_freeze_save_and_safe_projection(env19, monkeypatch):
    repo, tools = env19
    monkeypatch.setattr(tools, "model", model19(tools))
    stop = asyncio.run(Controller(tools).run())
    assert stop == "required_questions_supported"
    answer = repo.rows(ResearchAnswerRevision)[0]
    assert "### 解释队列管理" in answer.conclusion and "| 必答内容" not in answer.conclusion
    dossier = freeze(tools, stop)
    calls = []

    async def forbidden_model(*args, **kwargs):
        calls.append(True)
        raise AssertionError("report must not regenerate research")

    monkeypatch.setattr(tools, "model", forbidden_model)
    report = build_report(tools, dossier)
    assert not calls and report["method"]["writer"] == "supported_narrative_projection_v2"
    assert report["method"]["requirements_checked"] == {"resolved": 2, "total": 2, "human_reviewed": False}
    assert report["summary"].startswith("AlphaNet") and report["dossier_hash"] == dossier.content_hash
    assert report["markdown"].count("## 原文核查材料") == 1
    html = render_html(report)
    assert "<h3>解释队列管理</h3>" in html and '<a href="#ref-1">[1]</a>' in html
    assert answer.assessment["human_reviewed"] is False
    assert report["workflow_revision"] == "question_loop_v19"


@pytest.mark.parametrize("env19", ["question_loop_v19"], indirect=True)
def test_unknown_frozen_body_reference_fails_instead_of_silently_disappearing(env19, monkeypatch):
    _, tools = env19
    monkeypatch.setattr(tools, "model", model19(tools))
    stop = asyncio.run(Controller(tools).run())
    dossier = freeze(tools, stop)
    dossier.manifest["answers"][0]["conclusion"] += "【ev_wrong_version】"
    with pytest.raises(ValueError, match="冻结证据版本"):
        build_report(tools, dossier)


@pytest.mark.parametrize("env19", ["question_loop_v19"], indirect=True)
def test_known_abstract_uses_one_actual_window_not_four_padded_windows(env19):
    _, tools = env19
    aid = tools.documents()[0].asset_id
    known = envelope(tools, "main", future_sources=1, future_assets=[aid])
    unknown = envelope(tools, "main", future_sources=1)
    assert known["llm_calls"] == unknown["llm_calls"] == 2
    assert 18000 <= known["llm_tokens"] < unknown["llm_tokens"]


@pytest.mark.parametrize("env19", ["question_loop_v19"], indirect=True)
@pytest.mark.parametrize("reject_reused", [False, True])
def test_real_assessment_reuse_is_visible_and_rejected_claim_cannot_fill_gap(env19, monkeypatch, reject_reused):
    repo, tools = env19
    plan = deepcopy(tools.plan())
    plan["requirement_ledger"]["main"][1]["response_kind"] = "fact"
    with repo.transaction() as session:
        for asset in repo.rows(ResearchAsset):
            if asset.content_kind == "research_plan":
                asset.status = "superseded"
                session.add(asset)
    tools.save_plan(plan)
    base = model19(tools)

    async def model(system, data, **kw):
        result = await base(system, data, **kw)
        if kw["stage"] == "answer":
            result["units"][1]["claims"][0]["text"] = "AlphaNet队列满时删除最旧条目。"
        elif kw["stage"] == "answer_check":
            first = next(c["claim_id"] for c in data["claims"] if c.get("unit_id") == "U1")
            result["units"][1]["elements"][0]["claim_ids"].append(first)
            if reject_reused:
                result["claims"][0]["supported"] = False
        return result

    monkeypatch.setattr(tools, "model", model)
    asyncio.run(Controller(tools).run())
    answer = repo.rows(ResearchAnswerRevision)[0]
    second = answer.assessment["requirement_ledger"][1]
    if reject_reused:
        assert not second["resolved"]
    else:
        assert second["resolved"] and len(second["accepted_claims"]) == 2
        assert any(c["unit_id"] == "req_queue" for c in second["accepted_claims"])
        second_body = answer.conclusion.split("### 提出核查建议", 1)[1]
        assert "AlphaNet保存有界队列" in second_body and "【ev_" in second_body


def test_quality_first_budget_is_revisioned_and_historical_caps_unchanged():
    assert LOOP_VERSION == "question_loop_v20" and "question_loop_v18" in SUPPORTED_LOOP_VERSIONS
    assert limits_for("quick", revision="question_loop_v18")["llm_tokens"] == 120000
    assert limits_for("quick", revision="question_loop_v19")["llm_tokens"] == 240000
    assert limits_for("quick", {"llm_calls": 12}, revision="question_loop_v19")["llm_calls"] == 12
    assert limits_for("quick")["llm_calls"] == 25


def test_partial_prose_keeps_supported_sentences_and_actual_missing_part():
    unit = {"requirement": "测试评分", "resolved": False, "accepted_claims": [{"text": "AlphaNet保存队列。", "kind": "fact", "evidence_ids": ["ev_a"]}], "check": {"missing_elements": ["实际溢出判据缺少依据"]}}
    body = render_units([unit])
    assert "AlphaNet保存队列。【ev_a】" in body and "实际溢出判据缺少依据" in body
