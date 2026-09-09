"""Provider-neutral completion, streaming and structured-generation API."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Mapping
from typing import Any

from .adapters import get_adapter
from .errors import LLMError, LLMResponseError
from .json_utils import parse_json
from .models import LLMConfig, LLMResponse

MAX_RETRIES = 5
BASE_DELAY = 3.0
MAX_DELAY = 120.0


def retry_delays(max_retries: int = MAX_RETRIES) -> list[float]:
    return [min(BASE_DELAY * (2 ** index), MAX_DELAY) for index in range(max(0, max_retries))]


def is_retryable(exc: Exception) -> bool:
    if isinstance(exc, LLMError):
        return exc.retryable
    if isinstance(exc, (json.JSONDecodeError, LLMResponseError)):
        return True
    message = str(exc).lower()
    return any(token in message for token in (
        "429", "500", "502", "503", "504", "timeout", "rate limit", "overloaded", "capacity",
    ))


def _resolved(
    config: Mapping[str, Any] | LLMConfig,
    *,
    max_tokens: int | None,
    temperature: float | None,
    thinking_effort: str | None,
    timeout_seconds: float | None,
) -> tuple[LLMConfig, int, float, str]:
    resolved = LLMConfig.from_mapping(config)
    overrides: dict[str, Any] = {}
    if timeout_seconds is not None:
        overrides["timeout_seconds"] = timeout_seconds
    if overrides:
        resolved = resolved.with_overrides(**overrides)
    resolved.validate_for_request()
    return (
        resolved,
        max(1, int(max_tokens if max_tokens is not None else resolved.max_tokens)),
        float(temperature if temperature is not None else resolved.temperature),
        str(thinking_effort if thinking_effort is not None else resolved.thinking_effort),
    )


async def complete(
    config: Mapping[str, Any] | LLMConfig,
    *,
    system: str,
    user: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
    thinking_effort: str | None = None,
    timeout_seconds: float | None = None,
    max_retries: int = 0,
    extra_body: dict[str, Any] | None = None,
) -> LLMResponse:
    resolved, token_limit, temp, effort = _resolved(
        config,
        max_tokens=max_tokens,
        temperature=temperature,
        thinking_effort=thinking_effort,
        timeout_seconds=timeout_seconds,
    )
    adapter = get_adapter(resolved.api_format)
    delays = retry_delays(max_retries)
    for attempt in range(max_retries + 1):
        try:
            return await adapter.complete_once(
                resolved,
                system=system,
                user=user,
                max_tokens=token_limit,
                temperature=temp,
                thinking_effort=effort,
                extra_body=extra_body,
            )
        except Exception as exc:
            if attempt >= max_retries or not is_retryable(exc):
                raise
            await asyncio.sleep(delays[attempt])
    raise RuntimeError("unreachable")


async def complete_text(
    config: Mapping[str, Any] | LLMConfig,
    **kwargs: Any,
) -> str:
    return (await complete(config, **kwargs)).text


async def complete_json(
    config: Mapping[str, Any] | LLMConfig,
    *,
    system: str,
    user: str,
    expected: type = dict,
    max_retries: int = MAX_RETRIES,
    **kwargs: Any,
) -> Any:
    delays = retry_delays(max_retries)
    resolved = LLMConfig.from_mapping(config)
    resolved.validate_for_request()
    for attempt in range(max_retries + 1):
        try:
            text = await complete_text(
                resolved,
                system=system,
                user=user,
                max_retries=0,
                **kwargs,
            )
            return parse_json(text, expected=expected)
        except Exception as exc:
            retryable = is_retryable(exc) or isinstance(exc, (json.JSONDecodeError, ValueError))
            if attempt >= max_retries or not retryable:
                raise
            await asyncio.sleep(delays[attempt])
    raise RuntimeError("unreachable")


async def stream_text(
    config: Mapping[str, Any] | LLMConfig,
    *,
    system: str,
    user: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
    thinking_effort: str | None = None,
    timeout_seconds: float | None = None,
) -> AsyncIterator[str]:
    resolved, token_limit, temp, effort = _resolved(
        config,
        max_tokens=max_tokens,
        temperature=temperature,
        thinking_effort=thinking_effort,
        timeout_seconds=timeout_seconds,
    )
    adapter = get_adapter(resolved.api_format)
    async for text in adapter.stream_once(
        resolved,
        system=system,
        user=user,
        max_tokens=token_limit,
        temperature=temp,
        thinking_effort=effort,
    ):
        yield text


async def test_connection(
    config: Mapping[str, Any] | LLMConfig,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        resolved = LLMConfig.from_mapping(config)
        response = await complete(
            resolved,
            system="Reply with OK.",
            user="Hi",
            max_tokens=5,
            temperature=0,
            thinking_effort="off",
            timeout_seconds=min(resolved.timeout_seconds, 30.0),
            max_retries=0,
        )
        return {
            "ok": True,
            "message": f"连接成功 ({resolved.model})",
            "latency_ms": int((time.monotonic() - started) * 1000),
            "request_id": response.request_id,
            "api_format": resolved.api_format,
        }
    except Exception as exc:
        api_format = getattr(locals().get("resolved"), "api_format", None)
        return {
            "ok": False,
            "message": str(exc)[:500],
            "latency_ms": int((time.monotonic() - started) * 1000),
            "request_id": getattr(exc, "request_id", None),
            "api_format": api_format,
        }
