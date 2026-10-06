"""Offline MiniMax final-content isolation through the shared LLM Core."""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from backend.models.research_core import ResearchBudgetEvent
from backend.services import llm
from backend.services.llm import client as core
from backend.services.llm.adapters import OpenAIChatAdapter
from backend.services.llm.errors import LLMResponseError
from backend.services.llm.json_utils import parse_json
from backend.services.deep_research.research_loop.intent_v20 import IntentDraft
from backend.tests.test_research_planning_v20 import OfflinePlanner
from backend.tests.test_research_v20_integration import environment


FINAL = {"clauses": [{"clause_id": "C1", "kind": "request", "requests": ["解释队列如何保持顺序"], "scope": [], "targets": []}]}
FAKE = {"clauses": [{"clause_id": "C404", "kind": "request", "requests": ["思考中的错误草稿"], "scope": [], "targets": ["InventedNet"]}]}
USAGE = {"prompt_tokens": 11, "completion_tokens": 29, "total_tokens": 40,
         "completion_tokens_details": {"reasoning_tokens": 23}}


def fake_wire(monkeypatch, content, reasoning=None, *, finish_reason="stop"):
    calls, closed = [], []

    class Completions:
        async def create(self, **kwargs):
            calls.append(deepcopy(kwargs))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content=content, reasoning_content=reasoning), finish_reason=finish_reason)],
                usage=deepcopy(USAGE), _request_id="req-minimax-offline")

    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=Completions())

        async def close(self):
            closed.append(True)

    monkeypatch.setattr("openai.AsyncOpenAI", Client)
    return calls, closed


def config(model="MiniMax-M2.7-highspeed"):
    return llm.LLMConfig.from_mapping({"provider": "custom", "api_format": "openai_chat",
        "api_key": "offline-key", "base_url": "https://wire-fixture.invalid/v1", "model": model})


def complete(extra_body=None):
    return asyncio.run(OpenAIChatAdapter().complete_once(config(), system="schema", user="request",
        max_tokens=4000, temperature=0, thinking_effort="off", extra_body=extra_body))


@pytest.mark.parametrize("prefix", [
    "<think>" + json.dumps(FAKE, ensure_ascii=False) + "</think>",
    " \n<think>[" + json.dumps(FAKE, ensure_ascii=False) + "]</think>\n<think>another draft [1,2]</think>\n",
    "<think>outer <think>inner fake {\"fake\": true}</think> outer final draft</think>\n",
])
def test_explicit_split_strips_only_complete_leading_reasoning_and_parses_final_object(monkeypatch, prefix):
    final = json.dumps(FINAL, ensure_ascii=False)
    calls, closed = fake_wire(monkeypatch, prefix + final, json.dumps(FAKE, ensure_ascii=False))
    response = complete({"reasoning_split": True})
    assert response.text == final
    assert parse_json(response.text) == FINAL
    assert response.usage == USAGE
    assert calls[0]["extra_body"] == {"reasoning_split": True}
    assert "thinking" not in calls[0]["extra_body"] and "reasoning_effort" not in calls[0]
    assert calls[0]["max_tokens"] == 4000
    assert closed == [True]


@pytest.mark.parametrize("content", [
    None, "", " \n ",
    "<think>{\"fake\": true}</think>",
    "<think>{\"fake\": true}",
    "<think>done draft</think><think>{\"unclosed\": true}",
    "<think>outer <think>inner</think>{\"unclosed_outer\": true}",
])
def test_split_never_promotes_reasoning_or_unclosed_thinking_into_an_answer(monkeypatch, content):
    calls, closed = fake_wire(monkeypatch, content, json.dumps(FINAL, ensure_ascii=False))
    with pytest.raises(LLMResponseError) as error:
        complete({"reasoning_split": True})
    assert error.value.code == "LLM_EMPTY_CONTENT"
    assert error.value.request_id == "req-minimax-offline"
    assert len(calls) == 1 and closed == [True]


def test_think_tags_inside_final_json_strings_are_preserved(monkeypatch):
    final = {"quote": "The literal <think>draft</think> tags describe a format, not a leading reasoning block."}
    fake_wire(monkeypatch, json.dumps(final), "irrelevant reasoning {\"fake\": true}")
    assert parse_json(complete({"reasoning_split": True}).text) == final


