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
from .json_utils import final_content_after_thinking
from ..llm_proxy import resolve_transport


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


def _safe_provider_error(
    exc: Exception,
    config: LLMConfig,
    *,
    request_base_url: str | None = None,
) -> LLMError:
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
        marker in safe.lower() for marker in (
            "timeout", "temporarily", "overloaded", "capacity",
            "connection error", "connection reset", "connection refused",
            "connection aborted", "network", "ssl", "eof",
        )
    )
    return LLMError(
        code,
        safe,
        base_url=request_base_url or config.base_url,
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
        usage_out: dict[str, Any] | None = None,
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

        transport = resolve_transport(config)
        client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=transport.base_url or None,
            timeout=config.timeout_seconds,
            max_retries=0,
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
                from .structured import requests_result_tool
                if requests_result_tool(extra_body):
                    raise LLMResponseError("LLM_STRUCTURED_RESULT_NOT_RETURNED",
                                           base_url=transport.base_url, model=config.model)
                text = response
                finish_reason = None
            else:
                choices = getattr(response, "choices", None)
                if not choices:
                    raise LLMResponseError(
                        "LLM_INVALID_RESPONSE_ENVELOPE",
                        base_url=transport.base_url,
                        model=config.model,
                        request_id=_request_id(response),
                    )
                message = getattr(choices[0], "message", None)
                finish_reason = getattr(choices[0], "finish_reason", None)
                if message is None:
                    raise LLMResponseError(
                        "LLM_INVALID_RESPONSE_ENVELOPE",
                        base_url=transport.base_url,
                        model=config.model,
                        request_id=_request_id(response),
                    )
                content = getattr(message, "content", None)
                text = content if isinstance(content, str) else ""
                from .structured import requests_result_tool, result_arguments
                if requests_result_tool(extra_body):
                    arguments = result_arguments(message)
                    if arguments is None:
                        raise LLMResponseError(
                            "LLM_STRUCTURED_RESULT_NOT_RETURNED", base_url=transport.base_url,
                            model=config.model, request_id=_request_id(response),
                            finish_reason=str(finish_reason) if finish_reason is not None else None,
                        )
                    text = arguments
                if not text.strip() and (extra_body or {}).get("reasoning_split") is not True:
                    reasoning = getattr(message, "reasoning_content", None)
                    text = reasoning if isinstance(reasoning, str) else ""
            if (extra_body or {}).get("reasoning_split") is True:
                text = final_content_after_thinking(text)
            if not text.strip():
                raise LLMResponseError(
                    "LLM_EMPTY_CONTENT",
                    base_url=transport.base_url,
                    model=config.model,
                    request_id=_request_id(response),
                    content_length=len(text),
                    finish_reason=str(finish_reason) if finish_reason is not None else None,
                )
            return LLMResponse(
                text=text,
                model=config.model,
                api_format=config.api_format,
                request_id=str(_request_id(response)) if _request_id(response) else None,
                usage=_usage_dict(response),
                finish_reason=str(finish_reason) if finish_reason is not None else None,
            )
        except LLMError:
            raise
        except Exception as exc:
            raise _safe_provider_error(exc, config, request_base_url=transport.base_url) from exc
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
        usage_out: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        from openai import AsyncOpenAI

        transport = resolve_transport(config)
        client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=transport.base_url or None,
            timeout=config.timeout_seconds,
            max_retries=0,
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
            # 请求最终 chunk 附带 usage（token 统计依赖）；个别网关不认这个参数，
            # 400/422 时退化为无遥测的普通流重试一次。
            kwargs["stream_options"] = {"include_usage": True}
            try:
                stream = await client.chat.completions.create(**kwargs)
            except Exception as exc:
                if getattr(exc, "status_code", None) not in (400, 422):
                    raise
                kwargs.pop("stream_options", None)
                stream = await client.chat.completions.create(**kwargs)
            async for chunk in stream:
                usage = _usage_dict(chunk)
                if usage and usage_out is not None:
                    usage_out.clear()
                    usage_out.update(usage)
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
            raise _safe_provider_error(exc, config, request_base_url=transport.base_url) from exc
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

        transport = resolve_transport(config)
        client = AsyncAnthropic(
            api_key=config.api_key,
            base_url=transport.base_url or None,
            timeout=config.timeout_seconds,
            max_retries=0,
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
                    base_url=transport.base_url,
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
                finish_reason=(str(getattr(response, "stop_reason", None))
                               if getattr(response, "stop_reason", None) is not None else None),
            )
        except LLMError:
            raise
        except Exception as exc:
            raise _safe_provider_error(exc, config, request_base_url=transport.base_url) from exc
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
        usage_out: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        from anthropic import AsyncAnthropic

        transport = resolve_transport(config)
        client = AsyncAnthropic(
            api_key=config.api_key,
            base_url=transport.base_url or None,
            timeout=config.timeout_seconds,
            max_retries=0,
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
                if usage_out is not None:
                    # 结束后从终态消息取 usage（token 统计）；失败不影响主流程
                    try:
                        usage_out.update(_usage_dict(await stream.get_final_message()))
                    except Exception:
                        pass
        except LLMError:
            raise
        except Exception as exc:
            raise _safe_provider_error(exc, config, request_base_url=transport.base_url) from exc
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
