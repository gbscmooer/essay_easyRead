"""Typed aliases must never turn a nearby or incorrectly selected source into support."""

import asyncio
from copy import deepcopy

from backend.services import llm
from backend.services.deep_research.research_loop.answering_v19 import Answer, Verdict
from backend.services.deep_research.research_loop.answer_wire_v20 import (
    request_contract,
    source_reference_errors,
)
from backend.services.deep_research.research_loop.answer_budget_v19 import model_cost
from backend.tests.test_research_v20_integration import environment


def context():
    return {
        "requirements": [{"unit_id": "U1"}],
        "documents": [{"asset_id": "D1"}],
        "facts": [{"evidence_id": "E1"}, {"evidence_id": "E2"}],
        "claims": [{"claim_id": "C1", "evidence_ids": ["E2"]}],
        "sources": [
            {"source_id": "S1", "evidence_refs": ["E1", "E2"]},
            {"source_id": "S2", "evidence_refs": ["E1"]},
        ],
        "scope_constraints": [{"constraint_id": "K1"}],
    }


def verdict(supported_by):
    return {
        "claims": [
            {
                "claim_id": "C1",
                "supported": False,
                "negative_explicit": False,
                "relevant": True,
                "conditions_preserved": True,
                "support_mode": "unsupported",
                "supported_by": supported_by,
                "unsupported_parts": ["原文不足"],
                "reason": "原文不足",
            }
        ],
        "units": [],
        "relations": [],
        "scope_checks": [],
        "conflicts": [],
    }


def test_schema_uses_real_catalog_and_source_choices_are_claim_local_without_mutation():
    data = context()
    before = deepcopy(data)
    spec, wire = request_contract(Verdict, data)
    assert data == before
    assert spec["$defs"]["ClaimCheck"]["properties"]["supported_by"]["items"][
        "enum"
    ] == ["S1", "S2"]
    assert wire["claims"][0]["allowed_support_sources"] == ["S1"]
    assert spec["$defs"]["ClaimCheck"]["properties"]["claim_id"]["enum"] == ["C1"]
    answer, _ = request_contract(Answer, data)
    assert answer["$defs"]["Claim"]["properties"]["evidence_ids"]["items"]["enum"] == [
        "E1",
        "E2",
    ]
    assert (
        "enum"
        not in Verdict.model_json_schema()["$defs"]["ClaimCheck"]["properties"][
            "supported_by"
        ]["items"]
    )


def test_e_label_and_uncited_s_are_rejected_without_translation_and_false_is_preserved():
    for invalid in ("E2", "S2", "S999"):
        raw = verdict([invalid])
        before = deepcopy(raw)
        issues = source_reference_errors(raw, context())
        assert issues == [
            {
                "path": "claims.0.supported_by",
                "type": "source_reference",
                "allowed": ["S1"],
            }
        ]
        assert raw == before and not raw["claims"][0]["supported"]
    assert not source_reference_errors(verdict(["S1"]), context())
    assert not source_reference_errors(verdict([]), context())


def test_native_reservation_uses_the_same_enriched_schema_and_input():
    from backend.services.llm.structured import result_tool, tool_input_bytes
    import json

    data = context()
    spec, wire = request_contract(Verdict, data)
    size = len(
        ("sys\n严格遵守 JSON schema：" + json.dumps(spec, ensure_ascii=False)).encode()
    )
    size += len(json.dumps(wire, ensure_ascii=False, default=str).encode())
    size += tool_input_bytes({"tools": [result_tool(spec)]})
    assert (
        model_cost("sys", data, Verdict, 9000, native_result=True)
        == 16 + (size + 2) // 3 + 9000
    )


def test_new_native_checker_repairs_only_explicit_source_reference_error(
    engine, monkeypatch
):
    _, tools, _ = environment(engine, "question_loop_v20")
    tools.config["model"] = "MiniMax-M2.7-highspeed"
    sent = []

    async def fake(*args, **kwargs):
        sent.append(kwargs)
        return verdict(["E2"] if len(sent) == 1 else [])

    monkeypatch.setattr(llm, "complete_json", fake)
    result = asyncio.run(
        tools.model("check", context(), schema=Verdict, stage="answer_check")
    )
    assert len(sent) == 2 and "source_reference_contract" in sent[1]["system"]
    assert (
        not result["claims"][0]["supported"] and not result["claims"][0]["supported_by"]
    )
    assert "allowed_support_sources" in sent[0]["user"]


def test_historical_checker_keeps_original_request_and_post_check_guards(
    engine, monkeypatch
):
    _, tools, _ = environment(engine, "question_loop_v19")
    tools.config["model"] = "MiniMax-M2.7-highspeed"
    sent = []

    async def fake(*args, **kwargs):
        sent.append(kwargs)
        return verdict(["E2"])

    monkeypatch.setattr(llm, "complete_json", fake)
    result = asyncio.run(
        tools.model("check", context(), schema=Verdict, stage="answer_check")
    )
    assert len(sent) == 1 and "allowed_support_sources" not in sent[0]["user"]
    assert result["claims"][0]["supported_by"] == ["E2"]
