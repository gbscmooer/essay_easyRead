"""Provider adapters for the two supported upstream wire formats."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from .errors import LLMError, LLMResponseError
from .models import (
    API_FORMAT_ANTHROPIC,
    API_FORMAT_OPENAI,
    LLMConfig,
    LLMResponse,
)
from .thinking import resolve_thinking


def _request_id(response: object) -> object:
    return getattr(response, "_request_id", None) or getattr(response, "request_id", None)


def _usage_dict(response: object) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        try:
            value = usage.model_dump()
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}
    return dict(usage) if isinstance(usage, dict) else {}


def _safe_provider_error(exc: Exception, config: LLMConfig) -> LLMError:
    if isinstance(exc, LLMError):
        return exc
    status = getattr(exc, "status_code", None)
    request_id = getattr(exc, "request_id", None)
    if not request_id:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if headers:
            request_id = headers.get("x-request-id") or headers.get("request-id")
    code = {
        401: "LLM_AUTH_FAILED",
        403: "LLM_FORBIDDEN",
        404: "LLM_ENDPOINT_NOT_FOUND",
        429: "LLM_RATE_LIMITED",
    }.get(status, "LLM_PROVIDER_ERROR")
    raw = str(exc).replace(config.api_key, "***") if config.api_key else str(exc)
    safe = " ".join(raw.split())[:300] or exc.__class__.__name__
    retryable = status in {408, 409, 429, 500, 502, 503, 504} or any(
        marker in safe.lower() for marker in ("timeout", "temporarily", "overloaded", "capacity")
    )
    return LLMError(
        code,
        safe,
        base_url=config.base_url,
        model=config.model,
        request_id=request_id,
        status_code=status if isinstance(status, int) else None,
        retryable=retryable,
    )


class ProviderAdapter(ABC):
    """Stable boundary hiding SDK-specific clients and response envelopes."""

    api_format: str

    @abstractmethod
    async def complete_once(
        self,
        config: LLMConfig,
        *,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        thinking_effort: str,
        extra_body: dict[str, Any] | None = None,
    ) -> LLMResponse:
        raise NotImplementedError

    @abstractmethod
    def stream_once(
        self,
        config: LLMConfig,
        *,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        thinking_effort: str,
    ) -> AsyncIterator[str]:
        raise NotImplementedError


def _openai_uses_completion_tokens(model: str) -> bool:
    normalized = str(model or "").lower()
    return normalized.startswith("gpt-5") or (
        len(normalized) > 1 and normalized[0] == "o" and normalized[1].isdigit()
    )


class OpenAIChatAdapter(ProviderAdapter):
    api_format = API_FORMAT_OPENAI

    @staticmethod
    def _kwargs(
        config: LLMConfig,
        *,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        thinking_effort: str,
        extra_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        # OpenAI reasoning models commonly reject temperature; keep provider
        # defaults for those models and only send it to ordinary chat models.
        if not _openai_uses_completion_tokens(config.model):
            kwargs["temperature"] = temperature
        token_key = "max_completion_tokens" if _openai_uses_completion_tokens(config.model) else "max_tokens"
        kwargs[token_key] = max_tokens
        if extra_body:
            kwargs["extra_body"] = dict(extra_body)
        thinking, _ = resolve_thinking(
            config.api_format, config.base_url, config.model, thinking_effort, max_tokens,
        )
        for key, value in thinking.items():
            if key == "extra_body" and "extra_body" in kwargs:
                kwargs["extra_body"].update(value)
            else:
                kwargs[key] = value
        return kwargs

    async def complete_once(
        self,
        config: LLMConfig,
        *,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        thinking_effort: str,
        extra_body: dict[str, Any] | None = None,
    ) -> LLMResponse:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url or None,
            timeout=config.timeout_seconds,
        )
        try:
            response = await client.chat.completions.create(
                **self._kwargs(
                    config,
                    system=system,
                    user=user,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    thinking_effort=thinking_effort,
                    extra_body=extra_body,
                )
            )
            if isinstance(response, str):
                text = response
            else:
                choices = getattr(response, "choices", None)
                if not choices:
                    raise LLMResponseError(
                        "LLM_INVALID_RESPONSE_ENVELOPE",
                        base_url=config.base_url,
                        model=config.model,
                        request_id=_request_id(response),
                    )
                message = getattr(choices[0], "message", None)
                if message is None:
                    raise LLMResponseError(
                        "LLM_INVALID_RESPONSE_ENVELOPE",
                        base_url=config.base_url,
                        model=config.model,
                        request_id=_request_id(response),
                    )
                content = getattr(message, "content", None)
                text = content if isinstance(content, str) else ""
                if not text.strip():
                    reasoning = getattr(message, "reasoning_content", None)
                    text = reasoning if isinstance(reasoning, str) else ""
            if not text.strip():
                raise LLMResponseError(
                    "LLM_EMPTY_CONTENT",
                    base_url=config.base_url,
                    model=config.model,
                    request_id=_request_id(response),
                    content_length=len(text),
                )
            return LLMResponse(
                text=text,
                model=config.model,
                api_format=config.api_format,
                request_id=str(_request_id(response)) if _request_id(response) else None,
                usage=_usage_dict(response),
            )
        except LLMError:
            raise
        except Exception as exc:
            raise _safe_provider_error(exc, config) from exc
        finally:
            await client.close()

    async def stream_once(
        self,
        config: LLMConfig,
        *,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        thinking_effort: str,
    ) -> AsyncIterator[str]:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url or None,
            timeout=config.timeout_seconds,
        )
        try:
            kwargs = self._kwargs(
                config,
                system=system,
                user=user,
                max_tokens=max_tokens,
                temperature=temperature,
                thinking_effort=thinking_effort,
            )
            kwargs["stream"] = True
            stream = await client.chat.completions.create(**kwargs)
            async for chunk in stream:
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", None) if delta is not None else None
                if isinstance(content, str) and content:
                    yield content
        except LLMError:
            raise
        except Exception as exc:
            raise _safe_provider_error(exc, config) from exc
        finally:
            await client.close()


def _extract_anthropic_text(message: object) -> str:
    blocks = getattr(message, "content", None) or []
    texts = [getattr(block, "text", "") for block in blocks]
    return "".join(text for text in texts if isinstance(text, str))


class AnthropicMessagesAdapter(ProviderAdapter):
    api_format = API_FORMAT_ANTHROPIC

    @staticmethod
    def _kwargs(
        config: LLMConfig,
        *,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        thinking_effort: str,
    ) -> dict[str, Any]:
        thinking, temp_override = resolve_thinking(
            config.api_format, config.base_url, config.model, thinking_effort, max_tokens,
        )
        return {
            "model": config.model,
            "max_tokens": max_tokens,
            "temperature": temp_override if temp_override is not None else temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            **thinking,
        }

    async def complete_once(
        self,
        config: LLMConfig,
        *,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        thinking_effort: str,
        extra_body: dict[str, Any] | None = None,
    ) -> LLMResponse:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(
            api_key=config.api_key,
            base_url=config.base_url or None,
            timeout=config.timeout_seconds,
        )
        try:
            response = await client.messages.create(
                **self._kwargs(
                    config,
                    system=system,
                    user=user,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    thinking_effort=thinking_effort,
                )
            )
            text = _extract_anthropic_text(response)
            if not text.strip():
                raise LLMResponseError(
                    "LLM_EMPTY_CONTENT",
                    base_url=config.base_url,
                    model=config.model,
                    request_id=_request_id(response),
                    content_length=len(text),
                )
            return LLMResponse(
                text=text,
                model=config.model,
                api_format=config.api_format,
                request_id=str(_request_id(response)) if _request_id(response) else None,
                usage=_usage_dict(response),
            )
        except LLMError:
            raise
        except Exception as exc:
            raise _safe_provider_error(exc, config) from exc
        finally:
            await client.close()

    async def stream_once(
        self,
        config: LLMConfig,
        *,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        thinking_effort: str,
    ) -> AsyncIterator[str]:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(
            api_key=config.api_key,
            base_url=config.base_url or None,
            timeout=config.timeout_seconds,
        )
        try:
            async with client.messages.stream(
                **self._kwargs(
                    config,
                    system=system,
                    user=user,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    thinking_effort=thinking_effort,
                )
            ) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield text
        except LLMError:
            raise
        except Exception as exc:
            raise _safe_provider_error(exc, config) from exc
        finally:
            await client.close()


_ADAPTERS: dict[str, ProviderAdapter] = {
    API_FORMAT_OPENAI: OpenAIChatAdapter(),
    API_FORMAT_ANTHROPIC: AnthropicMessagesAdapter(),
}


def get_adapter(api_format: str) -> ProviderAdapter:
    try:
        return _ADAPTERS[api_format]
    except KeyError as exc:
        raise ValueError(f"unsupported LLM api_format: {api_format}") from exc
