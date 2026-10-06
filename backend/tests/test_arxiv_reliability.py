"""Real HTTP/Atom adapters with offline transports; no paid models or network."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.services import rate_limiter as rl
from backend.services.http_diagnostics import capture_http_attempts, http_failure
from backend.services.deep_research import arxiv_client as ar
from backend.services.deep_research import preflight as pf
from backend.services.deep_research.research_loop.contracts import Action
from backend.services.deep_research.research_loop.tools import Tools

ATOM = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>https://arxiv.org/abs/2305.06983v2</id><title>Queue representation</title>
<published>2023-05-01T00:00:00Z</published><updated>2023-06-01T00:00:00Z</updated>
<summary>A bounded queue representation.</summary></entry></feed>'''


@pytest.fixture(autouse=True)
def isolated_sources(monkeypatch):
    monkeypatch.setattr(ar, "_query_cache", ar.OrderedDict())
    monkeypatch.setattr(ar, "_req_lock", asyncio.Lock())
    limiter = rl.DomainRateLimiter()
    limiter._rules = {"arxiv.org": (0, 1), "example.com": (0, 1)}
    monkeypatch.setattr(rl, "global_rate_limiter", limiter)
    monkeypatch.setattr(rl.random, "uniform", lambda *args: 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 502, 503, 504])
async def test_transient_status_then_success_keeps_each_attempt(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status if len(calls) == 1 else 200, text=ATOM)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with capture_http_attempts() as attempts:
            result = await rl.resilient_get(ar.ARXIV_API, client=client, max_retries=2,
                                            base_backoff=0, deadline=time.monotonic() + 1)
    assert result.status_code == 200 and len(calls) == 2
    assert [a["http_status"] for a in attempts] == [status, 200]
    assert attempts[0]["error_code"] == "HTTPStatusError"
    assert attempts[1]["error_code"] is None


@pytest.mark.asyncio
async def test_long_retry_after_returns_429_without_sending_early_retry():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "30", "Authorization": "never-export"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with capture_http_attempts() as attempts:
            with pytest.raises(httpx.HTTPStatusError) as caught:
                await rl.resilient_get(ar.ARXIV_API, client=client, max_retries=2,
                                       deadline=time.monotonic() + 0.1)
    assert len(calls) == 1
    assert attempts[0]["retry_after_seconds"] == 30
    assert http_failure(caught.value, attempts)["failure_kind"] == "rate_limited"
    assert rl.global_rate_limiter._cooldown["arxiv.org"] > time.monotonic()
    assert "never-export" not in str(attempts)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,kind", [(400, "http_error"), (403, "auth_error"), (500, "server_error")])
async def test_nonretryable_failure_retains_status_and_does_not_retry(status, kind):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with capture_http_attempts() as attempts:
            with pytest.raises(httpx.HTTPStatusError) as caught:
                await rl.resilient_get(ar.ARXIV_API, client=client)
    assert len(calls) == 1
    assert http_failure(caught.value, attempts)["http_status"] == status
    assert http_failure(caught.value, attempts)["failure_kind"] == kind


@pytest.mark.asyncio
async def test_network_timeout_retry_recovers_and_is_not_http_zero_results(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("sensitive text must not be copied", request=request)
        return httpx.Response(200, text=ATOM)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(ar, "_get_client", lambda: client)
        with capture_http_attempts() as attempts:
            rows = await ar.search_topic("bounded queue", limit=1)
    assert rows[0]["title"] == "Queue representation"
    assert [a["error_code"] for a in attempts] == ["ReadTimeout", None]
    assert "sensitive text" not in str(attempts)


@pytest.mark.asyncio
async def test_deadline_covers_waiting_without_consuming_unsent_http(monkeypatch):
    sent = []
    monkeypatch.setattr(rl, "account_source_request", lambda: sent.append(True))
    rl.global_rate_limiter.cool_down(ar.ARXIV_API, 1)
    client = SimpleNamespace(get=AsyncMock())
    with capture_http_attempts() as attempts:
        with pytest.raises(TimeoutError):
            await rl.resilient_get(ar.ARXIV_API, client=client, deadline=time.monotonic() + 0.02)
    assert not sent
    client.get.assert_not_awaited()
    assert attempts[0]["phase"] == "waiting" and attempts[0]["http_status"] is None
    assert rl.global_rate_limiter.get_semaphore("arxiv.org")._value == 1


@pytest.mark.asyncio
async def test_operation_deadline_interrupts_transport_and_releases_slot():
    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, text=ATOM)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with capture_http_attempts() as attempts:
            with pytest.raises(TimeoutError):
                await rl.resilient_get(ar.ARXIV_API, client=client, deadline=time.monotonic() + 0.02)
    assert attempts[0]["phase"] == "request" and attempts[0]["error_code"] == "TimeoutError"
    assert rl.global_rate_limiter.get_semaphore("arxiv.org")._value == 1


@pytest.mark.asyncio
async def test_entire_attempt_timeout_leaves_room_for_recovery_retry():
    calls = []

    async def handler(request):
        calls.append(request)
        if len(calls) == 1:
            await asyncio.sleep(0.2)
        return httpx.Response(200, text=ATOM)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with capture_http_attempts() as attempts:
            response = await rl.resilient_get(ar.ARXIV_API, client=client, timeout=0.02,
                max_retries=2, base_backoff=0, deadline=time.monotonic() + 1)
    assert response.status_code == 200 and len(calls) == 2
    assert [a["error_code"] for a in attempts] == ["ReadTimeout", None]
    assert attempts[0]["elapsed_ms"] < 150


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["<html>upstream failure</html>", "broken XML", '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors#query</id></entry></feed>'])
async def test_invalid_success_body_is_not_cached_or_reported_empty(body, monkeypatch):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body))) as client:
        monkeypatch.setattr(ar, "_get_client", lambda: client)
        with pytest.raises(ar.ArxivResponseError):
            await ar.search_topic("bounded queue")
    assert not ar._query_cache


@pytest.mark.asyncio
async def test_simultaneous_queries_are_serial_and_cache_is_explicit(monkeypatch):
    active, peak = 0, 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, text=ATOM)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(ar, "_get_client", lambda: client)
        await asyncio.gather(ar.search_topic("queue input"), ar.search_topic("queue output"))
        with capture_http_attempts() as attempts:
            await ar.search_topic("queue input")
    assert peak == 1 and attempts == [{"phase": "cache", "http_status": 200, "elapsed_ms": 0}]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [(429, "rate_limited"), (403, "auth_error"), (503, "error"), (200, "ok")])
async def test_real_arxiv_preflight_classification(status, expected, monkeypatch):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, text=ATOM))) as client:
        monkeypatch.setattr(ar, "_get_client", lambda: client)
        result = await pf._probe_arxiv()
    assert result["role"] == "supplement" and result["status"] == expected
    assert result["http_status"] == status
    assert len(result["http_attempts"]) == 1
    if status == 429:
        assert "429" in result["detail"]
        assert pf.dead_source_names([result]) == set()


def test_real_preflight_endpoint_accepts_supplement_and_cors(client, monkeypatch):
    from backend.services import ai_config
    from backend.config import settings

    monkeypatch.setattr(ai_config, "get_research_config", lambda: {"provider": "local"})
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=ATOM))
    mock_client = httpx.AsyncClient(transport=transport)
    monkeypatch.setattr(ar, "_get_client", lambda: mock_client)
    response = client.get("/api/exploration/preflight", headers={"Origin": settings.frontend_origin})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == settings.frontend_origin
    arxiv = next(s for s in response.json()["sources"] if s["name"] == "arxiv")
    assert arxiv["role"] == "supplement" and arxiv["http_status"] == 200
    asyncio.run(mock_client.aclose())


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_workflow_source_audit_keeps_failure_and_other_sources(status, monkeypatch):
    async def empty_source(*args, **kwargs):
        return []

    monkeypatch.setattr("backend.services.deep_research.recall_engine._default_search_one", empty_source)
    tools = Tools.__new__(Tools)
    tools.repo = SimpleNamespace(run=lambda: SimpleNamespace(options={"search_online": True}),
                                 protocol=lambda: {"execution_revision": "question_loop_v19"})
    tools.resources = SimpleNamespace(source_exhausted=False)
    tools.provider_state = {}
    tools.plan = lambda: {"strategy": "material_first_v3"}
    tools.admit = AsyncMock(return_value=([], [], []))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, headers={"Retry-After": "30"}))) as mock_client:
        monkeypatch.setattr(ar, "_get_client", lambda: mock_client)
        result = await tools.search(Action(tool="search_papers", question_id="main", query="bounded queue"))
    audit = result["assets"][-1]["inline_payload"]
    assert [r["provider"] for r in audit["attempts"]] == ["openalex", "arxiv", "scopus"]
    failure = audit["attempts"][1]
    assert failure["status"] == "error" and failure["http_status"] == status
    assert failure["failure_kind"] == ("rate_limited" if status == 429 else "server_error")
    assert failure["http_attempts"][0]["retry_after_seconds"] == 30


@pytest.mark.asyncio
async def test_cancelled_workflow_search_does_not_mask_cancellation(monkeypatch):
    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr("backend.services.deep_research.recall_engine._default_search_one", cancelled)
    tools = Tools.__new__(Tools)
    tools.repo = SimpleNamespace(run=lambda: SimpleNamespace(options={"search_online": True}),
                                 protocol=lambda: {"execution_revision": "question_loop_v19"})
    tools.resources = SimpleNamespace(source_exhausted=False)
    tools.provider_state = {}
    tools.plan = lambda: {}
    with pytest.raises(asyncio.CancelledError):
        await tools.search(Action(tool="search_papers", question_id="main", query="bounded queue"))


@pytest.mark.asyncio
async def test_attempt_capture_is_task_local_even_through_wait_for():
    async def scoped(status):
        async def request():
            from backend.services.http_diagnostics import record_http_attempt
            record_http_attempt({"http_status": status})
        with capture_http_attempts() as attempts:
            await asyncio.wait_for(request(), timeout=1)
        return attempts
    first, second = await asyncio.gather(scoped(200), scoped(429))
    assert first == [{"http_status": 200}] and second == [{"http_status": 429}]
