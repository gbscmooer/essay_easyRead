"""Authorized LLM proxy transport selection and adapter integration."""

from types import SimpleNamespace

import pytest

from backend.services.llm import LLMConfig
from backend.services.llm.adapters import OpenAIChatAdapter
from backend.services.llm_proxy import resolve_transport


def _config(**overrides):
    return LLMConfig.from_mapping({
        "provider": "custom",
        "api_format": "openai_chat",
        "api_key": "proxy-token",
        "base_url": "https://upstream.example/v1",
        "model": "test-model",
        **overrides,
    })


def test_proxy_transport_normalizes_completion_endpoint():
    cfg = _config(proxy={
        "enabled": True,
        "base_url": "http://127.0.0.1:9000/v1/chat/completions",
    })

    target = resolve_transport(cfg)

    assert target.proxied is True
    assert target.base_url == "http://127.0.0.1:9000/v1"
    assert cfg.base_url == "https://upstream.example/v1"
    assert cfg.to_dict()["proxy"] == {
        "enabled": True,
        "base_url": "http://127.0.0.1:9000/v1",
    }


def test_proxy_transport_requires_explicit_url_when_enabled():
    cfg = _config(proxy={"enabled": True, "base_url": ""})

    with pytest.raises(ValueError, match="代理 Base URL"):
        cfg.validate_for_request()


def test_proxy_transport_does_not_require_unused_direct_url():
    cfg = _config(
        base_url="",
        proxy={"enabled": True, "base_url": "http://localhost:9000/v1"},
    )

    cfg.validate_for_request()
    assert resolve_transport(cfg).base_url == "http://localhost:9000/v1"


@pytest.mark.asyncio
async def test_openai_adapter_uses_proxy_without_spoofed_headers(monkeypatch):
    captured: dict = {}

    class Completions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))],
                _request_id="req-proxy",
                usage={},
            )

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.chat = SimpleNamespace(completions=Completions())

        async def close(self):
            return None

    monkeypatch.setattr("openai.AsyncOpenAI", FakeClient)
    cfg = _config(proxy={"enabled": True, "base_url": "http://localhost:9000/v1"})

    response = await OpenAIChatAdapter().complete_once(
        cfg,
        system="sys",
        user="user",
        max_tokens=8,
        temperature=0,
        thinking_effort="off",
    )

    assert response.text == "OK"
    assert captured["base_url"] == "http://localhost:9000/v1"
    assert captured["api_key"] == "proxy-token"
    assert "default_headers" not in captured
