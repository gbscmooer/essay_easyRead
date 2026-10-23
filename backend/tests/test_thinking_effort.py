"""思考强度（thinking_effort）配置默认值与协议参数映射。"""

from backend.services.llm import (
    anthropic_thinking_budget_tokens,
    normalize_thinking_effort,
    resolve_thinking,
)
from backend.services.llm.adapters import AnthropicMessagesAdapter, OpenAIChatAdapter
from backend.services.llm.models import LLMConfig
from backend.services.ai_config import DEFAULT_CONFIG


# ── 配置默认值 ────────────────────────────────────────────────

def test_default_config_thinking_effort():
    assert DEFAULT_CONFIG["llm"]["thinking_effort"] == "off"


def test_normalize_thinking_effort():
    assert normalize_thinking_effort(None) == "off"
    assert normalize_thinking_effort("") == "off"
    assert normalize_thinking_effort("HIGH") == "high"
    assert normalize_thinking_effort("bogus") == "off"
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

    kwargs, _ = resolve_thinking(
        True, "https://gateway.example/v1", "deepseek-flash", "off", 8192,
    )
    assert kwargs == {"extra_body": {"thinking": {"type": "disabled"}}}


# ── Adapter 参数合流 ──────────────────────────────────────────

def _config(api_format: str, base_url: str, model: str) -> LLMConfig:
    return LLMConfig.from_mapping({
        "provider": "custom", "api_format": api_format, "api_key": "test",
        "base_url": base_url, "model": model,
    })


def test_anthropic_adapter_applies_thinking_and_temp_override():
    call = AnthropicMessagesAdapter._kwargs(
        _config("anthropic_messages", "https://api.anthropic.com", "claude-sonnet-4-5"),
        system="sys", user="user", max_tokens=16384, temperature=0.1,
        thinking_effort="medium",
    )
    assert call["temperature"] == 1.0
    assert call["thinking"] == {"type": "enabled", "budget_tokens": 4096}


def test_anthropic_adapter_without_effort_has_no_thinking():
    call = AnthropicMessagesAdapter._kwargs(
        _config("anthropic_messages", "https://api.anthropic.com", "claude-sonnet-4-5"),
        system="sys", user="user", max_tokens=8192, temperature=0.15,
        thinking_effort="off",
    )
    assert call["temperature"] == 0.15
    assert "thinking" not in call


def test_openai_adapter_merges_extra_body_with_deepseek_thinking():
    call = OpenAIChatAdapter._kwargs(
        _config("openai_chat", "https://api.deepseek.com/v1", "deepseek-v4"),
        system="sys", user="user", max_tokens=4096, temperature=0.2,
        extra_body={"existing": 1}, thinking_effort="medium",
    )
    assert call["extra_body"] == {"existing": 1, "thinking": {"type": "enabled"}}
    assert call["temperature"] == 0.2


def test_glm_thinking_toggle():
    kwargs, _ = resolve_thinking(
        True, "https://open.bigmodel.cn/api/coding/paas/v4", "glm-5.3-flash", "off", 1500,
    )
    assert kwargs == {"extra_body": {"thinking": {"type": "disabled"}}}
    kwargs, _ = resolve_thinking(
        True, "https://open.bigmodel.cn/api/paas/v4", "GLM-4.5", "low", 1500,
    )
    assert kwargs == {"extra_body": {"thinking": {"type": "enabled"}}}
