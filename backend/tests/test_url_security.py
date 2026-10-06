"""url_security 结构化校验失败契约测试（PDF 下载 DNS 阻断专项配套）。

当前地址校验边界见 document.md §2.10；下列用例检验失败分类：
- 198.18/私网/链路本地字面量与解析地址一律拒绝，结构化标注 NON_PUBLIC_ADDRESS_BLOCKED；
- 同一域名混合公网+非公网解析结果整体拒绝，不挑公网放行；
- DNS 解析失败单独归类 DNS_RESOLUTION_FAILED；
- URLSecurityError 是 ValueError 子类，旧调用方捕获行为不变。
"""

import pytest

from backend.services.url_security import (
    ERROR_DNS_RESOLUTION_FAILED,
    ERROR_INVALID_URL,
    ERROR_NON_PUBLIC_BLOCKED,
    STAGE_RESOLVE,
    STAGE_VALIDATE,
    URLSecurityError,
    validate_public_http_url,
)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x",
    "http://10.0.0.8/document.pdf",
    "http://192.168.1.1/x",
    "http://169.254.169.254/latest/meta-data",
    "http://198.18.0.30/paper.pdf",
    "http://[::1]/x",
    "http://[fe80::1]/x",
])
def test_non_public_literals_rejected_with_structured_code(url):
    with pytest.raises(URLSecurityError) as exc_info:
        validate_public_http_url(url)
    assert exc_info.value.error_code == ERROR_NON_PUBLIC_BLOCKED
    assert exc_info.value.stage == STAGE_VALIDATE


@pytest.mark.parametrize("hostname", ["localhost", "svc.internal", "printer.local"])
def test_local_hostnames_rejected_as_non_public(hostname):
    with pytest.raises(URLSecurityError) as exc_info:
        validate_public_http_url(f"http://{hostname}/x")
    assert exc_info.value.error_code == ERROR_NON_PUBLIC_BLOCKED


@pytest.mark.parametrize("url", [
    "https://user:pass@example.com/paper.pdf",
    "ftp://example.com/paper.pdf",
    "not a url",
])
def test_invalid_urls_rejected_with_structured_code(url):
    with pytest.raises(URLSecurityError) as exc_info:
        validate_public_http_url(url)
    assert exc_info.value.error_code == ERROR_INVALID_URL
    assert exc_info.value.stage == STAGE_VALIDATE


def test_mixed_public_and_non_public_dns_answers_rejected_entirely(monkeypatch):
    monkeypatch.setattr(
        "backend.services.url_security.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("151.101.131.42", 443)),
            (2, 1, 6, "", ("198.18.0.30", 443)),
        ],
    )
    with pytest.raises(URLSecurityError) as exc_info:
        validate_public_http_url("https://arxiv.org/pdf/1706.03762")
    assert exc_info.value.error_code == ERROR_NON_PUBLIC_BLOCKED
    assert "198.18.0.30" in exc_info.value.detail
    assert "151.101.131.42" in exc_info.value.detail


def test_dns_failure_classified_as_resolve_stage(monkeypatch):
    def unreachable(*args, **kwargs):
        raise OSError("name resolution temporarily failed")
    monkeypatch.setattr("backend.services.url_security.socket.getaddrinfo", unreachable)
    with pytest.raises(URLSecurityError) as exc_info:
        validate_public_http_url("https://arxiv.org/pdf/1706.03762")
    assert exc_info.value.error_code == ERROR_DNS_RESOLUTION_FAILED
    assert exc_info.value.stage == STAGE_RESOLVE


def test_public_hostname_passes(monkeypatch):
    monkeypatch.setattr(
        "backend.services.url_security.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("151.101.131.42", 443))],
    )
    assert validate_public_http_url("https://arxiv.org/pdf/1706.03762") is None


def test_security_error_remains_value_error_for_legacy_callers():
    # 兼容契约：既有 except ValueError / pytest.raises(ValueError) 不被破坏
    with pytest.raises(ValueError, match="非公网"):
        validate_public_http_url("http://198.18.0.30/paper.pdf")
