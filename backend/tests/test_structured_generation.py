import asyncio

import pytest

from backend.services.deep_research import domain_synthesizer as ds


def test_official_v4_structured_tasks_disable_thinking_only(monkeypatch):
    """结构化任务不再自行注入 deepseek 思考开关，改由 thinking_effort 统一解析。

    旧行为（对 api.deepseek.com 的 deepseek-v4 强制 extra_body thinking disabled）
    已收口到 ai_analyzer.resolve_thinking：off → disabled，low/medium/high → enabled，
    其他模型不附加参数。这里只验证 _llm_json 忠实透传配置。
    """
    calls = []
    class Client:
        async def close(self):
            pass
    monkeypatch.setattr(ds, "_create_openai_client", lambda *args: Client())
    async def complete(*args, **kwargs):
        calls.append(kwargs)
        return '{"rows": []}'
    monkeypatch.setattr(ds, "_complete_once", complete)
    cfg = {"api_key": "test", "base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash"}
    result = asyncio.run(ds._llm_json(cfg, "s", "u", max_tokens=100))
    assert result == {"rows": []}
    # 未配置思考强度时透传 None（_complete_once 不附加思考参数）
    assert calls[0].get("thinking_effort") is None
    assert "extra_body" not in calls[0]

    calls.clear()
    cfg = {**cfg, "thinking_effort": "off"}
    asyncio.run(ds._llm_json(cfg, "s", "u", max_tokens=100))
    assert calls[0].get("thinking_effort") == "off"


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
