import asyncio

import pytest

from backend.services.deep_research import domain_synthesizer as ds


@pytest.mark.parametrize("base,model,disabled", [
    ("https://api.deepseek.com", "deepseek-v4-flash", True),
    ("https://api.deepseek.com/v1", "deepseek-v4-pro", True),
    ("https://other.example/v1", "deepseek-v4-flash", False),
    ("https://api.deepseek.com", "deepseek-chat", False),
])
def test_official_v4_structured_tasks_disable_thinking_only(base, model, disabled, monkeypatch):
    calls = []
    class Client:
        async def close(self):
            pass
    monkeypatch.setattr(ds, "_create_openai_client", lambda *args: Client())
    async def complete(*args, **kwargs):
        calls.append(kwargs)
        return '{"rows": []}'
    monkeypatch.setattr(ds, "_complete_once", complete)
    result = asyncio.run(ds._llm_json({"api_key": "test", "base_url": base, "model": model}, "s", "u", max_tokens=100))
    assert result == {"rows": []}
    assert ("extra_body" in calls[0]) is disabled
    if disabled:
        assert calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}


def test_fenced_json_is_parsed_without_retry(monkeypatch):
    class Client:
        async def close(self):
            pass
    monkeypatch.setattr(ds, "_create_openai_client", lambda *args: Client())
    calls = []
    async def complete(*args, **kwargs):
        calls.append(1)
        return '```json\n{"tasks": []}\n```'
    monkeypatch.setattr(ds, "_complete_once", complete)
    assert asyncio.run(ds._llm_json({"api_key": "test", "model": "test"}, "s", "u", max_tokens=100)) == {"tasks": []}
    assert len(calls) == 1
