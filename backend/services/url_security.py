"""Shared URL validation for outbound requests that may contain untrusted targets."""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit

# 非公网地址拦截开关：默认关闭。代理 TUN 的 fake-IP（198.18.0.0/15）会让正常出版商
# 域名解析为非公网地址，导致全文下载全部被拒；设 URL_PUBLIC_ADDRESS_CHECK=1 恢复拦截。
PUBLIC_ADDRESS_CHECK = os.getenv("URL_PUBLIC_ADDRESS_CHECK", "0") == "1"

# 结构化错误码（下载审计外显用）。错误码只是原因分类，不做二次转义：
# 例如 NON_PUBLIC_ADDRESS_BLOCKED 不得自动改名为"代理错误"。
ERROR_INVALID_URL = "INVALID_URL"
ERROR_DNS_RESOLUTION_FAILED = "DNS_RESOLUTION_FAILED"
ERROR_NON_PUBLIC_BLOCKED = "NON_PUBLIC_ADDRESS_BLOCKED"

# 校验阶段：resolve=DNS 解析本身失败；validate=格式/凭据/地址校验拒绝
STAGE_RESOLVE = "resolve"
STAGE_VALIDATE = "validate"


class URLSecurityError(ValueError):
    """URL 安全校验失败，携带结构化归因字段。

    继承 ValueError 保持既有调用方（except ValueError / pytest.raises）兼容；
    error_code/stage 供下载链路写入 attempt_log，detail 只含解析出的 IP/主机名
    等诊断信息，不含查询参数中的令牌、请求头或密钥。
    """

    def __init__(self, message: str, *, error_code: str, stage: str, detail: str = ""):
        super().__init__(message)
        self.error_code = error_code
        self.stage = stage
        self.detail = detail


def _require_global_address(value: str) -> None:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError as exc:
        raise URLSecurityError(
            f"URL 主机解析为非法 IP: {value}",
            error_code=ERROR_NON_PUBLIC_BLOCKED,
            stage=STAGE_VALIDATE,
            detail=f"address={value}",
        ) from exc
    if not address.is_global:
        raise URLSecurityError(
            f"不允许访问内部或非公网 IP 地址: {address}",
            error_code=ERROR_NON_PUBLIC_BLOCKED,
            stage=STAGE_VALIDATE,
            detail=f"address={address}",
        )


def validate_public_http_url(url: str, *, resolve_dns: bool = True) -> None:
    """Reject credentials, local names, and non-public literal/resolved addresses.

    DNS validation is fail-closed and checks every returned address.  Callers must repeat
    validation for every redirect target; this prevents public landing pages from pivoting
    a downloader into loopback, link-local, or private services.

    失败统一抛 URLSecurityError（ValueError 子类），error_code/stage 可被上层
    下载器结构化记录；同一域名同时返回公网与非公网地址时整体拒绝，不挑公网结果放行。
    """
    try:
        parsed = urlsplit(str(url))
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise URLSecurityError(
            "URL 格式无效",
            error_code=ERROR_INVALID_URL,
            stage=STAGE_VALIDATE,
        ) from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise URLSecurityError(
            "仅允许公网 HTTP(S) URL",
            error_code=ERROR_INVALID_URL,
            stage=STAGE_VALIDATE,
        )
    if parsed.username is not None or parsed.password is not None:
        raise URLSecurityError(
            "URL 不得携带用户凭据",
            error_code=ERROR_INVALID_URL,
            stage=STAGE_VALIDATE,
        )
    if not PUBLIC_ADDRESS_CHECK:
        return

    hostname = parsed.hostname.rstrip(".").casefold()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise URLSecurityError(
            "不允许访问本地或内部主机",
            error_code=ERROR_NON_PUBLIC_BLOCKED,
            stage=STAGE_VALIDATE,
            detail=f"hostname={hostname}",
        )

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
        raise URLSecurityError(
            f"URL 主机无法安全解析: {hostname}",
            error_code=ERROR_DNS_RESOLUTION_FAILED,
            stage=STAGE_RESOLVE,
            detail=f"hostname={hostname}",
        ) from exc
    addresses = {row[4][0] for row in rows if row[4]}
    if not addresses:
        raise URLSecurityError(
            f"URL 主机无可用公网地址: {hostname}",
            error_code=ERROR_DNS_RESOLUTION_FAILED,
            stage=STAGE_RESOLVE,
            detail=f"hostname={hostname}",
        )
    offending = []
    for address in addresses:
        try:
            _require_global_address(address)
        except URLSecurityError as exc:
            offending.append(str(address))
            if len(offending) >= 4:
                break
    if offending:
        raise URLSecurityError(
            f"不允许访问内部或非公网 IP 地址: {offending[0]}",
            error_code=ERROR_NON_PUBLIC_BLOCKED,
            stage=STAGE_VALIDATE,
            detail=f"hostname={hostname}; resolved={sorted(addresses)}; blocked={sorted(offending)}",
        )
