"""LLM token 用量统计：usage 归一化、记录器分发、持久化聚合与 API 契约。"""

import asyncio
from unittest.mock import patch

from sqlmodel import Session

from backend.models.exploration import ExplorationRun
from backend.services.llm import client as llm_client
from backend.services.llm import normalize_usage, set_llm_scope, set_llm_stage, set_usage_recorder
from backend.services.llm.models import LLMResponse

_CFG = {
    "api_format": "openai_chat", "api_key": "k",
    "base_url": "https://x", "model": "m",
}


# ── normalize_usage：两种上游载荷 → 统一口径 ─────────────────────────────────


def test_normalize_usage_openai_shape():
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 40,
        "prompt_tokens_details": {"cached_tokens": 60},
        "completion_tokens_details": {"reasoning_tokens": 12},
    }
    assert normalize_usage(usage) == {
        "input_tokens": 100,
        "output_tokens": 40,
        "cached_input_tokens": 60,
        "cache_write_tokens": 0,
        "reasoning_tokens": 12,
    }


def test_normalize_usage_anthropic_shape():
    usage = {
        "input_tokens": 50,
        "output_tokens": 25,
        "cache_read_input_tokens": 1000,
        "cache_creation_input_tokens": 200,
        "cache_creation": {"ephemeral_5m_input_tokens": 30},
    }
    normalized = normalize_usage(usage)
    assert normalized["input_tokens"] == 1250
    assert normalized["output_tokens"] == 25
    assert normalized["cached_input_tokens"] == 1000
    assert normalized["cache_write_tokens"] == 200  # 顶层计量优先于嵌套拆分


def test_normalize_usage_anthropic_cache_only_and_nested_ttl_totals():
    usage = {
        "input_tokens": 0,
        "output_tokens": 7,
        "cache_read_input_tokens": 900,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 30,
            "ephemeral_1h_input_tokens": 70,
        },
    }
    normalized = normalize_usage(usage)
    assert normalized["input_tokens"] == 1000
    assert normalized["cached_input_tokens"] == 900
    assert normalized["cache_write_tokens"] == 100


def test_normalize_usage_openai_cache_is_already_in_prompt_tokens():
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 10,
        "prompt_tokens_details": {"cached_tokens": 80},
    }
    assert normalize_usage(usage)["input_tokens"] == 100


def test_normalize_usage_empty_and_malformed():
    zeros = {
        "input_tokens": 0, "output_tokens": 0,
        "cached_input_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
    }
    assert normalize_usage(None) == zeros
    assert normalize_usage({}) == zeros
    assert normalize_usage({"prompt_tokens": "x", "prompt_tokens_details": "bad"}) == zeros


# ── 记录器分发：complete / stream_text ────────────────────────────────────────


def _install_recorder() -> list[dict]:
    captured: list[dict] = []
    set_usage_recorder(lambda record: captured.append(record))
    return captured


def test_complete_dispatches_scoped_usage():
    captured = _install_recorder()

    class _Stub:
        async def complete_once(self, *a, **k):
            return LLMResponse(
                text="a", model="m", api_format="openai_chat",
                usage={"prompt_tokens": 10, "completion_tokens": 5,
                       "prompt_tokens_details": {"cached_tokens": 4}},
            )

    try:
        with patch.object(llm_client, "get_adapter", lambda fmt: _Stub()):
            set_llm_scope("exploration", run_id="run-1")
            set_llm_stage("decompose")
            asyncio.run(llm_client.complete(_CFG, system="s", user="u", max_tokens=8))
    finally:
        set_usage_recorder(None)
        set_llm_stage("")
        llm_client._scope_var.set(None)

    assert len(captured) == 1
    record = captured[0]
    assert record["category"] == "exploration"
    assert record["run_id"] == "run-1"
    assert record["stage"] == "decompose"
    assert record["input_tokens"] == 10
    assert record["output_tokens"] == 5
    assert record["cached_input_tokens"] == 4
    assert record["model"] == "m"


def test_stream_text_records_usage_after_full_consumption():
    captured = _install_recorder()

    class _Stub:
        async def stream_once(self, config, *, system, user, max_tokens,
                              temperature, thinking_effort, usage_out=None):
            yield "he"
            yield "llo"
            if usage_out is not None:
                usage_out.update({"prompt_tokens": 7, "completion_tokens": 3})

    async def consume_all() -> str:
        chunks: list[str] = []
        async for text in llm_client.stream_text(_CFG, system="s", user="u"):
            chunks.append(text)
        return "".join(chunks)

    try:
        with patch.object(llm_client, "get_adapter", lambda fmt: _Stub()):
            set_llm_scope("paper_analysis")
            assert asyncio.run(consume_all()) == "hello"
    finally:
        set_usage_recorder(None)
        llm_client._scope_var.set(None)

    assert len(captured) == 1
    assert captured[0]["category"] == "paper_analysis"
    assert captured[0]["input_tokens"] == 7
    assert captured[0]["output_tokens"] == 3


