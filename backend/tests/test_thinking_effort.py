"""思考强度（thinking_effort）配置默认值与协议参数映射。"""

import pytest

from backend.services.ai_analyzer import (
    _complete_once,
    anthropic_thinking_budget_tokens,
    normalize_thinking_effort,
    resolve_thinking,
)
from backend.services.ai_config import DEFAULT_CONFIG


# ── 配置默认值 ────────────────────────────────────────────────

def test_default_config_thinking_effort():
    # 解读模型默认「中」，翻译不需要推理默认关闭
    assert DEFAULT_CONFIG["analysis"]["thinking_effort"] == "medium"
    assert DEFAULT_CONFIG["translate"]["thinking_effort"] == "off"


def test_normalize_thinking_effort():
    assert normalize_thinking_effort(None) == "medium"
    assert normalize_thinking_effort("") == "medium"
    assert normalize_thinking_effort("HIGH") == "high"
    assert normalize_thinking_effort("bogus") == "medium"
    assert normalize_thinking_effort("off") == "off"


# ── Anthropic 协议 ────────────────────────────────────────────

def test_anthropic_thinking_forces_temperature_one():
    kwargs, temp = resolve_thinking(
        False, "https://api.anthropic.com", "claude-sonnet-4-5-20250929", "medium", 16384)
    assert kwargs == {"thinking": {"type": "enabled", "budget_tokens": 4096}}
    assert temp == 1.0


def test_anthropic_budget_clamped_by_max_tokens():
    kwargs, temp = resolve_thinking(
        False, "https://api.anthropic.com", "claude-haiku-4-5-20251001", "high", 8000)
    assert kwargs["thinking"]["budget_tokens"] == 8000 - 1024
    assert temp == 1.0


def test_anthropic_skips_thinking_when_output_space_insufficient():
    # 图谱速读 max_tokens=1500 放不下最小 1024 预算 + 1024 正文空间
    kwargs, temp = resolve_thinking(
        False, "https://api.anthropic.com", "claude-sonnet-4-5", "medium", 1500)
    assert (kwargs, temp) == ({}, None)


def test_anthropic_non_thinking_model_untouched():
    kwargs, temp = resolve_thinking(
        False, "https://api.anthropic.com", "claude-3-haiku-20240307", "high", 16384)
    assert (kwargs, temp) == ({}, None)


def test_anthropic_off_untouched():
    kwargs, temp = resolve_thinking(
        False, "https://api.anthropic.com", "claude-sonnet-4-5", "off", 16384)
    assert (kwargs, temp) == ({}, None)


def test_anthropic_budget_tokens_helper():
    assert anthropic_thinking_budget_tokens("low") == 1024
    assert anthropic_thinking_budget_tokens("medium") == 4096
    assert anthropic_thinking_budget_tokens("high") == 16384
    assert anthropic_thinking_budget_tokens("off") == 0


# ── OpenAI 兼容协议 ───────────────────────────────────────────

def test_openai_reasoning_effort_for_reasoning_models():
    kwargs, temp = resolve_thinking(True, "https://api.openai.com/v1", "gpt-5-mini", "low", 2048)
    assert kwargs == {"reasoning_effort": "low"}
    assert temp is None

    kwargs, _ = resolve_thinking(True, "https://api.openai.com/v1", "o3", "high", 4096)
    assert kwargs == {"reasoning_effort": "high"}


def test_openai_non_reasoning_model_untouched():
    kwargs, temp = resolve_thinking(True, "https://api.openai.com/v1", "gpt-4o-mini", "high", 2048)
    assert (kwargs, temp) == ({}, None)


def test_deepseek_thinking_toggle():
    kwargs, _ = resolve_thinking(True, "https://api.deepseek.com/v1", "deepseek-v4", "off", 4096)
    assert kwargs == {"extra_body": {"thinking": {"type": "disabled"}}}
    kwargs, _ = resolve_thinking(True, "https://api.deepseek.com/v1", "deepseek-v4", "medium", 4096)
    assert kwargs == {"extra_body": {"thinking": {"type": "enabled"}}}


# ── _complete_once 参数合流 ───────────────────────────────────

class _FakeAnthropicResponse:
    def __init__(self):
        self.content = [type("TextBlock", (), {"text": "ok"})()]


class _FakeAnthropicMessages:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeAnthropicResponse()


class _FakeAnthropicClient:
    def __init__(self):
        self.messages = _FakeAnthropicMessages()


class _FakeChoiceMessage:
    def __init__(self):
        self.content = "ok"


class _FakeChoice:
    def __init__(self):
        self.message = _FakeChoiceMessage()


class _FakeOpenAIResponse:
    def __init__(self):
        self.choices = [_FakeChoice()]


class _FakeChatCompletions:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeOpenAIResponse()


class _FakeOpenAIClient:
    def __init__(self):
        self.chat = type("Chat", (), {})()
        self.chat.completions = _FakeChatCompletions()


@pytest.mark.asyncio
async def test_complete_once_applies_anthropic_thinking_and_temp_override():
    client = _FakeAnthropicClient()
    out = await _complete_once(
        client, False, "claude-sonnet-4-5", "sys", "user",
        max_tokens=16384, temperature=0.1,
        thinking_effort="medium", base_url="https://api.anthropic.com")
    assert out == "ok"
    call = client.messages.calls[0]
    assert call["temperature"] == 1.0
    assert call["thinking"] == {"type": "enabled", "budget_tokens": 4096}


@pytest.mark.asyncio
async def test_complete_once_without_effort_keeps_legacy_kwargs():
    client = _FakeAnthropicClient()
    await _complete_once(
        client, False, "claude-sonnet-4-5", "sys", "user",
        max_tokens=8192, temperature=0.15)
    call = client.messages.calls[0]
    assert call["temperature"] == 0.15
    assert "thinking" not in call


@pytest.mark.asyncio
async def test_complete_once_merges_extra_body_with_deepseek_thinking():
    client = _FakeOpenAIClient()
    await _complete_once(
        client, True, "deepseek-v4", "sys", "user",
        max_tokens=4096, temperature=0.2,
        extra_body={"existing": 1},
        thinking_effort="medium", base_url="https://api.deepseek.com/v1")
    call = client.chat.completions.calls[0]
    assert call["extra_body"] == {"existing": 1, "thinking": {"type": "enabled"}}
    assert call["temperature"] == 0.2