@pytest.mark.parametrize("extra_body", [None, {}, {"reasoning_split": False}, {"reasoning_split": "false"}])
def test_historical_adapter_default_keeps_its_original_content_and_reasoning_fallback(monkeypatch, extra_body):
    old = "<think>{\"draft\": true}</think>{\"final\": true}"
    fake_wire(monkeypatch, old, "old reasoning")
    assert complete(extra_body).text == old
    fake_wire(monkeypatch, "", "legacy reasoning content")
    assert complete(extra_body).text == "legacy reasoning content"


@pytest.mark.parametrize("revision,model,api_format,split", [
    ("question_loop_v20", "MiniMax-M2.7-highspeed", "openai_chat", True),
    ("question_loop_v20", "vendor/MiniMax-M2.7", "openai_chat", True),
    ("question_loop_v19", "MiniMax-M2.7-highspeed", "openai_chat", False),
    ("question_loop_v20", "other-model", "openai_chat", False),
    ("question_loop_v20", "MiniMax-M2.7-highspeed", "anthropic_messages", False),
])
def test_tools_enables_split_only_for_explicit_new_workflow_and_minimax_model(engine, monkeypatch, revision, model, api_format, split):
    _, tools, _ = environment(engine, revision)
    tools.config = {
        "provider": "custom", "api_format": api_format, "api_key": "offline-key",
        "base_url": "https://wire-fixture.invalid/v1", "model": model}
    captured = []

    async def offline(config_mapping, **kwargs):
        captured.append(deepcopy({key: value for key, value in kwargs.items() if key != "metadata_out"}))
        kwargs["metadata_out"]["finish_reason"] = "stop"
        return deepcopy(FINAL)

    monkeypatch.setattr(llm, "complete_json", offline)
    result = asyncio.run(tools.model("只保留原句", {}, schema=IntentDraft, stage="planning"))
    assert result == FINAL and len(captured) == 1
    assert captured[0]["extra_body"] == ({"reasoning_split": True} if split else None)
    assert captured[0]["thinking_effort"] == "off"


def test_tools_to_real_core_adapter_isolates_thinking_preserves_usage_and_charges_one_attempt(engine, monkeypatch):
    repo, tools, _ = environment(engine)
    tools.config = {"provider": "custom", "api_format": "openai_chat", "api_key": "offline-key",
        "base_url": "https://wire-fixture.invalid/v1", "model": "MiniMax-M2.7-highspeed"}
    fake = json.dumps(FAKE, ensure_ascii=False)
    final = json.dumps(FINAL, ensure_ascii=False)
    calls, closed = fake_wire(monkeypatch, f"<think>{fake}</think>{final}", fake)
    used = []
    monkeypatch.setattr(core, "_dispatch_usage", lambda config, usage, **kwargs: used.append(deepcopy(usage)))
    monkeypatch.setattr(core, "_dispatch_failure", lambda *args, **kwargs: None)
    original_budget, original_deadline = llm.llm_budget(), llm.llm_deadline()
    llm.set_llm_budget(tools.resources)
    llm.set_llm_deadline(None)
    try:
        result = asyncio.run(tools.model("严格输出原句", {"source_clauses": [{"clause_id": "C1", "source_quote": "解释队列如何保持顺序"}]},
            schema=IntentDraft, stage="planning", max_tokens=4000))
    finally:
        llm.set_llm_budget(original_budget)
        llm.set_llm_deadline(original_deadline)
    assert result == FINAL
    assert len(calls) == 1 and closed == [True]
    assert calls[0]["extra_body"] == {"reasoning_split": True}
    assert used == [USAGE]
    events = repo.rows(ResearchBudgetEvent)
    assert sum(event.delta_used for event in events if event.resource == "llm_calls") == 1
    assert sum(event.delta_used for event in events if event.resource == "llm_tokens") == 40