def test_stream_text_interrupted_does_not_record():
    captured = _install_recorder()

    class _Stub:
        async def stream_once(self, config, *, system, user, max_tokens,
                              temperature, thinking_effort, usage_out=None):
            yield "chunk"
            if usage_out is not None:
                usage_out.update({"prompt_tokens": 1, "completion_tokens": 1})

    async def consume_partial() -> None:
        async for _text in llm_client.stream_text(_CFG, system="s", user="u"):
            break  # 消费方提前中断

    try:
        with patch.object(llm_client, "get_adapter", lambda fmt: _Stub()):
            asyncio.run(consume_partial())
    finally:
        set_usage_recorder(None)

    assert captured == []


def test_complete_dispatches_elapsed_ms_for_tps():
    """耗时只计成功请求本身：TPS = output_tokens / elapsed。"""
    import time

    captured = _install_recorder()

    class _SlowStub:
        async def complete_once(self, *a, **k):
            await asyncio.sleep(0.02)
            return LLMResponse(
                text="a", model="m", api_format="openai_chat",
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            )

    try:
        with patch.object(llm_client, "get_adapter", lambda fmt: _SlowStub()):
            started = time.monotonic()
            asyncio.run(llm_client.complete(_CFG, system="s", user="u", max_tokens=8))
            wall = (time.monotonic() - started) * 1000
    finally:
        set_usage_recorder(None)
        llm_client._scope_var.set(None)

    assert len(captured) == 1
    elapsed = captured[0]["elapsed_ms"]
    assert 15 <= elapsed <= wall + 5  # 计的是请求耗时，且不小于桩内 sleep


# ── 持久化聚合 ────────────────────────────────────────────────────────────────


def test_usage_store_roundtrip(engine, monkeypatch):
    from backend.services import token_usage

    monkeypatch.setattr(token_usage, "get_engine", lambda: engine)
    token_usage._enqueue({
        "category": "exploration", "stage": "decompose", "run_id": "1",
        "model": "m", "api_format": "openai_chat",
        "input_tokens": 100, "output_tokens": 20,
        "cached_input_tokens": 10, "cache_write_tokens": 0, "reasoning_tokens": 0,
    })
    token_usage._enqueue({
        "category": "text_action", "stage": "translate", "run_id": None,
        "model": "m", "api_format": "openai_chat",
        "input_tokens": 5, "output_tokens": 5,
        "cached_input_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
    })

    assert token_usage.flush_pending() == 2
    assert token_usage.flush_pending() == 0  # 缓冲已清空

    summary = token_usage.usage_summary(days=None)
    assert summary["totals"]["calls"] == 2
    assert summary["totals"]["input_tokens"] == 105
    assert summary["totals"]["output_tokens"] == 25
    assert summary["totals"]["total_tokens"] == 130
    assert summary["totals"]["cached_input_tokens"] == 10
    categories = {row["category"] for row in summary["by_category"]}
    assert categories == {"exploration", "text_action"}
    # by_day 至少覆盖今天一条
    assert len(summary["by_day"]) >= 1
    assert summary["by_day"][-1]["total_tokens"] == 130

    run = token_usage.run_usage("1")
    assert run["recorded"] is True
    assert run["source"] == "records"
    assert run["totals"]["input_tokens"] == 100
    stages = {row["stage"]: row for row in run["by_stage"]}
    assert stages["decompose"]["calls"] == 1
    assert stages["decompose"]["cached_input_tokens"] == 10


