"""Safe LLM proxy transport configuration.

This module only selects an explicitly configured OpenAI/Anthropic-compatible
gateway.  It intentionally does not emulate another application's headers,
device identity, or subscription authentication.
"""

from .transport import ProxyTransportConfig, ResolvedTransport, resolve_transport

__all__ = ["ProxyTransportConfig", "ResolvedTransport", "resolve_transport"]
