"""Task-local, bounded HTTP diagnostics without URLs, headers or response bodies."""

from contextlib import contextmanager
from contextvars import ContextVar

import httpx

_attempts = ContextVar("source_http_attempts", default=None)


@contextmanager
def capture_http_attempts():
    attempts = []
    token = _attempts.set(attempts)
    try:
        yield attempts
    finally:
        _attempts.reset(token)


def record_http_attempt(record):
    attempts = _attempts.get()
    if attempts is not None and len(attempts) < 12:
        attempts.append(dict(record))


def http_failure(exc, attempts):
    """Keep the final exception distinct from the last observed HTTP response."""
    status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
    last = next((a.get("http_status") for a in reversed(attempts) if a.get("http_status")), None)
    kind = "http_error" if status else "tool_error"
    if status == 429:
        kind = "rate_limited"
    elif status in (401, 403):
        kind = "auth_error"
    elif status and status >= 500:
        kind = "server_error"
    elif isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        kind = "timeout"
    elif isinstance(exc, httpx.RequestError):
        kind = "network_error"
    return {"error_code": type(exc).__name__, "failure_kind": kind,
            "http_status": status, "last_http_status": last}