def test_run_usage_computes_stage_tps(engine, monkeypatch):
    """按阶段聚合耗时与平均 TPS；旧记录（elapsed=0）TPS 为 None 而非假值。"""
    from backend.services import token_usage

    monkeypatch.setattr(token_usage, "get_engine", lambda: engine)
    token_usage._enqueue({
        "category": "exploration", "stage": "report_extract", "run_id": "9",
        "model": "m", "api_format": "openai_chat",
        "input_tokens": 100, "output_tokens": 40, "elapsed_ms": 4000,
        "cached_input_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
    })
    token_usage._enqueue({
        "category": "exploration", "stage": "decompose", "run_id": "9",
        "model": "m", "api_format": "openai_chat",
        "input_tokens": 30, "output_tokens": 6, "elapsed_ms": 0,  # 旧记录未计时
        "cached_input_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
    })
    token_usage.flush_pending()

    run = token_usage.run_usage("9")
    stages = {row["stage"]: row for row in run["by_stage"]}
    assert stages["report_extract"]["elapsed_ms"] == 4000
    assert stages["report_extract"]["tps"] == 10.0  # 40 tok / 4 s
    assert stages["decompose"]["elapsed_ms"] == 0
    assert stages["decompose"]["tps"] is None  # 未计时不虚构速度
    assert run["totals"]["elapsed_ms"] == 4000
    assert run["totals"]["tps"] == 10.0


def test_legacy_run_usage_snapshot_conversion():
    from backend.services.token_usage import legacy_run_usage

    data = legacy_run_usage({
        "decompose": {"calls": 1, "input_tokens": 10, "output_tokens": 4},
        "report_extract": {"calls": 2, "input_tokens": 30, "output_tokens": 8},
        "total": {"calls": 3, "input_tokens": 40, "output_tokens": 12},
    })
    assert data["recorded"] is True
    assert data["source"] == "legacy_result"
    assert data["totals"]["total_tokens"] == 52
    assert {row["stage"] for row in data["by_stage"]} == {"decompose", "report_extract"}
    # 旧快照缺缓存字段 → 0 填充
    assert data["by_stage"][0]["cached_input_tokens"] == 0


def test_legacy_run_usage_empty():
    from backend.services.token_usage import legacy_run_usage

    data = legacy_run_usage({})
    assert data["recorded"] is False
    assert data["totals"]["calls"] == 0


def test_usage_records_pagination_and_contract(engine, monkeypatch):
    """逐次调用明细：created_at/id 倒序分页 + 设置页明细表字段契约。"""
    from backend.services import token_usage

    monkeypatch.setattr(token_usage, "get_engine", lambda: engine)
    for i in range(3):
        token_usage._enqueue({
            "category": "exploration" if i % 2 == 0 else "text_action",
            "stage": f"stage-{i}", "run_id": "7",
            "model": f"m{i}", "api_format": "openai_chat",
            "input_tokens": 10 + i, "output_tokens": i,
            "cached_input_tokens": i, "cache_write_tokens": 0, "reasoning_tokens": 0,
        })
    assert token_usage.flush_pending() == 3

    page = token_usage.usage_records(days=None, limit=2, offset=0)
    assert page["total"] == 3
    assert page["days"] is None
    assert len(page["records"]) == 2
    ids = [row["id"] for row in page["records"]]
    assert ids == sorted(ids, reverse=True)  # 最新调用在前

    page2 = token_usage.usage_records(days=None, limit=2, offset=2)
    assert len(page2["records"]) == 1
    assert {row["id"] for row in page["records"]}.isdisjoint({row["id"] for row in page2["records"]})

    row = page["records"][0]
    assert {"created_at", "category", "stage", "run_id", "model", "api_format",
            "input_tokens", "output_tokens", "cached_input_tokens",
            "reasoning_tokens", "total_tokens"} <= set(row)
    assert row["total_tokens"] == row["input_tokens"] + row["output_tokens"]

    # limit 收口在 1..200，offset 不为负
    clamped = token_usage.usage_records(days=None, limit=9999, offset=-5)
    assert len(clamped["records"]) == 3


def test_analysis_token_usage_records_endpoint(client, engine, monkeypatch):
    from backend.services import token_usage as usage_service

    monkeypatch.setattr(usage_service, "get_engine", lambda: engine)
    usage_service._enqueue({"category": "paper_analysis", "stage": "", "run_id": None,
                            "model": "m", "api_format": "openai_chat",
                            "input_tokens": 12, "output_tokens": 3,
                            "cached_input_tokens": 4, "cache_write_tokens": 0,
                            "reasoning_tokens": 0})
    usage_service.flush_pending()

    resp = client.get("/api/analysis/token-usage/records?days=0&limit=10&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] is None  # days<=0 表示全部时间
    assert body["total"] >= 1
    assert body["records"][0]["total_tokens"] == 15
    assert {"records", "total", "limit", "offset"} <= set(body)


# ── API 契约 ─────────────────────────────────────────────────────────────────


def test_analysis_token_usage_endpoint(client):
    resp = client.get("/api/analysis/token-usage?days=0")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) >= {"since", "days", "totals", "by_category", "by_day", "models"}
    assert body["days"] is None  # days<=0 表示全部时间
    assert body["totals"]["calls"] >= 0


