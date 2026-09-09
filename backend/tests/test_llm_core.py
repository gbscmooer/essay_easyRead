"""Provider-neutral LLM Core contracts."""

from types import SimpleNamespace

import pytest

from backend.services.llm import LLMConfig, LLMResponseError
from backend.services.llm.adapters import OpenAIChatAdapter
from backend.services.llm.json_utils import parse_json


def test_explicit_protocol_wins_over_endpoint_name():
    cfg = LLMConfig.from_mapping({
        "provider": "custom",
        "api_format": "openai_chat",
        "api_key": "test",
        "base_url": "https://anthropic-compatible.example/v1/chat/completions",
        "model": "model",
    })
    assert cfg.api_format == "openai_chat"
    assert cfg.base_url == "https://anthropic-compatible.example/v1"


def test_legacy_config_infers_protocol_and_normalizes_full_endpoint():
    cfg = LLMConfig.from_mapping({
        "api_key": "test",
        "base_url": "https://api.anthropic.com/v1/messages",
        "model": "claude-sonnet-4-6",
    })
    assert cfg.api_format == "anthropic_messages"
    assert cfg.base_url == "https://api.anthropic.com"


def test_structured_parser_accepts_fences_and_preamble():
    assert parse_json('response:\n```json\n{"tasks": []}\n```') == {"tasks": []}
    assert parse_json('items: ["a", "b"] trailing', expected=list) == ["a", "b"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "code"),
    [
        (SimpleNamespace(choices=[], _request_id="req-no-choice"), "LLM_INVALID_RESPONSE_ENVELOPE"),
        (SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=""))],
            _request_id="req-empty-content",
        ), "LLM_EMPTY_CONTENT"),
    ],
)
async def test_openai_adapter_rejects_unusable_envelopes(monkeypatch, response, code):
    class Completions:
        async def create(self, **kwargs):
            return response

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=Completions())

        async def close(self):
            return None

    monkeypatch.setattr("openai.AsyncOpenAI", FakeClient)
    cfg = LLMConfig.from_mapping({
        "provider": "custom", "api_format": "openai_chat", "api_key": "secret",
        "base_url": "https://gateway.example/v1", "model": "test-model",
    })

    with pytest.raises(LLMResponseError) as exc_info:
        await OpenAIChatAdapter().complete_once(
            cfg,
            system="sys",
            user="user",
            max_tokens=100,
            temperature=0.1,
            thinking_effort="off",
        )
    assert exc_info.value.code == code
    assert exc_info.value.endpoint == "gateway.example"
    assert exc_info.value.request_id.startswith("req-")


def test_openai_o_series_uses_completion_token_parameter():
    cfg = LLMConfig.from_mapping({
        "provider": "openai", "api_format": "openai_chat", "api_key": "test",
        "base_url": "https://api.openai.com/v1", "model": "o3-mini",
    })
    kwargs = OpenAIChatAdapter._kwargs(
        cfg, system="s", user="u", max_tokens=2048, temperature=0,
        thinking_effort="medium",
    )
    assert kwargs["max_completion_tokens"] == 2048
    assert "max_tokens" not in kwargs
    assert kwargs["reasoning_effort"] == "medium"
    assert "temperature" not in kwargs
