"""Real scoped synthesis, immutable-source ownership, and bounded budget regression."""

import asyncio
from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.models.research_core import ResearchAsset, ResearchAnswerRevision
from backend.services.deep_research.research_loop.source_refs_v14 import (
    wire_context,
    checker_payload,
    resolve_checks,
    source_payload,
)
from backend.services.deep_research.research_loop.answering_v14 import (
    ScopeCheck,
    scope_decisions,
    check_claims,
)
from backend.services.deep_research.research_loop.contracts import Action
from backend.services.deep_research.research_loop.controller import Controller
from backend.services.deep_research.research_loop.dossier import freeze
from backend.services.deep_research.research_loop.writer import build_report
from backend.services.deep_research.research_loop.answer_budget_v14 import envelope
from backend.services.deep_research.research_loop.answer_budget_v11 import (
    envelope as previous_envelope,
)
from backend.services.deep_research.research_loop.relations import checked_relations
from backend.tests.test_research_sources_v11 import (
    env11 as source_fixture,
    source_model,
)


def sample():
    quote = (
        "AlphaNet stores a bounded queue and drops the oldest entry when it is full."
    )
    known = {
        f"ev_{i}": {
            "evidence_id": f"ev_{i}",
            "asset_id": "doc1",
            "doc_content_hash": "version1",
            "spans": [{"quote": quote, "char_start": 0, "char_end": len(quote)}],
            "fact": quote,
            "source_kind": "full_text",
            "conditions": {},
            "missing_fields": [],
        }
        for i in range(1, 3)
    }
    papers = {eid: {"key": "paper1", "title": "AlphaNet queue"} for eid in known}
    units = [{"unit_id": "req1", "subject": "", "response_kind": "fact"}]
    return known, papers, units


def claim(cid, eid):
    return {
        "claim_id": cid,
        "unit_id": "req1",
        "text": "AlphaNet stores a bounded queue.",
        "kind": "fact",
        "evidence_ids": [eid],
    }


def review(cid, sid):
    return {
        "claim_id": cid,
        "supported": True,
        "relevant": True,
        "conditions_preserved": True,
        "support_mode": "direct",
        "supported_by": [sid],
        "unsupported_parts": [],
        "reason": "Direct source",
    }


def test_same_document_version_and_position_share_one_original_without_losing_owners():
    known, papers, units = sample()
    context = wire_context(units, known, papers)
    assert len(context["sources"]) == 1
    source = next(iter(context["sources"].values()))
    assert set(source["evidence_ids"]) == {"ev_1", "ev_2"}
    claims = [claim("c1", "ev_1"), claim("c2", "ev_2")]
    data, refs = checker_payload(claims, {"units": [], "relations": []}, context)
    assert len(data["sources"]) == 1 and "facts" not in data
    verdict = {
        "claims": [review("C1", "S1"), review("C2", "S1")],
        "units": [],
        "conflicts": [],
    }
    resolved = resolve_checks(verdict, context, refs)
    assert [r["supported_by"][0]["evidence_id"] for r in resolved["claims"]] == [
        "ev_1",
        "ev_2",
    ]
    accepted, rejected = check_claims(claims, resolved["claims"], known)
    assert len(accepted) == 2 and not rejected


@pytest.mark.parametrize("change", ["asset", "version", "position", "kind", "bytes"])
def test_equal_looking_originals_from_different_provenance_never_merge(change):
    known, papers, units = sample()
    if change == "asset":
        known["ev_2"]["asset_id"] = "doc2"
    elif change == "version":
        known["ev_2"]["doc_content_hash"] = "version2"
    elif change == "position":
        known["ev_2"]["spans"][0]["char_start"] = 99
    elif change == "kind":
        known["ev_2"]["source_kind"] = "abstract"
    else:
        known["ev_2"]["spans"][0]["quote"] += " "
    assert len(wire_context(units, known, papers)["sources"]) == 2


