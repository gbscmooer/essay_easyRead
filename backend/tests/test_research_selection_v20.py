"""Actual source identity is preserved without model text-copying."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.services.deep_research.research_loop.selection_v20 import (
    context_for,
    bind_checks,
)


def test_long_abstract_scope_windows_are_original_intervals_without_inserted_markers():
    docs, choices, _, _, _ = fixture()
    original = "Actual queue operation. " * 600 + "End of the original abstract."
    docs["D1"].inline_payload["paper"]["abstract"] = original
    data, bindings = context_for(
        SimpleNamespace(text="queue operation"),
        {"scope_constraints": [], "task_navigation": ["queue operation"]},
        docs,
        choices,
    )
    first = data["candidates"][0]
    assert sum(len(p["text"]) for p in first["scope_passages"]) <= 4000
    assert not any("[…省略…]" in p["text"] for p in first["scope_passages"])
    assert all(
        original[bindings[p["source_id"]]["start"] : bindings[p["source_id"]]["end"]]
        == p["text"]
        for p in first["scope_passages"]
    )


def fixture():
    docs = {
        f"D{i}": SimpleNamespace(
            asset_id=f"asset{i}",
            inline_payload={
                "paper": {
                    "title": f"Queue study {i}",
                    "abstract": f"Actual original queue operation {i}. "
                    + "Unicode 队列句。 " * 150,
                }
            },
        )
        for i in (1, 2)
    }
    choices = [{"asset_id": f"asset{i}", "reason": "Worth reading"} for i in (1, 2)]
    data, bindings = context_for(
        SimpleNamespace(text="Explain the original queue mechanism"),
        {"scope_constraints": ["Queue task"]},
        docs,
        choices,
    )
    raw = {
        "checks": [
            {
                "document_ref": row["document_ref"],
                "eligible": True,
                "scope_source_ids": [row["scope_passages"][0]["source_id"]],
                "reason": "Actual task",
            }
            for row in data["candidates"]
        ]
    }
    return docs, choices, data, bindings, raw


def test_windows_preserve_full_original_unicode_and_bind_verified_same_document():
    docs, choices, data, bindings, raw = fixture()
    for row in data["candidates"]:
        original = docs[row["document_ref"]].inline_payload["paper"]["abstract"]
        assert "".join(p["text"] for p in row["scope_passages"]) == original
        for p in row["scope_passages"]:
            span = bindings[p["source_id"]]
            assert original[span["start"] : span["end"]] == p["text"]
    selected, rejected = bind_checks(raw, bindings, data["candidates"], choices)
    assert not rejected and len(selected) == 2
    assert (
        selected[0]["selection_review"]["scope_quote"]
        == data["candidates"][0]["scope_passages"][0]["text"]
    )
    assert (
        selected[0]["selection_review"]["method"] == "program_bound_scope_passages_v20"
    )
    assert (
        "supported" not in selected[0]["selection_review"]
    )  # Scope admission is not final fact support.


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("cross_document", "source_identity"),
        ("invented", "source_identity"),
        ("duplicate_source", "duplicate_sources"),
        ("missing_sources", "missing_sources"),
        ("false", "ineligible"),
        ("duplicate_check", "check_count"),
    ],
)
def test_invalid_scope_identity_or_verdict_cannot_become_eligible(kind, expected):
    docs, choices, data, bindings, raw = fixture()
    row = raw["checks"][0]
    if kind == "cross_document":
        row["scope_source_ids"] = raw["checks"][1]["scope_source_ids"]
    elif kind == "invented":
        row["scope_source_ids"] = ["P404"]
    elif kind == "duplicate_source":
        row["scope_source_ids"] *= 2
    elif kind == "missing_sources":
        row["scope_source_ids"] = []
    elif kind == "false":
        row["eligible"] = False
    else:
        raw["checks"].append(deepcopy(row))
    selected, rejected = bind_checks(raw, bindings, data["candidates"], choices)
    assert [s["asset_id"] for s in selected] == ["asset2"]
    assert rejected[0]["failure_kind"] == expected and rejected[0]["check"] == row
