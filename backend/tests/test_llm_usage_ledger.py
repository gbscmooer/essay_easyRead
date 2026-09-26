"""LLM token 台账（工作流可追溯性）单元测试。"""

import asyncio
from unittest.mock import patch

from backend.services.llm import (
    LLMResponse, set_llm_stage, start_token_ledger, token_ledger_report,
)
from backend.services.llm import client as llm_client


def _resp(text: str, usage: dict) -> LLMResponse:
    return LLMResponse(text=text, model="m", api_format="openai_chat", usage=usage)


def test_ledger_accumulates_by_stage_and_normalizes_usage_keys():
    ledger = start_token_ledger()

    async def fake_adapter_complete_once(*args, **kwargs):
        return _resp("a", {"prompt_tokens": 100, "completion_tokens": 20})

    class _Stub:
        async def complete_once(self, *a, **k):
            return await fake_adapter_complete_once()

    with patch.object(llm_client, "get_adapter", lambda fmt: _Stub()):
        set_llm_stage("decompose")
        asyncio.run(llm_client.complete(
            {"api_format": "openai_chat", "api_key": "k", "base_url": "https://x", "model": "m"},
            system="s", user="u", max_tokens=10,
        ))
        set_llm_stage("synthesize")
        asyncio.run(llm_client.complete(
            {"api_format": "openai_chat", "api_key": "k", "base_url": "https://x", "model": "m"},
            system="s", user="u", max_tokens=10,
        ))

    report = token_ledger_report(ledger)
    assert report["decompose"] == {
        "calls": 1, "input_tokens": 100, "output_tokens": 20,
        "cached_input_tokens": 0, "cache_write_tokens": 0,
    }
    assert report["synthesize"]["calls"] == 1
    assert report["total"] == {
        "calls": 2, "input_tokens": 200, "output_tokens": 40,
        "cached_input_tokens": 0, "cache_write_tokens": 0,
    }


def test_ledger_accepts_anthropic_usage_keys():
    ledger = start_token_ledger()

    class _Stub:
        async def complete_once(self, *a, **k):
            return _resp("a", {"input_tokens": 7, "output_tokens": 3})

    with patch.object(llm_client, "get_adapter", lambda fmt: _Stub()):
        set_llm_stage("report")
        asyncio.run(llm_client.complete(
            {"api_format": "anthropic_messages", "api_key": "k", "base_url": "https://x", "model": "m"},
            system="s", user="u", max_tokens=10,
        ))

    report = token_ledger_report(ledger)
    assert report["report"]["input_tokens"] == 7
    assert report["total"]["output_tokens"] == 3


def test_no_ledger_active_is_zero_overhead():
    # 未启动台账时调用不应报错也不累计
    class _Stub:
        async def complete_once(self, *a, **k):
            return _resp("a", {})

    with patch.object(llm_client, "get_adapter", lambda fmt: _Stub()):
        asyncio.run(llm_client.complete(
            {"api_format": "openai_chat", "api_key": "k", "base_url": "https://x", "model": "m"},
            system="s", user="u", max_tokens=10,
        ))
    assert token_ledger_report({})["total"]["calls"] == 0
