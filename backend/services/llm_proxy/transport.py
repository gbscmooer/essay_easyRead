"""Resolve direct or user-authorized proxy transport targets for LLM calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit


def _as_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _normalize_proxy_url(base_url: str, api_format: str) -> str:
    raw = str(base_url or "").strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("代理 Base URL 必须是 http:// 或 https:// 地址")

    path = parsed.path.rstrip("/")
    if api_format == "openai_chat":
        for suffix in ("/chat/completions", "/responses"):
            if path.endswith(suffix):
                path = path[: -len(suffix)].rstrip("/")
                break
    elif path.endswith("/v1/messages"):
        path = path[: -len("/v1/messages")].rstrip("/")
    elif path.endswith("/v1"):
        path = path[:-3].rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


@dataclass(frozen=True)
class ProxyTransportConfig:
    """Configuration for a gateway the user is authorized to call."""

    enabled: bool = False
    base_url: str = ""

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | "ProxyTransportConfig" | None,
        *,
        api_format: str,
    ) -> "ProxyTransportConfig":
        if isinstance(value, cls):
            return value
        raw = value if isinstance(value, Mapping) else {}
        return cls(
            enabled=_as_bool(raw.get("enabled", False)),
            base_url=_normalize_proxy_url(str(raw.get("base_url") or ""), api_format),
        )

    def validate(self) -> None:
        if self.enabled and not self.base_url:
            raise ValueError("启用代理转发后必须填写代理 Base URL")

    def to_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "base_url": self.base_url}


class _LLMTransportConfig(Protocol):
    base_url: str
    proxy: ProxyTransportConfig


@dataclass(frozen=True)
class ResolvedTransport:
    base_url: str
    proxied: bool


def resolve_transport(config: _LLMTransportConfig) -> ResolvedTransport:
    """Return the SDK target without adding or spoofing client headers."""

    config.proxy.validate()
    return ResolvedTransport(
        base_url=config.proxy.base_url if config.proxy.enabled else config.base_url,
        proxied=config.proxy.enabled,
    )
