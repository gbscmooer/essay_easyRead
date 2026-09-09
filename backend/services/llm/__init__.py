"""Unified LLM Core.

Business services import the small functions exported here instead of SDKs.
Provider-specific URL, authentication, request and response handling lives in
the adapter layer.
"""

from .client import complete_json, complete_text, stream_text, test_connection
from .errors import LLMError, LLMResponseError
from .models import (
    API_FORMAT_ANTHROPIC,
    API_FORMAT_OPENAI,
    LLMConfig,
    LLMResponse,
    PROVIDER_PRESETS,
    infer_api_format,
    normalize_api_format,
    normalize_thinking_effort,
)
from .thinking import anthropic_thinking_budget_tokens, resolve_thinking

__all__ = [
    "API_FORMAT_ANTHROPIC",
    "API_FORMAT_OPENAI",
    "LLMConfig",
    "LLMError",
    "LLMResponse",
    "LLMResponseError",
    "PROVIDER_PRESETS",
    "anthropic_thinking_budget_tokens",
    "complete_json",
    "complete_text",
    "infer_api_format",
    "normalize_api_format",
    "normalize_thinking_effort",
    "resolve_thinking",
    "stream_text",
    "test_connection",
]
