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


def test_abstract_backfill_falls_back_to_semantic_scholar(monkeypatch):
    from backend.services import semantic_scholar
    from backend.services.deep_research import recall_engine

    async def openalex_down(self, dois):
        raise OpenAlexQuotaExhausted("budget", request=httpx.Request("GET", "https://x"),
                                     response=httpx.Response(429))

    async def openalex_partial(self, dois):
        return {"10.1/a": "from openalex"}

    asked = []

    async def s2(dois):
        asked.append(list(dois))
        return {d: f"s2 {d}" for d in dois if d != "10.1/none"}

    crossref_asked = []

    async def crossref(dois):
        crossref_asked.append(list(dois))
        return {"10.1/c": "from crossref"} if "10.1/c" in dois else {}

    from backend.services.deep_research import crossref_client

    monkeypatch.setattr(crossref_client, "get_abstracts_by_dois", crossref)
    monkeypatch.setattr(semantic_scholar, "get_abstracts_by_dois", s2)
    monkeypatch.setattr(OpenAlexProvider, "get_abstracts_by_dois", openalex_down)
    out = asyncio.run(recall_engine._default_backfill(["10.1/a", "10.1/b", "10.1/none"]))
    assert out == {"10.1/a": "s2 10.1/a", "10.1/b": "s2 10.1/b"}
    # OpenAlex 已补到的 DOI 不再查 Crossref / S2；Crossref 补到的不再查 S2
    monkeypatch.setattr(OpenAlexProvider, "get_abstracts_by_dois", openalex_partial)
    out = asyncio.run(recall_engine._default_backfill(["10.1/A", "10.1/b", "10.1/c"]))
    assert crossref_asked[-1] == ["10.1/b", "10.1/c"] and asked[-1] == ["10.1/b"]
    assert out["10.1/a"] == "from openalex" and out["10.1/c"] == "from crossref"


def test_crossref_abstracts_strip_jats(monkeypatch):
    from backend.services.deep_research import crossref_client

    seen = {}

    async def fake_get(url, params=None):
        seen.update(params)
        return {"message": {"items": [
            {"DOI": "10.1/A", "abstract": "<jats:title>Abstract</jats:title><jats:p>Links &amp; drops.</jats:p>"},
            {"DOI": "10.1/b"}]}}

    monkeypatch.setattr(crossref_client, "_crossref_get", fake_get)
    out = asyncio.run(crossref_client.get_abstracts_by_dois(["10.1/A", "10.1/b", "10.1/x,y"]))
    assert out == {"10.1/a": "Links & drops."}
    assert seen["filter"] == "doi:10.1/A,doi:10.1/b"  # 含逗号的 DOI 不能放进 filter


def test_crossref_abstracts_pause_after_rate_limit(monkeypatch):
    from backend.services.deep_research import crossref_client

    calls = []

    async def limited(url, params=None):
        calls.append(params["filter"])
        request = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("429", request=request, response=httpx.Response(429, request=request))

    monkeypatch.setattr(crossref_client, "_crossref_get", limited)
    monkeypatch.setattr(crossref_client, "_abstract_cooldown_until", 0.0)
    dois = [f"10.1/{i}" for i in range(60)]
    assert asyncio.run(crossref_client.get_abstracts_by_dois(dois)) == {}
    # 第一批 429 后暂停：其余批次和随后的调用都不再请求
    assert asyncio.run(crossref_client.get_abstracts_by_dois(dois)) == {}
    assert len(calls) == 1


def test_s2_batch_abstracts_cool_down_after_rate_limit(monkeypatch):
    from backend.services import semantic_scholar

    posts = []

    class Client:
        async def post(self, url, **kwargs):
            posts.append(kwargs["json"]["ids"])
            return httpx.Response(429, request=httpx.Request("POST", url))

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(semantic_scholar, "_get_http_client", lambda: Client())
    monkeypatch.setattr(semantic_scholar, "_s2_api_key", lambda: "")
    monkeypatch.setattr(semantic_scholar, "_batch_cooldown_until", 0.0)
    monkeypatch.setattr(semantic_scholar, "_last_request_time", 0.0)
    monkeypatch.setattr(semantic_scholar.asyncio, "sleep", no_sleep)
    assert asyncio.run(semantic_scholar.get_abstracts_by_dois(["10.1/a"])) == {}
    assert len(posts) == 2   # 无 key：重试一次后放弃，不抛错
    assert asyncio.run(semantic_scholar.get_abstracts_by_dois(["10.1/b"])) == {}
    assert len(posts) == 2   # 冷却期内直接跳过
