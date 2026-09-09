"""Stable configuration and response contracts for the LLM Core."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

API_FORMAT_OPENAI = "openai_chat"
API_FORMAT_ANTHROPIC = "anthropic_messages"
API_FORMATS = (API_FORMAT_OPENAI, API_FORMAT_ANTHROPIC)
THINKING_EFFORTS = ("off", "low", "medium", "high")


# The same shape drives backend defaults and the settings-page preset selector.
# A preset only fills a form; it never changes the selected provider implicitly.
PROVIDER_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "openai",
        "label": "OpenAI",
        "api_format": API_FORMAT_OPENAI,
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
    },
    {
        "id": "anthropic",
        "label": "Anthropic",
        "api_format": API_FORMAT_ANTHROPIC,
        "base_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-4-6",
    },
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "api_format": API_FORMAT_OPENAI,
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openai/gpt-4o-mini",
    },
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "api_format": API_FORMAT_OPENAI,
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
    },
    {
        "id": "siliconflow",
        "label": "SiliconFlow",
        "api_format": API_FORMAT_OPENAI,
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "deepseek-ai/DeepSeek-V3",
    },
    {
        "id": "custom",
        "label": "自定义",
        "api_format": API_FORMAT_OPENAI,
        "base_url": "",
        "default_model": "",
    },
)

_PRESETS_BY_ID = {item["id"]: item for item in PROVIDER_PRESETS}


def normalize_api_format(value: object, *, base_url: str = "", provider: str = "") -> str:
    """Normalize explicit protocol names and preserve legacy URL inference."""

    raw = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "openai": API_FORMAT_OPENAI,
        "openai_chat": API_FORMAT_OPENAI,
        "openai_completions": API_FORMAT_OPENAI,
        "chat_completions": API_FORMAT_OPENAI,
        "anthropic": API_FORMAT_ANTHROPIC,
        "anthropic_messages": API_FORMAT_ANTHROPIC,
        "messages": API_FORMAT_ANTHROPIC,
    }
    if raw in aliases:
        return aliases[raw]
    if raw:
        raise ValueError(f"unsupported LLM api_format: {raw}")
    return infer_api_format(base_url=base_url, provider=provider)


def infer_api_format(*, base_url: str = "", provider: str = "") -> str:
    """Infer only for legacy configs that predate the explicit api_format key."""

    preset = _PRESETS_BY_ID.get(str(provider or "").strip().lower())
    if preset and preset["id"] != "custom":
        return str(preset["api_format"])
    return API_FORMAT_ANTHROPIC if "anthropic" in str(base_url or "").lower() else API_FORMAT_OPENAI


def normalize_thinking_effort(value: object) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in THINKING_EFFORTS else "off"


def normalize_base_url(base_url: str, api_format: str) -> str:
    """Accept API roots or pasted full completion endpoints and return SDK roots."""

    raw = str(base_url or "").strip().rstrip("/")
    if not raw:
        return raw
    parsed = urlsplit(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("LLM Base URL 必须是 http:// 或 https:// 地址")

    path = parsed.path.rstrip("/")
    if api_format == API_FORMAT_OPENAI:
        for suffix in ("/chat/completions", "/responses"):
            if path.endswith(suffix):
                path = path[: -len(suffix)].rstrip("/")
                break
    else:
        if path.endswith("/v1/messages"):
            path = path[: -len("/v1/messages")].rstrip("/")
        elif path.endswith("/v1"):
            path = path[:-3].rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


@dataclass(frozen=True)
class LLMConfig:
    """Resolved single-provider configuration consumed by all LLM calls."""

    provider: str
    api_format: str
    api_key: str
    base_url: str
    model: str
    temperature: float = 0.2
    max_tokens: int = 16384
    thinking_effort: str = "off"
    timeout_seconds: float = 150.0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | "LLMConfig") -> "LLMConfig":
        if isinstance(value, cls):
            return value
        provider = str(value.get("provider") or "custom").strip().lower()
        base_url = str(value.get("base_url") or "").strip()
        api_format = normalize_api_format(
            value.get("api_format"), base_url=base_url, provider=provider,
        )
        normalized_url = normalize_base_url(base_url, api_format) if base_url else ""
        try:
            temperature = float(value.get("temperature", 0.2))
            max_tokens = int(value.get("max_tokens", 16384))
            timeout_seconds = float(value.get("timeout_seconds", 150.0))
        except (TypeError, ValueError) as exc:
            raise ValueError("LLM 高级参数格式无效") from exc
        return cls(
            provider=provider or "custom",
            api_format=api_format,
            api_key=str(value.get("api_key") or "").strip(),
            base_url=normalized_url,
            model=str(value.get("model") or "").strip(),
            temperature=max(0.0, min(2.0, temperature)),
            max_tokens=max(1, max_tokens),
            thinking_effort=normalize_thinking_effort(value.get("thinking_effort")),
            timeout_seconds=max(1.0, min(600.0, timeout_seconds)),
        )

    def with_overrides(self, **values: Any) -> "LLMConfig":
        payload = self.to_dict()
        payload.update({key: val for key, val in values.items() if val is not None})
        return LLMConfig.from_mapping(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "api_format": self.api_format,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "thinking_effort": self.thinking_effort,
            "timeout_seconds": self.timeout_seconds,
        }

    def validate_for_request(self) -> None:
        if not self.api_key:
            raise ValueError("API Key 未填写")
        if not self.base_url:
            raise ValueError("Base URL 未填写")
        if not self.model:
            raise ValueError("模型名称未填写")


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    api_format: str
    request_id: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