def test_reasoning_only_core_failure_does_not_trigger_json_recovery_or_zero_cost_accounting(engine, monkeypatch):
    repo, tools, _ = environment(engine)
    tools.config = {"provider": "custom", "api_format": "openai_chat", "api_key": "offline-key",
        "base_url": "https://wire-fixture.invalid/v1", "model": "MiniMax-M2.7-highspeed"}
    calls, closed = fake_wire(monkeypatch, "", json.dumps(FINAL, ensure_ascii=False))
    monkeypatch.setattr(core, "_dispatch_failure", lambda *args, **kwargs: None)
    original_budget, original_deadline = llm.llm_budget(), llm.llm_deadline()
    llm.set_llm_budget(tools.resources)
    llm.set_llm_deadline(None)
    try:
        with pytest.raises(LLMResponseError) as error:
            asyncio.run(tools.model("只输出最终对象", {}, schema=IntentDraft, stage="planning", max_tokens=4000))
    finally:
        llm.set_llm_budget(original_budget)
        llm.set_llm_deadline(original_deadline)
    assert error.value.code == "LLM_EMPTY_CONTENT"
    assert len(calls) == 1 and closed == [True]
    events = [event for event in repo.rows(ResearchBudgetEvent) if event.resource == "llm_tokens" and event.delta_used]
    assert len(events) == 1 and events[0].delta_used >= 4000
    assert events[0].usage_known is False and events[0].note == "unknown_upper_bound"


@pytest.mark.parametrize("failed_stage", ["planning", "planning_check", "planning_queries"])
def test_new_planner_keeps_empty_provider_response_as_model_error_without_semantic_repair(engine, monkeypatch, failed_stage):
    _, tools, specs = environment(engine)
    planner, stages = OfflinePlanner(), []

    async def model(system, data, **kwargs):
        stages.append(kwargs["stage"])
        if kwargs["stage"] == failed_stage:
            raise LLMResponseError("LLM_EMPTY_CONTENT", base_url="https://wire-fixture.invalid/v1",
                model="MiniMax-M2.7-highspeed", request_id="req-no-final-content")
        return await planner.model(system, data, **kwargs)

    monkeypatch.setattr(tools, "model", model)
    with pytest.raises(LLMResponseError) as error:
        asyncio.run(tools.make_plan(specs))
    assert error.value.code == "LLM_EMPTY_CONTENT"
    assert stages == ["planning", "planning_check", "planning_queries"][:["planning", "planning_check", "planning_queries"].index(failed_stage) + 1]


@pytest.mark.asyncio
async def test_empty_final_preserves_only_safe_finish_reason(monkeypatch):
    fake_wire(monkeypatch, "", "hidden reasoning", finish_reason="length")
    with pytest.raises(LLMResponseError) as caught:
        await OpenAIChatAdapter().complete_once(config(), system="schema", user="fixture", max_tokens=1800, temperature=0, thinking_effort="off", extra_body={"reasoning_split": True})
    assert caught.value.finish_reason == "length"
    assert "hidden reasoning" not in str(caught.value)
    unsafe = LLMResponseError("LLM_EMPTY_CONTENT", base_url="https://wire-fixture.invalid/v1", model="fixture", finish_reason="private response")
    assert unsafe.finish_reason is None and "private response" not in str(unsafe)

@pytest.mark.parametrize('code,status', [('LLM_RATE_LIMITED',429), ('LLM_AUTH_FAILED',401), ('LLM_PROVIDER_ERROR',None)])
def test_plain_provider_failure_is_diagnosed_once_without_format_retry_or_secret(engine, monkeypatch, code, status):
    from backend.models.research_core import ResearchEvent
    from backend.services.llm import LLMError
    repo, tools, _ = environment(engine)
    calls = []

    async def failed(*args, **kw):
        calls.append(kw)
        try:
            raise TimeoutError('secret-original-network-detail')
        except TimeoutError as cause:
            raise LLMError(code, 'secret-provider-message', status_code=status) from cause

    monkeypatch.setattr(llm, 'complete_json', failed)
    with pytest.raises(LLMError):
        asyncio.run(tools.model('schema', {}, stage='relevance'))
    events = [e.payload for e in repo.rows(ResearchEvent) if e.type=='structured_output_error']
    assert len(calls) == len(events) == 1
    assert events[0]['error_code'] == code and events[0]['failure_kind'] == 'timeout'
    if status:
        assert events[0]['http_status'] == status
    assert 'secret-' not in str(events)
