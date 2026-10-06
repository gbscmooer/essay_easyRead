"""Format recovery cannot invent evidence or change a semantic verdict."""

import asyncio
from copy import deepcopy

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.models.research_core import ResearchEvent
from backend.services import llm
from backend.services.deep_research.research_loop.diagnostic_bounds_v20 import (
    bound_reasons,
)
from backend.services.deep_research.research_loop.selection_v20 import ScopeChecks
from backend.tests.test_research_v20_integration import environment


class Row(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(max_length=20)
    fact: str = Field(max_length=12)
    supported: bool


class Root(BaseModel):
    checks: list[Row]


class NullableChoice(BaseModel):
    classification: Row | None


def test_nullable_navigation_reason_is_bounded_without_filling_null_choice():
    raw = {
        "classification": {
            "reason": "long" * 20,
            "fact": "Actual fact",
            "supported": False,
        }
    }
    result, audit = bound_reasons(raw, NullableChoice)
    assert len(result["classification"]["reason"]) == 20
    assert audit[0]["path"] == "classification.reason"
    assert result["classification"]["supported"] is False
    assert bound_reasons({"classification": None}, NullableChoice) == (
        {"classification": None},
        [],
    )


def test_diagnostics_only_copy_bounded_text_and_audit_hash_without_response_text():
    raw = {
        "checks": [
            {"reason": "详细诊断" * 30, "fact": "Actual fact", "supported": False}
        ]
    }
    before = deepcopy(raw)
    value, audit = bound_reasons(raw, Root)
    assert raw == before and value["checks"][0]["supported"] is False
    assert value["checks"][0]["fact"] == raw["checks"][0]["fact"]
    assert len(value["checks"][0]["reason"]) == 20
    assert audit[0]["path"] == "checks.0.reason" and audit[0]["original_length"] == 120
    assert set(audit[0]) == {"path", "limit", "original_length", "original_hash"}
    assert len(audit[0]["original_hash"]) == 64
    Root.model_validate(value)


@pytest.mark.parametrize(
    "fault", ["fact", "missing_verdict", "extra", "missing_sources"]
)
def test_invalid_facts_verdicts_and_missing_source_fields_remain_invalid(fault):
    raw = {
        "checks": [{"reason": "long" * 30, "fact": "Actual fact", "supported": False}]
    }
    schema = Root
    if fault == "fact":
        raw["checks"][0]["fact"] = "oversized factual statement" * 10
    elif fault == "missing_verdict":
        del raw["checks"][0]["supported"]
    elif fault == "extra":
        raw["checks"][0]["invented"] = True
    else:
        schema = ScopeChecks
        raw = {
            "checks": [{"document_ref": "D1", "eligible": True, "reason": "long" * 100}]
        }
    value, _ = bound_reasons(raw, schema)
    with pytest.raises(ValidationError):
        schema.model_validate(value)


@pytest.mark.parametrize("revision", ["question_loop_v20", "question_loop_v19"])
def test_actual_tools_recovery_is_v20_only_and_does_not_promote_false_verdict(
    engine, monkeypatch, revision
):
    repo, tools, _ = environment(engine, revision)
    calls = []

    async def complete(*args, **kwargs):
        calls.append(True)
        return {
            "checks": [
                {
                    "document_ref": "D1",
                    "eligible": False,
                    "scope_source_ids": [],
                    "reason": "diagnostic explanation " * 100,
                }
            ]
        }

    monkeypatch.setattr(llm, "complete_json", complete)
    if revision == "question_loop_v20":
        value = asyncio.run(
            tools.model(
                "check", {}, schema=ScopeChecks, stage="reading_selection_check"
            )
        )
        assert len(calls) == 1 and value["checks"][0]["eligible"] is False
        audit = [
            e.payload
            for e in repo.rows(ResearchEvent)
            if e.type == "structured_output_recovery"
        ]
        assert audit[0]["kind"] == "diagnostic_reason_bound"
    else:
        with pytest.raises(ValidationError):
            asyncio.run(
                tools.model(
                    "check", {}, schema=ScopeChecks, stage="reading_selection_check"
                )
            )
        assert len(calls) == 2
        assert not [
            e
            for e in repo.rows(ResearchEvent)
            if e.type == "structured_output_recovery"
        ]