@pytest.mark.parametrize("bad_source", ["S2", "S404"])
def test_dedup_does_not_allow_borrowing_an_uncited_or_unknown_original(bad_source):
    known, papers, units = sample()
    known["ev_2"]["asset_id"] = "doc2"
    context = wire_context(units, known, papers)
    claims = [claim("c1", "ev_1")]
    data, refs = checker_payload(claims, {"units": [], "relations": []}, context)
    assert data["sources"][0]["evidence_refs"] == ["E1"] and len(data["sources"]) == 1
    checked = resolve_checks(
        {"claims": [review("C1", bad_source)], "units": [], "conflicts": []},
        context,
        refs,
    )
    accepted, rejected = check_claims(claims, checked["claims"], known)
    assert not accepted and rejected[0]["code"] == "unknown_source_ref"


@pytest.mark.parametrize("preserved,refs", [(False, []), (True, ["C1"])])
def test_global_false_without_a_concrete_violating_claim_is_not_a_valid_scope_check(
    preserved, refs
):
    with pytest.raises(ValidationError):
        ScopeCheck(
            constraint_id="K1",
            preserved=preserved,
            violating_claim_ids=refs,
            reason="test",
        )


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "unknown_scope", "unknown_claim"]
)
def test_scope_coverage_and_reference_errors_remain_explicit(fault):
    rows = [
        {
            "constraint_id": "K1",
            "preserved": True,
            "violating_claim_ids": [],
            "reason": "kept",
        }
    ]
    if fault == "missing":
        rows = []
    elif fault == "duplicate":
        rows *= 2
    elif fault == "unknown_scope":
        rows[0]["constraint_id"] = "K2"
    else:
        rows[0].update(preserved=False, violating_claim_ids=["C404"])
    _, issues = scope_decisions(rows, ["Only queues"], {"C1": "claim1"})
    assert issues


env14 = source_fixture


def model14(tools, fault=None):
    base = source_model(tools)

    async def model(system, data, **kw):
        result = await base(system, data, **kw)
        if kw["stage"] == "answer" and fault == "bad_optional_condition":
            result["conditions"] = [
                {
                    "text": "AlphaNet never loses any entry.",
                    "kind": "fact",
                    "evidence_ids": [data["facts"][0]["evidence_id"]],
                }
            ]
        if kw["stage"] == "answer_check":
            result.pop("conditions_preserved")
            result["scope_checks"] = [
                {
                    **s,
                    "preserved": True,
                    "violating_claim_ids": [],
                    "reason": "Within user scope",
                }
                for s in (
                    {"constraint_id": s["constraint_id"]}
                    for s in data["scope_constraints"]
                )
            ]
            if fault == "bad_optional_condition":
                row = next(
                    r
                    for r, c in zip(result["claims"], data["claims"])
                    if "condition_index" in c
                )
                row.update(
                    supported=False,
                    conditions_preserved=False,
                    supported_by=[],
                    unsupported_parts=["No loss guarantee"],
                )
            elif fault == "partial_unit":
                result["units"][0]["complete"] = False
            elif fault == "scope_violation":
                result["scope_checks"][0].update(
                    preserved=False, violating_claim_ids=[data["claims"][0]["claim_id"]]
                )
            elif fault == "missing_scope":
                result["scope_checks"] = []
        return kw["schema"].model_validate(result).model_dump()

    return model


@pytest.mark.parametrize("env14", ["question_loop_v14"], indirect=True)
@pytest.mark.parametrize("fault", [None, "bad_optional_condition", "partial_unit"])
def test_actual_controller_retains_supported_content_after_optional_failure(
    env14, monkeypatch, fault
):
    repo, tools = env14
    monkeypatch.setattr(tools, "model", model14(tools, fault))
    stop = asyncio.run(Controller(tools).run())
    answer = repo.rows(ResearchAnswerRevision)[0]
    ledger = answer.assessment["requirement_ledger"]
    assert all(u["accepted_claims"] for u in ledger) and answer.conclusion
    assert ledger[1]["kind"] == "inference"
    assert answer.assessment["raw_support_verdict"]["scope_checks"] == []
    assert any(
        a.content_kind == "support_check_attempt" for a in repo.rows(ResearchAsset)
    )
    if fault == "bad_optional_condition":
        assert (
            "never loses" not in answer.conclusion
            and answer.assessment["accepted_conditions"] == 0
        )
    elif fault == "partial_unit":
        assert not ledger[0]["resolved"] and ledger[1]["resolved"]
    report = build_report(tools, freeze(tools, stop))
    assert (
        report["coverage"]["used"] == 1
        and report["delivery"]["answer_status"] != "none"
    )