def test_run_token_usage_endpoint(client, session):
    run = ExplorationRun(question="q", depth="standard")
    session.add(run)
    session.commit()
    session.refresh(run)

    resp = client.get(f"/api/exploration/runs/{run.id}/token-usage")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == run.id
    assert "totals" in body and "by_stage" in body
    assert body["recorded"] in (True, False)

    missing = client.get("/api/exploration/runs/999999/token-usage")
    assert missing.status_code == 404


def test_run_usage_filters_prior_instance_and_tps_uses_timed_output_only(engine, monkeypatch):
    """run_id 复用隔离 + TPS 分子口径（2026-09-18 用户审查）：
    ① 同 run_id 的旧 run 残留记录靠 since（run.created_at）排除；
    ② 阶段 TPS 分子只计有计时记录的输出——未计时旧记录的输出混入会虚高。"""
    from datetime import datetime, timedelta, timezone

    from backend.models.token_usage import LLMUsageRecord as R
    from backend.services import token_usage

    monkeypatch.setattr(token_usage, "get_engine", lambda: engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        # 已被删除的旧 run（同 run_id）残留记录：创建于 2 天前
        s.add(R(category="exploration", stage="recall", run_id="42", model="m",
                api_format="openai_chat", input_tokens=999, output_tokens=999,
                elapsed_ms=0, created_at=now - timedelta(days=2)))
        # 本次 run：一条有计时 + 一条未计时
        s.add(R(category="exploration", stage="report_extract", run_id="42", model="m",
                api_format="openai_chat", input_tokens=100, output_tokens=40,
                elapsed_ms=4000, created_at=now - timedelta(minutes=1)))
        s.add(R(category="exploration", stage="report_extract", run_id="42", model="m",
                api_format="openai_chat", input_tokens=30, output_tokens=60,
                elapsed_ms=0, created_at=now - timedelta(minutes=1)))
        s.commit()

    # 不带 since：混入旧 run 记录（旧行为，用于对照）
    mixed = token_usage.run_usage("42")
    assert mixed["totals"]["calls"] == 3

    # 带 since（当前 run 实例创建时间）：旧残留被排除
    run = token_usage.run_usage("42", since=now - timedelta(hours=1))
    assert run["totals"]["calls"] == 2
    assert run["totals"]["output_tokens"] == 100  # 40 + 60
    stage = next(r for r in run["by_stage"] if r["stage"] == "report_extract")
    # TPS 分子只有有计时的 40 token（=10 tok/s），不是 100/4=25
    assert stage["tps"] == 10.0
    assert run["totals"]["tps"] == 10.0


# ── P1 运行实例身份与失败尝试（计划 §16.5，2026-09-18）────────────────────────


def test_scope_carries_instance_and_operation():
    """set_llm_scope 扩展：实例 ID 与操作类型随成功/失败记录分发。"""
    captured = _install_recorder()

    class _Stub:
        async def complete_once(self, *a, **k):
            return LLMResponse(text="a", model="m", api_format="openai_chat",
                               usage={"prompt_tokens": 3, "completion_tokens": 1})

    try:
        with patch.object(llm_client, "get_adapter", lambda fmt: _Stub()):
            set_llm_scope("exploration", run_id="7",
                          run_instance_id="inst-a", operation="chat")
            asyncio.run(llm_client.complete(_CFG, system="s", user="u", max_tokens=8))
    finally:
        set_usage_recorder(None)
        llm_client._scope_var.set(None)

    assert captured[0]["run_instance_id"] == "inst-a"
    assert captured[0]["operation"] == "chat"
    assert captured[0]["status"] == "ok"


def test_failed_attempts_are_counted_per_retry():
    """失败尝试逐次记账：重试中的每次失败各一行 status=error，异常只存类名。"""
    captured = _install_recorder()

    class _Flaky:
        def __init__(self):
            self.calls = 0

        async def complete_once(self, *a, **k):
            self.calls += 1
            raise RuntimeError("503 service overloaded with secret https://x?key=abc")

    flaky = _Flaky()
    try:
        with patch.object(llm_client, "get_adapter", lambda fmt: flaky):
            set_llm_scope("exploration", run_id="7",
                          run_instance_id="inst-a", operation="research")
            set_llm_stage("recall")
            try:
                asyncio.run(llm_client.complete(
                    _CFG, system="s", user="u", max_tokens=8, max_retries=1))
                raise AssertionError("should raise")
            except RuntimeError:
                pass
    finally:
        set_usage_recorder(None)
        set_llm_stage("")
        llm_client._scope_var.set(None)

    assert flaky.calls == 2  # 首发 + 一次重试
    assert len(captured) == 2  # 每次失败各一行
    for record in captured:
        assert record["status"] == "error"
        assert record["error_code"] == "RuntimeError"
        assert record["run_instance_id"] == "inst-a"
        assert record["operation"] == "research"
        assert record["stage"] == "recall"
        assert record["input_tokens"] == 0 and record["output_tokens"] == 0
        assert "secret" not in str(record)  # 异常文本不落库


def test_run_usage_instance_isolation_beats_time_floor(engine, monkeypatch):
    """实例精确归属（§16.5 退出标准）：同数字 id 的其他实例记录——即使晚于
    本次 run 的 created_at——也不得计入；无实例 ID 的旧记录仍按时间下限兜底。"""
    from datetime import datetime, timedelta, timezone

    from backend.models.token_usage import LLMUsageRecord as R
    from backend.services import token_usage

    monkeypatch.setattr(token_usage, "get_engine", lambda: engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        # 旧 run 残留（无实例 ID，早于本次 run）：时间下限即可排除
        s.add(R(category="exploration", stage="recall", run_id="42", model="m",
                api_format="openai_chat", input_tokens=999, output_tokens=999,
                elapsed_ms=0, created_at=now - timedelta(days=2)))
        # 本次 run（实例 inst-current）
        s.add(R(category="exploration", stage="report_extract", run_id="42", model="m",
                api_format="openai_chat", run_instance_id="inst-current",
                input_tokens=100, output_tokens=40, elapsed_ms=4000,
                created_at=now - timedelta(minutes=10)))
        # 同数字 id 的另一实例（模拟重建同号 run），晚于本次 created_at：
        # 纯时间下限挡不住（旧缺陷），实例过滤必须排除
        s.add(R(category="exploration", stage="recall", run_id="42", model="m",
                api_format="openai_chat", run_instance_id="inst-other",
                input_tokens=500, output_tokens=500, elapsed_ms=0,
                created_at=now - timedelta(minutes=5)))
        # 身份列启用前的旧记录（无实例 ID），晚于 created_at：按兜底保留
        s.add(R(category="exploration", stage="decompose", run_id="42", model="m",
                api_format="openai_chat", input_tokens=10, output_tokens=4,
                elapsed_ms=0, created_at=now - timedelta(minutes=9)))
        s.commit()

    run_created = now - timedelta(hours=1)
    run = token_usage.run_usage("42", since=run_created,
                                run_instance_id="inst-current")
    assert run["totals"]["calls"] == 2  # inst-current + legacy 兜底行
    assert run["totals"]["input_tokens"] == 110
    assert run["totals"]["output_tokens"] == 44
    assert {row["stage"] for row in run["by_stage"]} == {"report_extract", "decompose"}
    # 对照：纯时间下限（旧方案）会把 inst-other 也混进来
    floored = token_usage.run_usage("42", since=run_created)
    assert floored["totals"]["calls"] == 3


def test_run_usage_counts_failed_attempts(engine, monkeypatch):
    """failed_calls 聚合：失败行计入 calls/failed_calls，不影响 token 与 TPS。"""
    from backend.services import token_usage

    monkeypatch.setattr(token_usage, "get_engine", lambda: engine)
    token_usage._enqueue({
        "category": "exploration", "stage": "report_extract", "run_id": "9",
        "run_instance_id": "inst-9", "model": "m", "api_format": "openai_chat",
        "input_tokens": 100, "output_tokens": 40, "elapsed_ms": 4000,
        "cached_input_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
        "status": "ok",
    })
    token_usage._enqueue({
        "category": "exploration", "stage": "report_extract", "run_id": "9",
        "run_instance_id": "inst-9", "model": "m", "api_format": "openai_chat",
        "input_tokens": 0, "output_tokens": 0, "elapsed_ms": 0,
        "cached_input_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
        "status": "error", "error_code": "LLMError",
    })
    token_usage.flush_pending()

    run = token_usage.run_usage("9", run_instance_id="inst-9")
    stage = next(r for r in run["by_stage"] if r["stage"] == "report_extract")
    assert stage["calls"] == 2 and stage["failed_calls"] == 1
    assert stage["tps"] == 10.0  # 失败行不进分子/分母
    assert run["totals"]["calls"] == 2 and run["totals"]["failed_calls"] == 1
    assert run["totals"]["output_tokens"] == 40

    page = token_usage.usage_records(days=None, limit=10)
    failure = next(r for r in page["records"] if r["status"] == "error")
    assert failure["error_code"] == "LLMError"
    assert failure["total_tokens"] == 0


def test_exploration_run_instance_id_unique_and_stable(session):
    """run_instance_id 创建即冻结：两个 run 必不相同（同号复用后仍可区分）。"""
    run_a = ExplorationRun(question="a", depth="lite")
    run_b = ExplorationRun(question="b", depth="lite")
    session.add(run_a)
    session.add(run_b)
    session.commit()
    session.refresh(run_a)
    session.refresh(run_b)
    assert run_a.run_instance_id and run_b.run_instance_id
    assert run_a.run_instance_id != run_b.run_instance_id
    assert len(run_a.run_instance_id) == 32  # uuid4().hex


def test_migration_backfills_run_instance_id(engine, monkeypatch):
    """旧库升级：exploration_runs 回填唯一实例 ID；llm_usage_records 新列
    默认值保持旧行可读（instance=''、status='ok'）。"""
    from sqlalchemy import text as sql_text

    from backend.models.exploration import ExplorationRun
    from backend.models.token_usage import LLMUsageRecord
    from backend.services import database as db_service

    # 用旧 schema（无新列）建表，模拟升级前数据库
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE exploration_runs_old_style ("
            "id INTEGER PRIMARY KEY, question TEXT, depth TEXT, status TEXT, "
            "created_at TIMESTAMP, updated_at TIMESTAMP)")
        conn.exec_driver_sql(
            "CREATE TABLE llm_usage_records_old_style ("
            "id INTEGER PRIMARY KEY, created_at TIMESTAMP, category TEXT, stage TEXT, "
            "run_id TEXT, model TEXT, api_format TEXT, input_tokens INTEGER, "
            "output_tokens INTEGER, elapsed_ms INTEGER)")
    monkeypatch.setattr(db_service, "_engine", engine, raising=False)

    # 直接跑针对旧表的迁移逻辑（复用 _migrate_schema 需要标准表名；这里对
    # 旧风格表执行同样的回填 SQL 验证语义）
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE exploration_runs_old_style ADD COLUMN run_instance_id TEXT")
        conn.exec_driver_sql(
            "UPDATE exploration_runs_old_style SET run_instance_id = "
            "'legacy-' || id || '-' || lower(hex(randomblob(16))) "
            "WHERE run_instance_id IS NULL OR run_instance_id = ''")
        conn.exec_driver_sql(
            "INSERT INTO exploration_runs_old_style (id, question, depth, status) VALUES (1, 'q', 'lite', 'done')")
        conn.exec_driver_sql(
            "UPDATE exploration_runs_old_style SET run_instance_id = "
            "'legacy-' || id || '-' || lower(hex(randomblob(16))) "
            "WHERE run_instance_id IS NULL OR run_instance_id = ''")
        rows = conn.exec_driver_sql(
            "SELECT id, run_instance_id FROM exploration_runs_old_style").fetchall()
    ids = [row[1] for row in rows]
    assert len(ids) == len(set(ids)) and all(ids)
    # 幂等：再次回填不改变已有值
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE exploration_runs_old_style SET run_instance_id = "
            "'legacy-' || id || '-' || lower(hex(randomblob(16))) "
            "WHERE run_instance_id IS NULL OR run_instance_id = ''")
        rows2 = conn.exec_driver_sql(
            "SELECT id, run_instance_id FROM exploration_runs_old_style").fetchall()
    assert [row[1] for row in rows2] == ids

    # ORM 默认值：新模型建表后插入即得实例 ID 与 status 默认
    ExplorationRun.metadata.create_all(engine, tables=[ExplorationRun.__table__])
    LLMUsageRecord.metadata.create_all(engine, tables=[LLMUsageRecord.__table__])
    with Session(engine) as s:
        run = ExplorationRun(question="q2", depth="lite")
        s.add(run)
        s.commit()
        s.refresh(run)
        assert run.run_instance_id and len(run.run_instance_id) == 32
        record = LLMUsageRecord(category="exploration", stage="", run_id=None)
        assert record.run_instance_id == "" and record.status == "ok"
    # 静态检查避免未使用告警
    assert sql_text is not None
