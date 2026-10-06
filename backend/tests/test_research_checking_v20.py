"""Small checks isolate their own sources and cannot bypass final semantic guards."""

import asyncio
from copy import deepcopy

import pytest

from backend.models.research_core import ResearchAsset
from backend.services.deep_research.research_loop.checking_v20 import (
    check,
    batches,
    BATCH_SIZE,
    CLAIM_OUTPUT,
    COVERAGE_OUTPUT,
)
from backend.services.llm import LLMError
from backend.tests.test_research_sources_v11 import env11 as source_fixture

env20 = source_fixture


def data(count=13):
    return {
        "question": "Explain the supplied methods.",
        "requirements": [{"unit_id": "U1"}],
        "claims": [
            {
                "claim_id": f"C{i}",
                "unit_id": "U1",
                "text": "Source-grounded claim",
                "kind": "fact",
                "evidence_ids": [f"E{i}"],
            }
            for i in range(1, count + 1)
        ],
        "sources": [
            {
                "source_id": f"S{i}",
                "evidence_refs": [f"E{i}"],
                "quote": f"Original source {i}",
            }
            for i in range(1, count + 1)
        ],
        "documents": [],
        "evidence_navigation": [],
        "scope_constraints": [],
        "relations": [],
    }


def review(claim, supported=True):
    i = claim["claim_id"][1:]
    return {
        "claim_id": claim["claim_id"],
        "supported": supported,
        "negative_explicit": False,
        "relevant": True,
        "conditions_preserved": True,
        "support_mode": "direct" if supported else "unsupported",
        "supported_by": [f"S{i}"] if supported else [],
        "unsupported_parts": [] if supported else ["Missing operation"],
        "added_information": False,
        "reason": "Only original source",
    }


def test_claim_groups_have_only_their_own_source_windows_and_preserve_ids():
    original = data()
    before = deepcopy(original)
    groups = batches(original)
    assert original == before and [len(g["claims"]) for g in groups] == [6, 6, 1]
    for group in groups:
        assert len(group["sources"]) == len(group["claims"])
        assert {s["source_id"][1:] for s in group["sources"]} == {
            c["claim_id"][1:] for c in group["claims"]
        }
    # Reservation is conservative before the generated claim-to-evidence links exist.
    assert all(len(g["sources"]) == 13 for g in batches(original, prospective=True))


@pytest.mark.parametrize("env20", ["question_loop_v20"], indirect=True)
def test_all_batches_precede_coverage_and_false_verdict_stays_false(env20, monkeypatch):
    repo, tools = env20
    calls = []

    async def model(system, value, **kwargs):
        calls.append((kwargs["stage"], deepcopy(value), kwargs["max_tokens"]))
        if kwargs["stage"] == "answer_claim_check":
            return {
                "claims": [
                    review(c, supported=c["claim_id"] != "C2") for c in value["claims"]
                ]
            }
        assert len(value["claim_checks"]) == 13
        assert value["claim_checks"][1]["supported"] is False
        return {"units": [], "relations": [], "scope_checks": [], "conflicts": []}

    monkeypatch.setattr(tools, "model", model)
    result = asyncio.run(check(tools, data()))
    assert [c[0] for c in calls] == ["answer_claim_check"] * 3 + [
        "answer_coverage_check"
    ]
    assert [c[2] for c in calls] == [CLAIM_OUTPUT] * 3 + [COVERAGE_OUTPUT]
    assert len(result["claims"]) == 13 and not result["claims"][1]["supported"]
    assert not tools.evidence("main")
    audits = [
        a for a in repo.rows(ResearchAsset) if a.content_kind == "answer_check_batch"
    ]
    assert len(audits) == 4


@pytest.mark.parametrize("env20", ["question_loop_v20"], indirect=True)
@pytest.mark.parametrize("fault", ["missing", "duplicate", "wrong_source", "provider"])
def test_second_batch_failure_preserves_first_audit_and_does_not_claim_coverage(
    env20, monkeypatch, fault
):
    repo, tools = env20
    stages = []

    async def model(system, value, **kwargs):
        stages.append(kwargs["stage"])
        rows = [review(c) for c in value["claims"]]
        if len(stages) == 2:
            if fault == "provider":
                raise LLMError("LLM_PROVIDER_ERROR", "secret request")
            if fault == "missing":
                rows.pop()
            elif fault == "duplicate":
                rows[-1] = deepcopy(rows[0])
            else:
                rows[0]["supported_by"] = ["S1"]  # C7 never cited E1.
        return {"claims": rows}

    monkeypatch.setattr(tools, "model", model)
    with pytest.raises((ValueError, LLMError)):
        asyncio.run(check(tools, data()))
    assert stages == ["answer_claim_check"] * 2
    audits = [
        a.inline_payload
        for a in repo.rows(ResearchAsset)
        if a.content_kind == "answer_check_batch"
    ]
    assert len(audits) == 2 and len(audits[0]["candidate"]["claims"]) == BATCH_SIZE
    assert audits[1]["error_code"]
    assert "secret request" not in str(audits)
