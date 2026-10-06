"""Real-source bindings, query limits and partial repairs cannot invent navigation."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.services.deep_research.research_loop.navigation_v20 import (
    TaskRecall,
    source_context,
    bind_hint,
    split_query_words,
)
from backend.services.deep_research.research_loop.portfolio_v20 import execute
from backend.services.deep_research.research_loop.contracts import Action
from backend.tests.test_research_sources_v11 import env11 as source_fixture

env20 = source_fixture


def test_phrase_items_only_split_english_whitespace_without_changing_content_or_bounds():
    original = {
        "task_searches": [hint(query_tokens=["image anomaly", "detection mechanism"])],
        "gaps": [],
    }
    value, changes = split_query_words(original, TaskRecall)
    assert value["task_searches"][0]["query_tokens"] == [
        "image",
        "anomaly",
        "detection",
        "mechanism",
    ]
    assert original["task_searches"][0]["query_tokens"] == [
        "image anomaly",
        "detection mechanism",
    ]
    assert changes[0]["word_count"] == 4
    for invalid in (["图像检测", "method"], ["word " * 9], ["image", "; curl"]):
        raw = {"task_searches": [hint(query_tokens=invalid)], "gaps": []}
        normalized, changes = split_query_words(raw, TaskRecall)
        assert normalized == raw and not changes
        with pytest.raises(ValidationError):
            TaskRecall.model_validate(normalized)


def context():
    return {
        "question": "解释图像检测机制；并讨论迁移条件",
        "requirements": [],
        "task_navigation": ["image anomaly detection", "transfer conditions"],
        "task_queries": ["image anomaly detection"],
        "completed_queries": [],
        "candidates": [],
    }


def requirements():
    return [
        {"unit_id": "a", "source_quote": "解释图像检测机制"},
        {"unit_id": "b", "source_quote": "讨论迁移条件"},
    ]


def hint(**updates):
    return {
        "unit_refs": ["U1"],
        "query_tokens": ["image", "mechanism"],
        "reason": "原需求补充",
        **updates,
    }


def test_separate_user_requests_bind_separate_original_intervals_without_joining_quotes():
    data = context()
    before = deepcopy(data)
    result = bind_hint(
        hint(unit_refs=["U1", "U2"]),
        data,
        {"U1": "a", "U2": "b"},
        requirements(),
        {},
        "task_recall",
    )
    assert data == before and result["query"] == "image mechanism"
    assert result["requirement_ids"] == ["a", "b"]
    for span in result["source_spans"]:
        assert data["question"][span["start"] : span["end"]] == span["quote"]
    assert result["source_quote"] == requirements()[0]["source_quote"]
    assert (
        "；" not in result["source_quote"]
        and result["navigation_origin"] == "user_task"
    )


@pytest.mark.parametrize(
    "updates,code",
    [
        ({"unit_refs": ["U99"]}, "PORTFOLIO_REQUIREMENT_REF"),
        ({"unit_refs": ["U1", "U1"]}, "PORTFOLIO_REQUIREMENT_REF"),
        ({"query_tokens": ["memory", "GPT"]}, "PORTFOLIO_TASK_VOCABULARY"),
        ({"query_tokens": ["image"]}, "PORTFOLIO_QUERY_BOUNDS"),
    ],
)
def test_unknown_requirements_words_and_bounds_are_rejected(updates, code):
    with pytest.raises(ValueError, match=code):
        bind_hint(
            hint(**updates), context(), {"U1": "a"}, requirements(), {}, "task_recall"
        )


@pytest.mark.parametrize(
    "words", [["image anomaly", "detection"], ["word"] * 9, ["图像", "detection"]]
)
def test_word_list_schema_rejects_sentences_overlong_lists_and_non_english(words):
    with pytest.raises(ValidationError):
        TaskRecall.model_validate(
            {"task_searches": [hint(query_tokens=words)], "gaps": []}
        )


def test_missing_user_quote_and_completed_query_cannot_be_recovered_by_defaults():
    data = context()
    units = {"U1": "a"}
    with pytest.raises(ValueError, match="TASK_ANCHOR"):
        bind_hint(
            hint(),
            data,
            units,
            [{"unit_id": "a", "source_quote": "invented request"}],
            {},
            "task_recall",
        )
    data["completed_queries"] = ["IMAGE mechanism"]
    with pytest.raises(ValueError, match="DUPLICATE_QUERY"):
        bind_hint(hint(), data, units, requirements(), {}, "task_recall")


def test_source_ids_bind_exact_document_spans_and_reject_non_original_windows():
    original = "preface " + "Normalizing Flow image anomaly detection. " * 25 + " end"
    quote = original[8:-4]
    asset = SimpleNamespace(
        asset_id="doc", content_hash="frozen-hash", inline_payload={"text": original}
    )
    windows = {
        "W1": {"asset_id": "doc", "quote": quote},
        "W2": {"asset_id": "doc", "quote": "invented window"},
    }
    data, sources = source_context(context(), windows, lambda aid: asset)
    assert "candidates" not in data and len(sources) == 2
    for source in sources.values():
        assert original[source["start"] : source["end"]] == source["quote"]
        assert (
            source["doc_content_hash"] == "frozen-hash" and source["asset_id"] == "doc"
        )
    row = hint(source_id="P1", query_tokens=["Normalizing", "Flow", "image", "anomaly"])
    result = bind_hint(
        row, data, {"U1": "a"}, requirements(), sources, "coverage_refresh"
    )
    assert (
        result["source_quote"] == sources["P1"]["quote"]
        and result["source_asset_id"] == "doc"
    )
    assert result["source_spans"][0]["start"] == 8
    with pytest.raises(ValueError, match="SOURCE_REF"):
        bind_hint(
            {**row, "source_id": "W2"},
            data,
            {"U1": "a"},
            requirements(),
            sources,
            "coverage_refresh",
        )
    with pytest.raises(ValueError, match="补搜只能组合"):
        bind_hint(
            {**row, "query_tokens": ["invented", "method"]},
            data,
            {"U1": "a"},
            requirements(),
            sources,
            "coverage_refresh",
        )


@pytest.mark.parametrize("env20", ["question_loop_v20"], indirect=True)
def test_partial_repair_retains_valid_navigation_and_never_creates_evidence(
    env20, monkeypatch
):
    from backend.models.research_core import ResearchAsset

    repo, tools = env20
    tools.resources.current_question_id = "main"
    calls = []
    plan = deepcopy(tools.plan())
    plan["requirement_ledger"]["main"][0]["source_quote"] = "解释AlphaNet怎样管理队列"
    monkeypatch.setattr(tools, "plan", lambda: plan)

    async def model(system, data, **kw):
        calls.append(deepcopy(data))
        valid = {
            "unit_refs": ["U1"],
            "query_tokens": ["queue", "operation"],
            "reason": "原任务操作",
        }
        bad = {**valid, "unit_refs": ["U99"]}
        replacement = {**valid, "query_tokens": ["queue", "implementation"]}
        return (
            kw["schema"]
            .model_validate(
                {
                    "task_searches": [valid, bad] if len(calls) == 1 else [replacement],
                    "gaps": [],
                }
            )
            .model_dump()
        )

    monkeypatch.setattr(tools, "model", model)
    result = asyncio.run(
        execute(
            tools, Action(tool="plan_reading", question_id="main", query="task_recall")
        )
    )
    payload = result["assets"][0]["inline_payload"]
    assert [h["query"] for h in payload["next_searches"]] == [
        "queue operation",
        "queue implementation",
    ]
    assert calls[1]["repair"]["accepted_queries"] == ["queue operation"]
    assert not tools.evidence("main")
    audits = [
        a.inline_payload
        for a in repo.rows(ResearchAsset)
        if a.content_kind == "portfolio_attempt"
    ]
    assert (
        len(audits) == 2
        and audits[0]["rejections"][0]["code"] == "PORTFOLIO_REQUIREMENT_REF"
    )


@pytest.mark.parametrize("env20", ["question_loop_v20"], indirect=True)
def test_repair_outage_preserves_already_valid_navigation(env20, monkeypatch):
    from backend.services.llm import LLMError

    repo, tools = env20
    plan = deepcopy(tools.plan())
    plan["requirement_ledger"]["main"][0]["source_quote"] = "解释AlphaNet怎样管理队列"
    monkeypatch.setattr(tools, "plan", lambda: plan)
    count = 0

    async def model(system, data, **kwargs):
        nonlocal count
        count += 1
        if count > 1:
            raise LLMError("LLM_PROVIDER_ERROR", "secret provider message")
        good = {
            "unit_refs": ["U1"],
            "query_tokens": ["queue", "operation"],
            "reason": "原任务操作",
        }
        return {"task_searches": [good, {**good, "unit_refs": ["U99"]}], "gaps": []}

    monkeypatch.setattr(tools, "model", model)
    result = asyncio.run(
        execute(
            tools, Action(tool="plan_reading", question_id="main", query="task_recall")
        )
    )
    payload = result["assets"][0]["inline_payload"]
    assert [h["query"] for h in payload["next_searches"]] == ["queue operation"]
    assert any(
        r["code"] == "PORTFOLIO_NAVIGATION_REPAIR_FAILED"
        for r in payload["navigation_rejections"]
    )
    assert "secret provider message" not in str(payload)
