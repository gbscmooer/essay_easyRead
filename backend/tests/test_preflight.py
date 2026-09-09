"""数据源预检（preflight）单测。

守住的行为契约：
1. health_check 是**权限**探测而非 ping 可达：401/403 → auth_error（确定性失败）、
   429 → rate_limited（权限仍在，不判死）、网络异常 → network_error、无 Key → unconfigured；
2. 预检的源组合与 providers.get_provider 的选源逻辑同构（auto+Key → Scopus 主 + OpenAlex 备）；
3. 判死口径：rate_limited 不判死；预检结果短缓存避免重复烧配额；
4. recall 主源预熔断：预检判死的 FailoverProvider 主源不再被调用；
5. 全部数据源判死 → run_exploration 在 stage 0 快速失败，decompose/检索不再执行；
6. /api/exploration/preflight 返回结构化体检结果。
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from backend.services.deep_research import explorer
from backend.services.deep_research.explorer import recall_papers, run_exploration
from backend.services.deep_research.preflight import (
    any_source_ok, dead_source_names, preflight_research_sources,
)
from backend.services.deep_research.providers import FailoverProvider
from backend.services.deep_research.providers.openalex import OpenAlexProvider
from backend.services.deep_research.providers.scopus import ScopusProvider


# ── 1) health_check 状态分类 ─────────────────────────────────────────────────

def _fake_client(responses=None, side_effect=None):
    client = MagicMock()
    if side_effect is not None:
        client.get = AsyncMock(side_effect=side_effect)
    else:
        client.get = AsyncMock(side_effect=responses or [])
    client.aclose = AsyncMock()
    return client


def _resp(status: int):
    r = MagicMock()
    r.status_code = status
    return r


@pytest.mark.parametrize("status,expected", [
    (200, "ok"),
    (401, "auth_error"),
    (403, "auth_error"),
    (429, "rate_limited"),
    (500, "error"),
])
def test_scopus_health_check_classifies_permission_status(status, expected):
    provider = ScopusProvider(api_key="real-key")
    provider._client = _fake_client(responses=[_resp(status)])
    out = asyncio.run(provider.health_check())
    assert out["status"] == expected
    assert out["http_status"] == status
    assert out["latency_ms"] is not None
    if expected != "ok":
        assert str(status) in out["detail"]  # 修复指引要带 HTTP 状态码
    provider._client.get.assert_awaited_once()


def test_scopus_health_check_unconfigured_without_key():
    provider = ScopusProvider(api_key="")
    provider._client = _fake_client(responses=[_resp(200)])
    out = asyncio.run(provider.health_check())
    assert out["status"] == "unconfigured"
    provider._client.get.assert_not_awaited()  # 无 Key 不发请求，也不白付 401


def test_scopus_health_check_network_error():
    provider = ScopusProvider(api_key="real-key")
    provider._client = _fake_client(side_effect=httpx.ConnectError("no route"))
    out = asyncio.run(provider.health_check())
    assert out["status"] == "network_error"
    assert "ConnectError" in out["detail"]


def test_openalex_health_check_ok_and_blocked():
    ok = OpenAlexProvider(email="a@b.c")
    ok._client = _fake_client(responses=[_resp(200)])
    assert asyncio.run(ok.health_check())["status"] == "ok"
    blocked = OpenAlexProvider()
    blocked._client = _fake_client(responses=[_resp(403)])
    out = asyncio.run(blocked.health_check())
    assert out["status"] == "auth_error"


# ── 2) 判死口径与源组合 ──────────────────────────────────────────────────────

def test_dead_source_names_spares_rate_limited():
    healths = [
        {"name": "scopus", "status": "auth_error"},
        {"name": "openalex", "status": "ok"},
        {"name": "openalex2", "status": "rate_limited"},
        {"name": "s2", "status": "network_error"},
        {"name": "local", "status": "unconfigured"},
    ]
    assert dead_source_names(healths) == {"scopus", "s2", "local"}
    assert any_source_ok(healths) is True
    assert any_source_ok(healths[:1] + healths[3:]) is False


def test_preflight_local_mode_is_always_ok():
    healths = asyncio.run(preflight_research_sources({"provider": "local"}, use_cache=False))
    assert [(h["name"], h["role"], h["status"]) for h in healths] == [("local", "primary", "ok")]


def test_preflight_auto_with_key_probes_scopus_primary_and_openalex_fallback(monkeypatch):
    calls = []

    async def scopus_health(self, **kwargs):
        calls.append("scopus")
        return {"name": "scopus", "status": "auth_error", "detail": "HTTP 401：Key 无效",
                "latency_ms": 120, "http_status": 401}

    async def oa_health(self, **kwargs):
        calls.append("openalex")
        return {"name": "openalex", "status": "ok", "detail": "检索服务正常",
                "latency_ms": 90, "http_status": 200}

    monkeypatch.setattr(ScopusProvider, "health_check", scopus_health)
    monkeypatch.setattr(OpenAlexProvider, "health_check", oa_health)
    healths = asyncio.run(preflight_research_sources(
        {"provider": "auto", "elsevier_api_key": "k"}, use_cache=False))
    assert set(calls) == {"scopus", "openalex"}
    by_name = {h["name"]: h for h in healths}
    assert by_name["scopus"]["role"] == "primary"
    assert by_name["openalex"]["role"] == "fallback"
    assert dead_source_names(healths) == {"scopus"}


def test_preflight_auto_without_key_skips_scopus(monkeypatch):
    monkeypatch.setattr("backend.services.deep_research.providers._elsevier_key", lambda cfg: "")
    async def should_not_run(self, **kwargs):
        raise AssertionError("无 Key 的 auto 模式不应探测 Scopus")

    monkeypatch.setattr(ScopusProvider, "health_check", should_not_run)
    async def oa_health(self, **kwargs):
        return {"name": "openalex", "status": "ok", "detail": "", "latency_ms": 1, "http_status": 200}
    monkeypatch.setattr(OpenAlexProvider, "health_check", oa_health)
    healths = asyncio.run(preflight_research_sources({"provider": "auto"}, use_cache=False))
    assert [h["name"] for h in healths] == ["openalex"]
    assert healths[0]["role"] == "primary"


def test_preflight_caches_within_ttl(monkeypatch):
    count = {"n": 0}

    async def oa_health(self, **kwargs):
        count["n"] += 1
        return {"name": "openalex", "status": "ok", "detail": "", "latency_ms": 1, "http_status": 200}

    monkeypatch.setattr(OpenAlexProvider, "health_check", oa_health)
    cfg = {"provider": "openalex", "email": f"cache-test-{time.monotonic()}@x.c"}
    first = asyncio.run(preflight_research_sources(cfg, use_cache=True))
    second = asyncio.run(preflight_research_sources(cfg, use_cache=True))
    assert first == second
    assert count["n"] == 1  # TTL 内复用缓存，不重复烧探测请求


# ── 3) recall 预熔断 ─────────────────────────────────────────────────────────

class _RecallStub:
    def __init__(self, name: str):
        self.name = name
        self.calls = 0

    async def search_works(self, query, limit=20, **kwargs):
        self.calls += 1
        return []

    async def aclose(self):
        pass


def test_recall_preflight_dead_primary_is_never_called(monkeypatch):
    primary, secondary = _RecallStub("scopus"), _RecallStub("openalex")
    provider = FailoverProvider(primary, secondary)
    monkeypatch.setattr(
        "backend.services.deep_research.providers.get_provider", lambda cfg: provider,
    )
    _, used = asyncio.run(recall_papers(
        ["retrieval augmented generation"], per_query=5, cap=10, now_year=2026,
        preflight_dead={"scopus"},
    ))
    assert primary.calls == 0  # 预检判死的主源被预熔断，不再白付 401
    assert secondary.calls > 0
    assert used == "openalex"


def test_recall_alive_primary_unaffected(monkeypatch):
    primary, secondary = _RecallStub("scopus"), _RecallStub("openalex")
    provider = FailoverProvider(primary, secondary)
    monkeypatch.setattr(
        "backend.services.deep_research.providers.get_provider", lambda cfg: provider,
    )
    asyncio.run(recall_papers(["query"], per_query=5, cap=10, now_year=2026, preflight_dead=set()))
    assert primary.calls > 0
    assert secondary.calls == 0


# ── 4) run_exploration 快速失败 ──────────────────────────────────────────────

@pytest.fixture()
def exploration_env(tmp_path, monkeypatch):
    from sqlmodel import SQLModel, create_engine

    engine = create_engine(f"sqlite:///{tmp_path}/preflight.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(explorer, "get_engine", lambda: engine)
    monkeypatch.setattr(
        "backend.services.ai_config.get_analysis_config", lambda: {"api_key": "", "base_url": "", "model": ""},
    )
    events: list[tuple[str, dict]] = []

    class _Bus:
        async def publish(self, evt):
            events.append((evt.event, evt.data))

        async def close(self):
            pass

    monkeypatch.setattr(explorer, "get_exploration_bus", lambda rid: _Bus())
    return engine, events


def test_run_exploration_fails_fast_when_all_sources_dead(exploration_env, monkeypatch):
    from sqlmodel import Session
    from backend.models.exploration import ExplorationRun

    async def all_dead(*args, **kwargs):
        return [
            {"name": "scopus", "role": "primary", "status": "auth_error",
             "detail": "HTTP 401：Key 无效", "latency_ms": 100, "http_status": 401},
            {"name": "openalex", "role": "fallback", "status": "network_error",
             "detail": "连接失败", "latency_ms": None, "http_status": None},
        ]

    monkeypatch.setattr(
        "backend.services.deep_research.preflight.preflight_research_sources", all_dead,
    )

    def decompose_must_not_run(*args, **kwargs):
        raise AssertionError("数据源全挂时不应继续拆解检索式")

    monkeypatch.setattr(explorer, "decompose_question", decompose_must_not_run)

    engine, events = exploration_env
    with Session(engine) as s:
        run = ExplorationRun(question="用 AI 识别肿瘤有没有人做", depth="standard",
                             options={"search_online": True})
        s.add(run)
        s.commit()
        s.refresh(run)
        run_id = run.id

    asyncio.run(run_exploration(run_id))

    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "failed"
        assert "数据源预检未通过" in (run.error_message or "")
        assert "401" in (run.error_message or "")
    names = [e for e, _ in events]
    assert names[0] == "stage_start"  # preflight 阶段先行
    assert any(e == "stage_done" for e in names)
    assert any(e == "error" for e in names)
    # 失败事件携带可读的修复指引
    error_data = next(d for e, d in events if e == "error")
    assert "Scopus" in error_data["message"] or "scopus" in error_data["message"]


def test_run_exploration_offline_mode_skips_preflight(exploration_env, monkeypatch):
    """search_online=False 的纯工作区研究不做联网预检（无源可探）。"""
    from sqlmodel import Session
    from backend.models.exploration import ExplorationRun

    async def must_not_probe(*args, **kwargs):
        raise AssertionError("离线研究不应探测数据源")

    monkeypatch.setattr(
        "backend.services.deep_research.preflight.preflight_research_sources", must_not_probe,
    )

    engine, events = exploration_env
    with Session(engine) as s:
        run = ExplorationRun(question="纯工作区研究", depth="standard", options={"search_online": False})
        s.add(run)
        s.commit()
        s.refresh(run)
        run_id = run.id

    asyncio.run(run_exploration(run_id))
    names = [e for e, _ in events]
    assert "stage_start" in names  # 直接进入 decompose，不经过 preflight
    stage_starts = [d.get("stage") for e, d in events if e == "stage_start"]
    assert "preflight" not in stage_starts


# ── 5) HTTP 契约 ─────────────────────────────────────────────────────────────

def test_preflight_endpoint_returns_structured_health(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import app

    async def fake_sources(*args, **kwargs):
        return [
            {"name": "scopus", "role": "primary", "status": "ok", "detail": "检索权限正常",
             "latency_ms": 230, "http_status": 200},
            {"name": "openalex", "role": "fallback", "status": "ok", "detail": "检索服务正常",
             "latency_ms": 95, "http_status": 200},
        ]

    monkeypatch.setattr(
        "backend.services.deep_research.preflight.preflight_research_sources", fake_sources,
    )
    client = TestClient(app)
    resp = client.get("/api/exploration/preflight")
    assert resp.status_code == 200
    body = resp.json()
    assert body["checked_at"]
    assert [s["name"] for s in body["sources"]] == ["scopus", "openalex"]
    assert body["sources"][0]["status"] == "ok"
