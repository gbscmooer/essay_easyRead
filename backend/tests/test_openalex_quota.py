"""OpenAlex API key 与每日额度用尽（2026-10 起无 key 请求共用按出口 IP 计的每日额度）。"""

import asyncio

import httpx
import pytest

from backend.services.ai_config import mask_config
from backend.services.deep_research.preflight import dead_source_names
from backend.services.deep_research.providers import openalex
from backend.services.deep_research.providers.openalex import OpenAlexProvider, OpenAlexQuotaExhausted

_BUDGET_BODY = {"error": "Rate limit exceeded", "message": "Insufficient budget. This request has no API key"}


@pytest.fixture
def mock_http(monkeypatch):
    seen: list[httpx.Request] = []
    replies: list[httpx.Response] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return replies.pop(0) if replies else httpx.Response(200, json={"results": []})

    monkeypatch.setattr(openalex.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    return seen, replies


def test_api_key_is_sent_as_header_not_in_url(mock_http):
    seen, _ = mock_http
    provider = OpenAlexProvider(email="a@b.c", api_key="secret123")
    asyncio.run(provider._get("/works", {"search": "x"}))
    assert seen[0].headers["Authorization"] == "Bearer secret123"
    assert "secret123" not in str(seen[0].url) and "mailto=a%40b.c" in str(seen[0].url)


def test_quota_exhausted_fails_fast_and_skips_until_reset(mock_http):
    seen, replies = mock_http
    replies.append(httpx.Response(429, json=_BUDGET_BODY))
    provider = OpenAlexProvider(api_key="")
    with pytest.raises(OpenAlexQuotaExhausted):
        asyncio.run(provider._get("/works", {"search": "x"}))
    # 不做 4 次带退避的重试；之后的请求在 UTC 零点前不再发出
    with pytest.raises(OpenAlexQuotaExhausted):
        asyncio.run(provider._get("/works", {"search": "y"}))
    assert len(seen) == 1 and provider.last_request_status == "quota_exhausted"


def test_plain_rate_limit_still_retries(mock_http, monkeypatch):
    seen, replies = mock_http
    replies.append(httpx.Response(429, json={"error": "too many requests"}))

    async def no_sleep(_):
        return None

    monkeypatch.setattr(openalex.asyncio, "sleep", no_sleep)
    assert asyncio.run(OpenAlexProvider(api_key="")._get("/works")) == {"results": []}
    assert len(seen) == 2


def test_health_check_reports_quota_and_source_is_dead(mock_http):
    _, replies = mock_http
    replies.append(httpx.Response(429, json=_BUDGET_BODY))
    out = asyncio.run(OpenAlexProvider(api_key="").health_check())
    assert out["status"] == "quota_exhausted" and "OpenAlex API Key" in out["detail"]
    assert dead_source_names([out]) == {"openalex"}
    replies.append(httpx.Response(429, json=_BUDGET_BODY))
    keyed = asyncio.run(OpenAlexProvider(api_key="k").health_check())
    assert "API key 的每日额度已用完" in keyed["detail"]


def test_key_resolution_and_masking(monkeypatch):
    monkeypatch.setattr("backend.services.ai_config.get_research_config", lambda: {"openalex_api_key": "fromcfg"})
    assert OpenAlexProvider().api_key == "fromcfg"
    monkeypatch.setattr("backend.services.ai_config.get_research_config", lambda: {})
    monkeypatch.setattr("backend.config.settings.openalex_api_key", "fromenv")
    assert OpenAlexProvider().api_key == "fromenv"
    assert OpenAlexProvider(api_key="explicit").api_key == "explicit"
    masked = mask_config({"research": {"openalex_api_key": "abcdefgh1234"}})
    assert masked["research"]["openalex_api_key"] == "***1234"
