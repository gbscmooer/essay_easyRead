"""MiniMax's explicit structured channel remains schema checked and accounted."""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from backend.services import llm
from backend.services.llm.adapters import OpenAIChatAdapter
from backend.services.llm.structured import (
    RESULT_FUNCTION,
    result_tool,
    tool_input_bytes,
    unwrap_result,
)
from backend.services.deep_research.research_loop.answering_v19 import (
    Answer,
    CHECK_SYSTEM,
    Verdict,
)
from backend.services.deep_research.research_loop.answer_budget_v19 import model_cost
from backend.tests.test_research_v20_integration import environment
from backend.tests.test_minimax_workflow_protocol import config, USAGE


def wire(
    monkeypatch,
    *,
    calls=None,
    content=None,
    finish_reason="tool_calls",
    response_text=None,
):
    sent = []

    class Completions:
        async def create(self, **kwargs):
            sent.append(deepcopy(kwargs))
            if response_text is not None:
                return response_text
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=content,
                            reasoning_content="reasoning is never a result",
                            tool_calls=calls,
                        ),
                        finish_reason=finish_reason,
                    )
                ],
                usage=USAGE,
                _request_id="native-offline",
            )

    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=Completions())

        async def close(self):
            pass

    monkeypatch.setattr("openai.AsyncOpenAI", Client)
    return sent


def returned(args, name=RESULT_FUNCTION):
    return SimpleNamespace(
        type="function", function=SimpleNamespace(name=name, arguments=args)
    )


def request():
    return {"reasoning_split": True, "tools": [result_tool(Answer.model_json_schema())]}


def complete():
    return asyncio.run(
        OpenAIChatAdapter().complete_once(
            config(),
            system="schema",
            user="input",
            max_tokens=6000,
            temperature=0,
            thinking_effort="off",
            extra_body=request(),
        )
    )


def test_only_declared_tool_arguments_are_returned_unchanged_with_real_usage(
    monkeypatch,
):
    data = {"units": [], "explanations": [], "conditions": [], "relations": []}
    args = json.dumps(data, ensure_ascii=False)
    sent = wire(
        monkeypatch,
        calls=[returned(args)],
        content="<think>untrusted reasoning and alternate output</think>",
    )
    out = complete()
    assert out.text == args and out.usage == USAGE and out.finish_reason == "tool_calls"
    assert sent[0]["extra_body"]["tools"] == request()["tools"]
    assert "tool_choice" not in sent[0]["extra_body"]
    Answer.model_validate_json(out.text)


@pytest.mark.parametrize(
    "fault", ["missing", "multiple", "wrong_name", "empty", "text_only", "raw_text"]
)
def test_missing_or_ambiguous_return_never_uses_text_or_executes_another_tool(
    monkeypatch, fault
):
    calls = []
    if fault == "multiple":
        calls = [returned("{}"), returned("{}")]
    elif fault == "wrong_name":
        calls = [returned("{}", "delete_data")]
    elif fault == "empty":
        calls = [returned("")]
    wire(
        monkeypatch,
        calls=calls,
        content="{}",
        response_text="{}" if fault == "raw_text" else None,
    )
    with pytest.raises(llm.LLMError) as exc:
        complete()
    assert exc.value.code == "LLM_STRUCTURED_RESULT_NOT_RETURNED"


@pytest.mark.parametrize(
    "revision,stage,enabled",
    [
        ("question_loop_v20", "answer", True),
        ("question_loop_v20", "answer_check", True),
        ("question_loop_v20", "relevance", False),
        ("question_loop_v19", "answer", False),
    ],
)
def test_tools_channel_is_only_new_minimax_answer_stages(
    engine, monkeypatch, revision, stage, enabled
):
    _, tools, _ = environment(engine, revision)
    tools.config["model"] = "MiniMax-M2.7-highspeed"
    recorded = []

    async def fake(*args, **kwargs):
        recorded.append(kwargs)
        return {"units": [], "explanations": [], "conditions": [], "relations": []}

    monkeypatch.setattr(llm, "complete_json", fake)
    asyncio.run(tools.model("generate", {}, schema=Answer, stage=stage))
    assert bool((recorded[0]["extra_body"] or {}).get("tools")) == enabled


def test_native_schema_bytes_are_in_prospective_final_reservation():
    plain = model_cost(CHECK_SYSTEM, {}, Verdict, 9000)
    native = model_cost(CHECK_SYSTEM, {}, Verdict, 9000, native_result=True)
    extra = tool_input_bytes({"tools": [result_tool(Verdict.model_json_schema())]})
    assert extra > 1000 and extra // 3 <= native - plain <= (extra + 2) // 3


def test_core_charges_native_input_before_dispatch_and_preserves_token_usage(
    monkeypatch,
):
    from backend.services.llm import client as core

    sent = wire(monkeypatch, calls=[returned("{}")])
    before = []

    class Budget:
        def before_model_attempt(self, **kwargs):
            before.append(kwargs)

        def after_model_attempt(self, **kwargs):
            pass

    async def run():
        core.set_llm_budget(Budget())
        try:
            return await core.complete(
                config(),
                system="schema",
                user="input",
                max_tokens=6000,
                extra_body=request(),
            )
        finally:
            core.set_llm_budget(None)

    result = asyncio.run(run())
    assert before[0]["estimated_input_tokens"] >= tool_input_bytes(request()) // 3
    assert len(sent) == 1 and result.usage == USAGE


def test_result_wrapper_hoists_schema_definitions_and_preserves_original_schema():
    original = Answer.model_json_schema()
    before = deepcopy(original)
    params = result_tool(original)["function"]["parameters"]
    assert original == before and params["$defs"] == original["$defs"]
    assert "$defs" not in params["properties"]["result"]
    assert params["required"] == ["result"] and params["additionalProperties"] is False
    value = {
        "units": [
            {
                "kind": "inference",
                "text": 'Unicode 引号"与换行\n不变',
                "evidence_ids": ["E1"],
            }
        ],
        "supported": False,
    }
    assert unwrap_result({"result": value}) is value


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"result": '{"units":[]}'},
        {"result": []},
        {"result": {}, "extra": "ignored?"},
        {"units": []},
    ],
)
def test_explicit_result_wrapper_never_fills_or_ignores_unknown_outer_fields(bad):
    with pytest.raises(ValueError):
        unwrap_result(bad)


def test_actual_core_unwraps_only_declared_result_channel_and_keeps_false_boolean(
    monkeypatch,
):
    from backend.services.llm import client as core

    value = {
        "checks": [{"supported": False, "quote": 'actual "source"', "source_id": "S1"}]
    }
    wire(
        monkeypatch, calls=[returned(json.dumps({"result": value}, ensure_ascii=False))]
    )
    metadata = {}

    async def run():
        return await core.complete_json(
            config(),
            system="schema",
            user="input",
            max_tokens=6000,
            max_retries=0,
            extra_body=request(),
            metadata_out=metadata,
        )

    out = asyncio.run(run())
    assert out == value and out["checks"][0]["supported"] is False
    assert metadata["structured_recovery"]["kind"] == "native_result_envelope"
