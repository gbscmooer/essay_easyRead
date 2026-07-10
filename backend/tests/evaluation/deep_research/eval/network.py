"""Network guard for offline evaluation."""

from __future__ import annotations

import os
import socket
from contextlib import contextmanager
from functools import wraps


def network_allowed() -> bool:
    return os.environ.get("EVAL_ALLOW_NETWORK", "0").strip() in {"1", "true", "yes", "on"}


def guard_network_call(context: str = "network call") -> None:
    """Raise when ``EVAL_ALLOW_NETWORK=0`` and code is about to perform a network request."""
    if not network_allowed():
        raise RuntimeError(
            f"EVAL_ALLOW_NETWORK=0 — blocked {context}. "
            "Use mock fixtures or set EVAL_ALLOW_NETWORK=1 for live APIs."
        )


@contextmanager
def enforce_network_policy():
    """离线模式在 socket 层硬阻断网络，防止 runner 忘记显式调用 guard。"""
    if network_allowed():
        yield
        return

    original_socket = socket.socket
    original_create_connection = socket.create_connection

    class GuardedSocket(original_socket):
        def connect(self, *_args, **_kwargs):
            guard_network_call("socket connection")

        def connect_ex(self, *_args, **_kwargs):
            guard_network_call("socket connection")

    def blocked_create_connection(*_args, **_kwargs):
        guard_network_call("socket connection")

    socket.socket = GuardedSocket
    socket.create_connection = blocked_create_connection
    try:
        yield
    finally:
        socket.socket = original_socket
        socket.create_connection = original_create_connection


def offline_guard(func):
    """Decorator applying the process-local socket guard for one evaluation run."""
    @wraps(func)
    def wrapped(*args, **kwargs):
        with enforce_network_policy():
            return func(*args, **kwargs)

    return wrapped


# Back-compat alias used in tests/docs
require_offline = guard_network_call
