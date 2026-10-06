"""限速器与弹性 HTTP 请求测试。"""

import asyncio
import email.utils
import time
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from backend.services.rate_limiter import (
    RETRY_AFTER_CAP_SECONDS,
    AdaptiveThrottle,
    DomainRateLimiter,
    parse_retry_after,
    resilient_get,
)


def test_parse_retry_after():
    assert parse_retry_after({"retry-after": "5"}) == 5.0
    assert parse_retry_after({}, default_wait=3.5) == 3.5
    assert parse_retry_after({"retry-after": "invalid"}, default_wait=4.0) == 4.0


def test_parse_retry_after_capped():
    """Retry-After 巨值（配额次日重置）封顶：真睡满会把整个 run 挂死在 sleep 里，
    协作式暂停的安全点也永远到不了。"""
    assert parse_retry_after({"retry-after": "120"}) == RETRY_AFTER_CAP_SECONDS
    assert parse_retry_after({"retry-after": "86400"}) == RETRY_AFTER_CAP_SECONDS
    # HTTP 日期形式的远期窗口同样封顶
    far = email.utils.formatdate(time.time() + 3600, usegmt=True)
    assert parse_retry_after({"retry-after": far}) == RETRY_AFTER_CAP_SECONDS
    # 巨值默认等待同样封顶
    assert parse_retry_after({}, default_wait=999.0) == RETRY_AFTER_CAP_SECONDS


@pytest.mark.asyncio
async def test_domain_rate_limiter_throttles():
    limiter = DomainRateLimiter()
    # 连续请求同一域名应触发等待
    t0 = asyncio.get_event_loop().time()
    await limiter.throttle("https://api.unpaywall.org/v2/10.1234/test")
    await limiter.throttle("https://api.unpaywall.org/v2/10.5678/test")
    elapsed = asyncio.get_event_loop().time() - t0
    assert elapsed >= 0.08  # rule is 0.1s interval


