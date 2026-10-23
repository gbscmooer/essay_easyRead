"""Provider-neutral reasoning settings translated by API format."""

from __future__ import annotations

import re

from .models import API_FORMAT_OPENAI, normalize_thinking_effort

_ANTHROPIC_THINKING_BUDGET = {"low": 1024, "medium": 4096, "high": 16384}
_ANTHROPIC_THINKING_MODEL_RE = re.compile(
    r"claude-(?:3[.-][7-9]|[4-9]|(?:sonnet|opus|haiku)-[4-9])"
)
_OPENAI_REASONING_MODEL_RE = re.compile(r"^(o\d|gpt-5)")
# OpenAI-compatible gateways use names beyond deepseek-vN (for example
# deepseek-flash / deepseek-chat and vendor-prefixed deepseek/*).  Treat the
# whole DeepSeek family consistently so a stage-level ``off`` override really
# disables reasoning instead of silently inheriting the provider default.
_DEEPSEEK_THINKING_MODEL_RE = re.compile(r"(?:^|[/])deepseek(?:[-/]|$)", re.IGNORECASE)
# 智谱 GLM（glm-4.5 起的混合推理模型）同样用 thinking.type 开关；不下发时服务端默认开启思考，
# 阶段级 off 会静默失效（实测 glm-5.3-flash 关闭后单次相关性判断输出 220→12 token）。
_GLM_THINKING_MODEL_RE = re.compile(r"(?:^|[/])glm-", re.IGNORECASE)


def anthropic_thinking_budget_tokens(effort: str) -> int:
    level = normalize_thinking_effort(effort)
    return _ANTHROPIC_THINKING_BUDGET.get(level, 0) if level != "off" else 0


def resolve_thinking(
    api_format_or_openai: str | bool,
    base_url: str,
    model: str,
    effort: str,
    max_tokens: int,
) -> tuple[dict, float | None]:
    """Translate one UI setting into protocol-specific request parameters.

    The boolean form is retained for compatibility with older internal tests.
    """

    is_openai = (
        api_format_or_openai
        if isinstance(api_format_or_openai, bool)
        else api_format_or_openai == API_FORMAT_OPENAI
    )
    level = normalize_thinking_effort(effort)
    if is_openai:
        if (_DEEPSEEK_THINKING_MODEL_RE.search(model or "")
                or _GLM_THINKING_MODEL_RE.search(model or "")):
            state = "enabled" if level != "off" else "disabled"
            return {"extra_body": {"thinking": {"type": state}}}, None
        if level != "off" and _OPENAI_REASONING_MODEL_RE.match(model or ""):
            return {"reasoning_effort": level}, None
        return {}, None

    if level != "off" and _ANTHROPIC_THINKING_MODEL_RE.search(model or ""):
        budget = min(_ANTHROPIC_THINKING_BUDGET[level], max_tokens - 1024)
        if budget >= 1024:
            return {"thinking": {"type": "enabled", "budget_tokens": budget}}, 1.0
    return {}, None
