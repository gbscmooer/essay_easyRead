"""Shared URL validation for outbound requests that may contain untrusted targets."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


def _require_global_address(value: str) -> None:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError as exc:
        raise ValueError(f"URL 主机解析为非法 IP: {value}") from exc
    if not address.is_global:
        raise ValueError(f"不允许访问内部或非公网 IP 地址: {address}")


def validate_public_http_url(url: str, *, resolve_dns: bool = True) -> None:
    """Reject credentials, local names, and non-public literal/resolved addresses.

    DNS validation is fail-closed and checks every returned address.  Callers must repeat
    validation for every redirect target; this prevents public landing pages from pivoting
    a downloader into loopback, link-local, or private services.
    """
    try:
        parsed = urlsplit(str(url))
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("URL 格式无效") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("仅允许公网 HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL 不得携带用户凭据")

    hostname = parsed.hostname.rstrip(".").casefold()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("不允许访问本地或内部主机")

    try:
        literal = ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        literal = None
    if literal is not None:
        _require_global_address(str(literal))
        return
    if not resolve_dns:
        return

    try:
        rows = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f"URL 主机无法安全解析: {hostname}") from exc
    addresses = {row[4][0] for row in rows if row[4]}
    if not addresses:
        raise ValueError(f"URL 主机无可用公网地址: {hostname}")
    for address in addresses:
        _require_global_address(address)