@pytest.mark.asyncio
async def test_resilient_get_success():
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_resp)

    resp = await resilient_get(
        "https://example.com/test",
        client=mock_client,
        max_retries=2,
    )
    assert resp.status_code == 200
    assert mock_client.get.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/private",
    "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.8/document.pdf",
    "http://[::1]/document.pdf",
])
async def test_resilient_get_public_network_guard_blocks_private_literals(url):
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    with pytest.raises(ValueError, match="不允许"):
        await resilient_get(
            url,
            client=mock_client,
            max_retries=1,
            validate_public_network=True,
        )
    mock_client.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_resilient_get_public_network_guard_checks_dns(monkeypatch):
    monkeypatch.setattr(
        "backend.services.url_security.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("192.168.1.20", 443))],
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    with pytest.raises(ValueError, match="非公网"):
        await resilient_get(
            "https://papers.example/document.pdf",
            client=mock_client,
            max_retries=1,
            validate_public_network=True,
        )
    mock_client.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_resilient_get_dns_block_not_retried(monkeypatch):
    """确定性地址阻断（fake-IP/非公网）：URLSecurityError 原样穿透，不进入 HTTP 重试。"""
    from backend.services.url_security import URLSecurityError

    monkeypatch.setattr(
        "backend.services.url_security.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("198.18.0.30", 443))],
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    with pytest.raises(URLSecurityError) as exc_info:
        await resilient_get(
            "https://arxiv.org/pdf/1706.03762",
            client=mock_client,
            max_retries=3,
            validate_public_network=True,
        )
    assert exc_info.value.error_code == "NON_PUBLIC_ADDRESS_BLOCKED"
    mock_client.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_resilient_get_redirect_to_non_public_blocked_before_request(monkeypatch):
    """公网入口重定向到非公网目标：在下一跳请求前拒绝。"""
    from backend.services.url_security import ERROR_NON_PUBLIC_BLOCKED, URLSecurityError

    def fake_dns(host, *args, **kwargs):
        if str(host) == "journals.example":
            return [(2, 1, 6, "", ("93.184.216.34", 443))]
        return [(2, 1, 6, "", ("198.18.0.99", 443))]
    monkeypatch.setattr("backend.services.url_security.socket.getaddrinfo", fake_dns)

    redirect = MagicMock(spec=httpx.Response)
    redirect.status_code = 302
    redirect.is_redirect = True
    redirect.headers = {"location": "https://evil.example/paper.pdf"}
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=redirect)

    with pytest.raises(URLSecurityError) as exc_info:
        await resilient_get(
            "https://journals.example/paper",
            client=mock_client,
            max_retries=2,
            validate_public_network=True,
        )
    assert exc_info.value.error_code == ERROR_NON_PUBLIC_BLOCKED
    # 只发出第一跳：重定向目标在校验阶段被拦，未发第二次请求
    assert mock_client.get.await_count == 1


@pytest.mark.asyncio
async def test_resilient_get_drops_credentials_on_https_to_http_redirect(monkeypatch):
    """A scheme downgrade is a different origin even when the host is unchanged."""
    monkeypatch.setattr(
        "backend.services.url_security.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    redirect = MagicMock(spec=httpx.Response)
    redirect.status_code = 302
    redirect.is_redirect = True
    redirect.headers = {"location": "http://api.elsevier.com/paper.pdf"}
    redirect.url = httpx.URL("https://api.elsevier.com/article")
    final = MagicMock(spec=httpx.Response)
    final.status_code = 200
    final.is_redirect = False
    final.raise_for_status = MagicMock()
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(side_effect=[redirect, final])

    await resilient_get(
        "https://api.elsevier.com/article",
        headers={"X-ELS-APIKey": "secret", "Authorization": "Bearer secret"},
        client=mock_client, max_retries=1, validate_public_network=True,
    )
    assert mock_client.get.await_count == 2
    second_headers = mock_client.get.await_args_list[1].kwargs["headers"]
    assert all(key.lower() not in {"x-els-apikey", "authorization"} for key in second_headers)


@pytest.mark.asyncio
async def test_resilient_get_retries_on_429():
    req = httpx.Request("GET", "https://api.openalex.org/works")
    mock_429 = httpx.Response(429, headers={"retry-after": "1"}, request=req)
    mock_200 = httpx.Response(200, request=req)

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(side_effect=[mock_429, mock_200])

    with patch("asyncio.sleep", new=AsyncMock()) as mock_sleep:
        resp = await resilient_get(
            "https://api.openalex.org/works",
            client=mock_client,
            max_retries=3,
        )
        assert resp.status_code == 200
        assert mock_client.get.call_count == 2
        assert mock_sleep.called


# ── AdaptiveThrottle：付费源自适应限速 ───────────────────────────────────────

def test_adaptive_throttle_success_shrinks_to_floor():
    t = AdaptiveThrottle(start=0.35, floor=0.1, cap=8.0, shrink=0.8)
    assert t.interval == pytest.approx(0.35)
    for _ in range(20):
        t.on_success()
    assert t.interval == pytest.approx(0.1)


def test_adaptive_throttle_rate_limited_grows_and_respects_retry_after():
    t = AdaptiveThrottle(start=0.35, floor=0.1, cap=8.0, grow=1.6)
    t.on_rate_limited(0.0)
    assert t.interval == pytest.approx(0.35 * 1.6)
    # Retry-After 是服务端指令，优先于 ×grow
    t.on_rate_limited(retry_after=5.0)
    assert t.interval == pytest.approx(5.0)
    t.on_rate_limited(0.0)
    assert t.interval == pytest.approx(8.0)  # 封顶


def test_adaptive_throttle_start_clamped_to_bounds():
    t = AdaptiveThrottle(start=99.0, floor=0.1, cap=8.0)
    assert t.interval == 8.0


def test_adaptive_throttle_rate_limited_caps_slot_push():
    """429 的 Retry-After 巨值不得把共享发射槽推到数小时后（否则同域请求集体挂起）。"""
    t = AdaptiveThrottle(start=0.35, floor=0.1, cap=8.0)
    t.on_rate_limited(retry_after=3600.0)
    assert t._next_slot - time.monotonic() <= RETRY_AFTER_CAP_SECONDS + 1.0
    assert t.interval == pytest.approx(8.0)


@pytest.mark.asyncio
async def test_adaptive_throttle_caps_concurrency():
    """并发信号量封顶：6 个任务并发抢锁，同时在飞的不超过 concurrency=2。"""
    t = AdaptiveThrottle(start=0.0, floor=0.0, cap=8.0, concurrency=2)
    active = 0
    max_active = 0

    async def worker():
        nonlocal active, max_active
        async with t:
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.03)
            active -= 1

    await asyncio.gather(*[worker() for _ in range(6)])
    assert max_active == 2


@pytest.mark.asyncio
async def test_adaptive_throttle_spacing_serializes_launch():
    """发射间隔 ≥ interval：两个 acquire 在 interval=0.08 下，第二个至少晚 0.08。"""
    t = AdaptiveThrottle(start=0.08, floor=0.08, cap=8.0, concurrency=4)
    stamps: list[float] = []

    async def worker():
        async with t:
            stamps.append(asyncio.get_event_loop().time())

    await asyncio.gather(worker(), worker())
    assert max(stamps) - min(stamps) >= 0.07
