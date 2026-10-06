"""Persist error categories without provider messages, request bodies or secrets."""

import re

from ...llm import LLMError


def model_error(exc):
    code = getattr(exc, "code", None)
    result = {"error_code": code if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code)
              else type(exc).__name__, "error_type": type(exc).__name__}
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
        result["http_status"] = status
    reason = getattr(exc, "finish_reason", None)
    if reason in ("stop", "length", "max_tokens", "content_filter", "tool_calls"):
        result["finish_reason"] = reason
    # Adapters wrap SDK errors. Inspect types only; provider text may contain keys.
    cause, seen = exc, set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        name = type(cause).__name__
        if name in ("APITimeoutError", "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout", "TimeoutError"):
            result["failure_kind"] = "timeout"
            break
        if name in ("APIConnectionError", "ConnectError", "ReadError", "WriteError"):
            result["failure_kind"] = "connection"
            break
        cause = cause.__cause__
    if isinstance(exc, LLMError) and exc.code.startswith("BUDGET_"):
        result["failure_kind"] = "resource_exhausted"
    return result
