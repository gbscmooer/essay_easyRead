"""Security boundary for the browser-accessible local PTY terminal."""

from __future__ import annotations

import ipaddress
import secrets
import time
from collections.abc import Callable
from urllib.parse import urlsplit


TERMINAL_PROTOCOL_PREFIX = "essay-terminal."
TERMINAL_TICKET_TTL_SECONDS = 30
MAX_PENDING_TERMINAL_TICKETS = 32


def _canonical_origin(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return parsed.scheme, parsed.hostname, port
    except ValueError:
        return None


def origin_matches(origin: str | None, configured_origin: str) -> bool:
    """Compare browser origins, treating omitted default ports canonically."""
    if not origin:
        return False
    supplied = _canonical_origin(origin)
    configured = _canonical_origin(configured_origin)
    return supplied is not None and supplied == configured


def _local_address(value: str) -> bool:
    if value.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(value.strip("[]"))
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def trusted_local_peer(client_host: str, forwarded_for: str | None = None) -> bool:
    """Allow direct loopback or an entirely local/private reverse-proxy chain."""
    client_is_loopback = client_host.lower() == "localhost"
    client_is_local = client_is_loopback or _local_address(client_host)
    if not client_is_local:
        return False
    if not forwarded_for:
        if client_is_loopback:
            return True
        try:
            return ipaddress.ip_address(client_host.strip("[]")).is_loopback
        except ValueError:
            return False
    forwarded_hosts = [item.strip() for item in forwarded_for.split(",") if item.strip()]
    return bool(forwarded_hosts) and all(_local_address(host) for host in forwarded_hosts)


class TerminalTicketStore:
    """Bounded in-memory store for short-lived, single-use terminal tickets."""

    def __init__(
        self,
        *,
        ttl_seconds: int = TERMINAL_TICKET_TTL_SECONDS,
        max_pending: int = MAX_PENDING_TERMINAL_TICKETS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_pending = max_pending
        self._clock = clock
        self._tickets: dict[str, float] = {}

    def _prune(self, now: float) -> None:
        expired = [ticket for ticket, expires_at in self._tickets.items() if expires_at <= now]
        for ticket in expired:
            self._tickets.pop(ticket, None)

    def issue(self) -> str:
        now = self._clock()
        self._prune(now)
        while len(self._tickets) >= self.max_pending:
            oldest = min(self._tickets, key=self._tickets.__getitem__)
            self._tickets.pop(oldest, None)
        ticket = secrets.token_urlsafe(32)
        self._tickets[ticket] = now + self.ttl_seconds
        return ticket

    def consume_protocol(self, requested_protocols: str) -> str | None:
        now = self._clock()
        self._prune(now)
        for protocol in (item.strip() for item in requested_protocols.split(",")):
            if not protocol.startswith(TERMINAL_PROTOCOL_PREFIX):
                continue
            ticket = protocol.removeprefix(TERMINAL_PROTOCOL_PREFIX)
            if ticket and self._tickets.pop(ticket, None) is not None:
                return protocol
        return None

    def clear(self) -> None:
        self._tickets.clear()

    def __contains__(self, ticket: str) -> bool:
        return ticket in self._tickets

    def __len__(self) -> int:
        self._prune(self._clock())
        return len(self._tickets)


terminal_ticket_store = TerminalTicketStore()
