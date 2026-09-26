"""Provider-neutral LLM Core contracts."""

import inspect
from types import SimpleNamespace

import pytest

from backend.services.llm import LLMConfig, LLMResponse, LLMResponseError
from backend.services.llm import client as llm_client
from backend.services.llm import adapters
from backend.services.llm.adapters import AnthropicMessagesAdapter, OpenAIChatAdapter
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


def test_all_provider_clients_disable_hidden_sdk_retries():
    """普通/流式 OpenAI 与 Anthropic 客户端都由 Core 统一管理重试。"""
    source = inspect.getsource(adapters)
    assert source.count("max_retries=0") == 4


@pytest.mark.asyncio
async def test_anthropic_adapter_disables_hidden_sdk_retries(monkeypatch):
    captured: dict = {}

    class Messages:
        async def create(self, **kwargs):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="OK")],
                usage=None,
                _request_id="req-anthropic",
            )

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.messages = Messages()

        async def close(self):
            return None

    monkeypatch.setattr("anthropic.AsyncAnthropic", FakeClient)
    cfg = LLMConfig.from_mapping({
        "provider": "anthropic", "api_format": "anthropic_messages",
        "api_key": "secret", "base_url": "https://api.anthropic.com",
        "model": "test-model",
    })
    response = await AnthropicMessagesAdapter().complete_once(
        cfg, system="sys", user="user", max_tokens=8,
        temperature=0, thinking_effort="off",
    )
    assert response.text == "OK"
    assert captured["max_retries"] == 0


@pytest.mark.asyncio
async def test_provider_finish_reasons_are_normalized(monkeypatch):
    class OpenAICompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content='{"ok": true}'),
                    finish_reason="length")],
                usage=None, _request_id="req-openai-length",
            )

    class OpenAIClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=OpenAICompletions())

        async def close(self):
            return None

    monkeypatch.setattr("openai.AsyncOpenAI", OpenAIClient)
    openai_cfg = LLMConfig.from_mapping({
        "provider": "custom", "api_format": "openai_chat", "api_key": "secret",
        "base_url": "https://gateway.example/v1", "model": "model",
    })
    response = await OpenAIChatAdapter().complete_once(
        openai_cfg, system="s", user="u", max_tokens=8,
        temperature=0, thinking_effort="off")
    assert response.finish_reason == "length"

    class Messages:
        async def create(self, **kwargs):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="OK")],
                usage=None, stop_reason="max_tokens", _request_id="req-anthropic-length")

    class AnthropicClient:
        def __init__(self, **kwargs):
            self.messages = Messages()

        async def close(self):
            return None

    monkeypatch.setattr("anthropic.AsyncAnthropic", AnthropicClient)
    anthropic_cfg = LLMConfig.from_mapping({
        "provider": "anthropic", "api_format": "anthropic_messages",
        "api_key": "secret", "base_url": "https://api.anthropic.com", "model": "model",
    })
    response = await AnthropicMessagesAdapter().complete_once(
        anthropic_cfg, system="s", user="u", max_tokens=8,
        temperature=0, thinking_effort="off")
    assert response.finish_reason == "max_tokens"


@pytest.mark.asyncio
async def test_complete_json_exposes_finish_reason_even_when_json_is_truncated(monkeypatch):
    class Adapter:
        async def complete_once(self, *_args, **_kwargs):
            return LLMResponse(
                text='{"unfinished": "value', model="m", api_format="openai_chat",
                finish_reason="length", usage={"completion_tokens": 100})

    monkeypatch.setattr(llm_client, "get_adapter", lambda _format: Adapter())
    metadata: dict = {}
    with pytest.raises(Exception):
        await llm_client.complete_json({
            "provider": "custom", "api_format": "openai_chat", "api_key": "test",
            "base_url": "https://example.test/v1", "model": "m",
        }, system="s", user="u", max_tokens=100, max_retries=0,
           metadata_out=metadata)
    assert metadata["finish_reason"] == "length"
    assert metadata["usage"]["completion_tokens"] == 100