@pytest.mark.parametrize("env14", ["question_loop_v14"], indirect=True)
@pytest.mark.parametrize("fault", ["scope_violation", "missing_scope"])
def test_user_scope_failures_are_local_or_explicitly_unverified(
    env14, monkeypatch, fault
):
    repo, tools = env14
    original = deepcopy(tools.plan())
    original["constraints"]["main"] = ["Only queue processing, no performance ranking"]
    monkeypatch.setattr(tools, "plan", lambda: original)
    monkeypatch.setattr(tools, "model", model14(tools, fault))
    action = Action(
        tool="read_documents",
        question_id="main",
        asset_ids=[a.asset_id for a in tools.documents()],
    )
    repo.complete_task(*repo.claim_task(action), asyncio.run(tools.read_batch(action)))
    result = asyncio.run(tools.assess(Action(tool="assess_answer", question_id="main")))
    answer = result["answer"]
    ledger = answer["assessment"]["requirement_ledger"]
    if fault == "scope_violation":
        assert not ledger[0]["accepted_claims"] and ledger[1]["accepted_claims"]
        assert answer["assessment"]["scope_rejected_claim_ids"]
    else:
        assert answer["assessment"]["scope_check_issues"]
        assert not any(u["resolved"] for u in ledger) and not answer["conclusion"]


@pytest.mark.parametrize("env14", ["question_loop_v14"], indirect=True)
def test_budget_uses_deduplicated_originals_and_does_not_expand_caps(
    env14, monkeypatch
):
    _, tools = env14
    known, papers, _ = sample()
    quote = "AlphaNet stores a bounded queue. " * 65
    evidence = []
    for i in range(12):
        row = deepcopy(known["ev_1"])
        row.update(evidence_id=f"ev_{i}", spans=[{"quote": quote, "char_start": 0}])
        evidence.append(row)
    monkeypatch.setattr(tools, "evidence", lambda qid: evidence)
    monkeypatch.setattr(
        tools.repo,
        "asset",
        lambda aid: type("Asset", (), {"inline_payload": {"paper": papers["ev_1"]}})(),
    )
    previous, current = previous_envelope(tools, "main"), envelope(tools, "main")
    assert current["llm_tokens"] < previous["llm_tokens"]
    assert current["llm_calls"] == previous["llm_calls"] == 2
    assert tools.resources.limits["llm_tokens"] == 120000


@pytest.mark.parametrize(
    "predicate,explicit,expected",
    [
        ("method_difference", False, True),
        ("shared_task", False, True),
        ("uses", False, False),
    ],
)
def test_independent_comparison_needs_both_sides_but_explicit_adoption_needs_pointer(
    predicate, explicit, expected
):
    known, papers, _ = sample()
    known["ev_2"]["asset_id"] = "doc2"
    papers["ev_2"] = {"key": "paper2", "title": "BetaNet queue"}
    proposal = {
        "subject_asset_id": "doc1",
        "object_asset_id": "doc2",
        "predicate": predicate,
        "description": "Alpha drops old entries while Beta rejects new ones.",
        "scope": "Only queue mechanism",
        "subject_evidence_ids": ["ev_1"],
        "object_evidence_ids": ["ev_2"],
    }
    checked = {
        "index": 0,
        "supported": True,
        "relevant": True,
        "conditions_preserved": True,
        "explicit_reference": explicit,
        "reason": "Own evidence",
    }
    assert bool(checked_relations([proposal], [checked], known, papers)) == expected
    proposal["object_evidence_ids"] = ["ev_1"]
    assert not checked_relations([proposal], [checked], known, papers)
