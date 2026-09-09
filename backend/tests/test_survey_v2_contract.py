"""academic_survey_v2 契约测试（S0：schema 冻结、迁移/回滚、兼容投影、接口边界）

覆盖计划 §10.1 中 S0 负责的行：兼容（老 run 可读、旧入口不变）、恢复（v2 不被启动
清理误置 failed）、前端/API（GET/POST 契约与 CORS）。全部使用合成数据与临时库，
不接触真实研究数据，也不发起任何网络请求。
"""

from datetime import datetime, timezone

import pytest
from sqlmodel import Session, create_engine, select

from backend.models.exploration import ExplorationRun
from backend.models.research_core import (
    WORKFLOW_ACADEMIC_SURVEY_V2,
    ResearchAsset,
    ResearchBudgetEvent,
    ResearchEvidence,
    ResearchReportRevision,
    ResearchTask,
)
from backend.services import research_event_log as event_log
from backend.services.database import _migrate_schema, init_db, reconcile_interrupted_runs


# ============================================================
# 迁移：幂等、空库、旧库升级、回滚可读
# ============================================================


def _table_columns(engine, table):
    with engine.connect() as conn:
        return {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}


V2_TABLES = (
    "research_tasks", "research_task_attempts", "research_assets", "research_evidence",
    "research_relations", "research_claims", "research_report_revisions",
    "research_budget_events", "research_events", "research_identity_decisions",
)


def test_migration_on_empty_db_creates_v2_tables(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'empty.db'}"
    init_db(db_url)
    verify = create_engine(db_url)
    with verify.connect() as conn:
        names = {row[0] for row in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(V2_TABLES).issubset(names)
    cols = _table_columns(verify, "exploration_runs")
    assert {"workflow_version", "state_version", "protocol_asset_id"}.issubset(cols)


def test_migration_upgrades_legacy_database_idempotently(tmp_path):
    """旧形状 exploration_runs（无 v2 列）升级：新增列带默认值、旧数据可读、重复迁移无害。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.connect() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE exploration_runs (
                id INTEGER PRIMARY KEY,
                question TEXT NOT NULL,
                depth TEXT DEFAULT 'standard',
                status TEXT DEFAULT 'pending',
                provider TEXT DEFAULT '',
                options JSON,
                decompose JSON,
                result JSON,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )
            """
        )
        conn.exec_driver_sql(
            "INSERT INTO exploration_runs (question, status, options) VALUES ('旧研究', 'done', '{}')"
        )
        conn.commit()
    _migrate_schema(engine)
    _migrate_schema(engine)  # 幂等

    cols = _table_columns(engine, "exploration_runs")
    assert {"workflow_version", "state_version", "protocol_asset_id"}.issubset(cols)
    with engine.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT question, status, workflow_version, state_version FROM exploration_runs WHERE id = 1"
        ).fetchone()
    assert row == ("旧研究", "done", "legacy_v1", 0)
    # 回滚可读性：v1 代码只按旧列名 SELECT，新列存在不破坏读取
    with engine.connect() as conn:
        legacy_view = conn.exec_driver_sql(
            "SELECT question, status FROM exploration_runs WHERE id = 1"
        ).fetchone()
    assert legacy_view == ("旧研究", "done")


def test_rollback_keeps_v2_rows_readable_by_v1_reader(tmp_path):
    """附加列不破坏 v1 读取：v1 代码只按旧列名 SELECT，v2 行仍可读。

    注意边界（F5）：这只证明列级兼容；v2 报告（资产+revision）的只读访问
    依赖 S0 契约层代码，见 test_rollback_form_keeps_v2_readonly_and_blocks_writes。
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'rollback.db'}")
    init_db(f"sqlite:///{tmp_path / 'rollback.db'}")
    with Session(engine) as session:
        session.add(ExplorationRun(question="v1 研究", status="done"))
        session.add(ExplorationRun(question="v2 研究", status="paused",
                                   workflow_version=WORKFLOW_ACADEMIC_SURVEY_V2, state_version=3))
        session.commit()
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT question FROM exploration_runs ORDER BY id"
        ).fetchall()
    assert [r[0] for r in rows] == ["v1 研究", "v2 研究"]


# ============================================================
# 启动自愈豁免：v2 运行不被 API 重启误置 failed
# ============================================================


def test_reconcile_marks_v1_failed_but_exempts_v2(engine):
    with Session(engine) as session:
        v1 = ExplorationRun(question="v1 interrupted", status="running")
        v2 = ExplorationRun(question="v2 leased", status="running",
                            workflow_version=WORKFLOW_ACADEMIC_SURVEY_V2, state_version=2)
        session.add(v1)
        session.add(v2)
        session.commit()
        v1_id, v2_id = v1.id, v2.id

    count = reconcile_interrupted_runs(engine)
    assert count == 1
    with Session(engine) as session:
        assert session.get(ExplorationRun, v1_id).status == "failed"
        assert session.get(ExplorationRun, v2_id).status == "running"  # worker 租约负责恢复


# ============================================================
# 测试脚手架：构造合成 v2 运行
# ============================================================


@pytest.fixture
def v2_env(engine):
    """合成 v2 运行：协议资产 + 任务 + 事件 + 预算 + 证据 + revision。"""
    with Session(engine) as session:
        run = ExplorationRun(
            question="RAG 综述研究", depth="deep", status="running",
            workflow_version=WORKFLOW_ACADEMIC_SURVEY_V2, state_version=4,
        )
        session.add(run)
        session.flush()
        protocol = ResearchAsset(
            run_id=run.id, content_kind="protocol", format="json",
            inline_payload={
                "protocol_id": "proto-1", "version": 1,
                "question": "RAG 综述研究",
                "subquestions": [
                    {"subquestion_id": "sq1", "question": "主要机制", "required": True},
                    {"subquestion_id": "sq2", "question": "评价体系"},
                ],
                "budget": {"llm_tokens": 1000000, "llm_calls": 400},
                "hash": "protohash1",
            },
        )
        session.add(protocol)
        session.flush()
        run.protocol_asset_id = protocol.asset_id
        report_asset = ResearchAsset(
            run_id=run.id, content_kind="report", format="json",
            inline_payload={"markdown": "# 报告\n正文", "title": "RAG survey",
                            "tables": {}, "coverage": {}},
        )
        session.add(report_asset)
        session.flush()
        revision = ResearchReportRevision(
            run_id=run.id, report_asset_id=report_asset.asset_id,
            protocol_hash="protohash1", is_selected=True,
            quality_status="internally_checked", stop_reason="budget_exhausted",
        )
        session.add(revision)
        document = ResearchAsset(
            run_id=run.id, content_kind="document", format="pdf",
            family_key="arxiv:2305.06983", content_hash="dochash1", parse_status="parsed",
            public_available_at=datetime(2023, 5, 1, tzinfo=timezone.utc),
        )
        session.add(document)
        session.flush()
        evidence = ResearchEvidence(
            run_id=run.id, asset_id=document.asset_id, doc_content_hash="dochash1",
            spans=[{"page": 6, "table": "Table II", "quote": "Natural Questions dataset",
                    "char_start": 100, "char_end": 130}],
            relation="header+cell",
        )
        session.add(evidence)
        tasks = [
            ResearchTask(run_id=run.id, kind="search", subquestion_id="sq1", state="succeeded"),
            ResearchTask(run_id=run.id, kind="extract", subquestion_id="sq1", state="failed",
                        error_code="TIMEOUT"),
            ResearchTask(run_id=run.id, kind="extract", subquestion_id="sq2", state="pending"),
        ]
        session.add_all(tasks)
        session.add_all([
            ResearchBudgetEvent(run_id=run.id, resource="llm_tokens",
                                delta_reserved=1000, delta_used=800, delta_refunded=200),
            ResearchBudgetEvent(run_id=run.id, resource="llm_calls",
                                delta_reserved=100, delta_used=100, delta_refunded=0,
                                usage_known=False),
            ResearchBudgetEvent(run_id=run.id, resource="llm_calls",
                                delta_reserved=50, delta_used=0, delta_refunded=0),
        ])
        event_log.append_event(session, run_id=run.id, type="run_created", payload={"q": 1})
        event_log.append_event(session, run_id=run.id, type="stage_started",
                               payload={"stage": "search"})
        event_log.append_event(session, run_id=run.id, type="budget_stop",
                               payload={"reason": "llm_calls 上限"})
        session.commit()
        yield {
            "run_id": run.id,
            "task_ids": [t.task_id for t in tasks],
            "failed_task_id": tasks[1].task_id,
            "evidence_id": evidence.evidence_id,
            "revision_id": revision.revision_id,
            "document_asset_id": document.asset_id,
            "protocol_asset_id": protocol.asset_id,
        }


@pytest.fixture
def v1_env(engine):
    with Session(engine) as session:
        run = ExplorationRun(
            question="v1 历史研究", status="done",
            result={"papers": [{"key": "p1"}],
                    "survey_report": {"markdown": "# 旧报告", "coverage": {"total": 5}}},
        )
        session.add(run)
        session.commit()
        yield run.id


# ============================================================
# POST /runs：v2 门禁与输入校验契约
# ============================================================


def test_create_v2_is_gated_with_contract_error(client):
    response = client.post("/api/exploration/runs", json={
        "question": "检索增强生成综述研究",
        "depth": "deep",
        "workflow_version": "academic_survey_v2",
        "budget": {"llm_tokens": 500000},
    })
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "WORKFLOW_NOT_IMPLEMENTED"
    assert set(body) == {"detail", "code"}


def test_create_rejects_unknown_workflow_version(client):
    response = client.post("/api/exploration/runs", json={
        "question": "研究问题", "workflow_version": "v3",
    })
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_create_v1_without_workflow_version_still_works(client, monkeypatch, engine):
    """v1 默认行为不变：不传 workflow_version 的创建仍走旧链路（后台任务打桩防真实检索）。"""
    from backend.routers import exploration

    monkeypatch.setattr(exploration, "get_engine", lambda: engine)

    async def fake_run(run_id):
        return None

    monkeypatch.setattr(exploration, "run_exploration", fake_run)
    response = client.post("/api/exploration/runs", json={"question": "AI 肿瘤识别研究"})
    assert response.status_code == 200
    body = response.json()
    assert body["workflow_version"] == "legacy_v1"
    assert body["state_version"] == 0
    assert body["protocol"] is None and body["current_revision"] is None
    with Session(engine) as session:
        row = session.get(ExplorationRun, body["id"])
        assert row.workflow_version == "legacy_v1"
        assert "workflow" not in (row.options or {})


def test_v2_options_snapshot_roundtrip():
    from datetime import datetime as dt

    from backend.routers.exploration import _v2_options_snapshot
    from backend.models.schemas import ExplorationRunCreate

    req = ExplorationRunCreate(
        question="研究", workflow_version="academic_survey_v2",
        protocol_options={"subquestions_min": 5},
        budget={"llm_calls": 120},
        content_cutoff=dt(2024, 3, 27, 23, 59, 59, tzinfo=timezone.utc),
        policy_id="policy-abc",
    )
    options = _v2_options_snapshot(req, {"search_online": True})
    assert options["search_online"] is True
    assert options["workflow"]["version"] == "academic_survey_v2"
    assert options["workflow"]["content_cutoff"] == "2024-03-27T23:59:59+00:00"
    assert options["workflow"]["policy_id"] == "policy-abc"
    # v1 输入不加快照键
    plain = _v2_options_snapshot(ExplorationRunCreate(question="研究"), {"search_online": True})
    assert "workflow" not in plain


# ============================================================
# v1 兼容投影：新端点对 v1 运行的行为
# ============================================================


def test_v1_run_legacy_projections(client, v1_env):
    base = f"/api/exploration/runs/{v1_env}"
    protocol = client.get(f"{base}/protocol").json()
    assert protocol["legacy"] is True and protocol["workflow_version"] == "legacy_v1"
    assert protocol["legacy_options"] == {} and protocol["protocol"] is None

    tasks = client.get(f"{base}/tasks").json()
    assert tasks == {"run_id": v1_env, "workflow_version": "legacy_v1",
                     "legacy": True, "items": [], "next_cursor": None, "total": 0}

    budget = client.get(f"{base}/budget").json()
    assert budget["legacy"] is True and budget["resources"] == []

    coverage = client.get(f"{base}/coverage").json()
    assert coverage["legacy"] is True
    assert coverage["legacy_summary"] == {"total": 5}

    revisions = client.get(f"{base}/revisions").json()
    assert [item["revision_id"] for item in revisions["items"]] == ["legacy"]
    assert revisions["items"][0]["legacy"] is True and revisions["items"][0]["is_selected"]

    report = client.get(f"{base}/revisions/legacy/report?format=markdown")
    assert report.status_code == 200 and "# 旧报告" in report.text
    assert client.get(f"{base}/revisions/other/report").status_code == 404

    # v1 无独立证据对象
    assert client.get(f"{base}/evidence/whatever").status_code == 404


def test_v1_run_control_and_retry_unsupported(client, engine, v1_env, monkeypatch):
    base = f"/api/exploration/runs/{v1_env}"
    for action in ("pause", "resume", "cancel"):
        response = client.post(f"{base}/{action}", json={})
        assert response.status_code == 400
        assert response.json()["code"] == "UNSUPPORTED_WORKFLOW"
    response = client.post(f"{base}/retry", json={})
    assert response.status_code == 400
    assert response.json()["code"] == "UNSUPPORTED_WORKFLOW"
    # v1 重建路径不受 v2 守卫影响：无材料也进入零命中报告重建。
    with Session(engine) as session:
        bare = ExplorationRun(question="v1 无材料", status="done", result={})
        session.add(bare)
        session.commit()
        bare_id = bare.id
    started = []
    monkeypatch.setattr("backend.routers.exploration._start", lambda coro: (started.append(coro), coro.close()))
    response = client.post(f"/api/exploration/runs/{bare_id}/report")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert len(started) == 1


def test_v2_run_report_rebuild_and_delete_guarded(client, engine, v2_env):
    base = f"/api/exploration/runs/{v2_env['run_id']}"
    assert client.post(f"{base}/report").json()["code"] == "WORKFLOW_NOT_IMPLEMENTED"
    assert client.delete(base).json()["code"] == "WORKFLOW_NOT_IMPLEMENTED"


# ============================================================
# v2 端点契约
# ============================================================


def test_v2_protocol_tasks_coverage_budget_evidence(client, v2_env):
    run_id = v2_env["run_id"]
    base = f"/api/exploration/runs/{run_id}"

    protocol = client.get(f"{base}/protocol").json()
    assert protocol["legacy"] is False
    assert protocol["protocol"]["protocol_id"] == "proto-1"
    assert len(protocol["protocol"]["subquestions"]) == 2
    assert protocol["changes"][0]["version"] == 1

    tasks = client.get(f"{base}/tasks").json()
    assert tasks["total"] == 3 and len(tasks["items"]) == 3
    assert tasks["next_cursor"] is None
    failed = client.get(f"{base}/tasks?state=failed").json()
    assert failed["total"] == 1 and failed["items"][0]["error_code"] == "TIMEOUT"
    page = client.get(f"{base}/tasks?limit=2").json()
    assert len(page["items"]) == 2 and page["next_cursor"] is not None
    rest = client.get(f"{base}/tasks?limit=2&cursor={page['next_cursor']}").json()
    assert len(rest["items"]) == 1
    assert client.get(f"{base}/tasks?state=bogus").status_code == 422
    assert client.get(f"{base}/tasks?cursor=nope").status_code == 422

    coverage = client.get(f"{base}/coverage").json()
    assert coverage["legacy"] is False
    assert [s["subquestion_id"] for s in coverage["subquestions"]] == ["sq1", "sq2"]

    budget = client.get(f"{base}/budget").json()
    by_resource = {entry["resource"]: entry for entry in budget["resources"]}
    assert by_resource["llm_tokens"]["limit"] == 1000000
    assert by_resource["llm_tokens"]["used"] == 800
    assert by_resource["llm_tokens"]["reserved"] == 0  # 1000 - 800 - 200
    assert by_resource["llm_calls"]["used"] == 100
    assert by_resource["llm_calls"]["reserved"] == 50
    assert by_resource["llm_calls"]["usage_known"] is False
    assert budget["stop_reason"] == "budget_exhausted"  # revision 优先于 budget_stop 事件

    evidence = client.get(f"{base}/evidence/{v2_env['evidence_id']}").json()
    assert evidence["spans"][0]["table"] == "Table II"
    assert evidence["document"]["content_hash"] == "dochash1"
    assert evidence["document"]["public_available_at"].startswith("2023-05-01")
    assert client.get(f"{base}/evidence/missing").status_code == 404


def test_v2_revisions_and_report_export(client, v2_env):
    run_id = v2_env["run_id"]
    base = f"/api/exploration/runs/{run_id}"
    revisions = client.get(f"{base}/revisions").json()
    assert revisions["legacy"] is False
    assert revisions["items"][0]["revision_id"] == v2_env["revision_id"]
    assert revisions["items"][0]["quality_status"] == "internally_checked"
    assert revisions["items"][0]["is_selected"] is True

    report = client.get(f"{base}/revisions/{v2_env['revision_id']}/report?format=markdown")
    assert report.status_code == 200 and "# 报告" in report.text
    as_json = client.get(f"{base}/revisions/{v2_env['revision_id']}/report?format=json").json()
    assert as_json["title"] == "RAG survey"
    assert client.get(f"{base}/revisions/unknown/report").status_code == 404


def test_v2_run_summary_in_get_run(client, v2_env):
    run_id = v2_env["run_id"]
    body = client.get(f"/api/exploration/runs/{run_id}").json()
    assert body["workflow_version"] == "academic_survey_v2"
    assert body["state_version"] == 4
    assert body["protocol"]["protocol_id"] == "proto-1"
    assert body["current_revision"]["revision_id"] == v2_env["revision_id"]
    summaries = client.get("/api/exploration/runs?limit=10").json()
    target = next(s for s in summaries if s["id"] == run_id)
    assert target["workflow_version"] == "academic_survey_v2"


# ============================================================
# 状态 CAS：pause/resume/cancel 幂等与 409
# ============================================================


def test_pause_resume_cancel_cas_semantics(client, engine, v2_env):
    run_id = v2_env["run_id"]
    base = f"/api/exploration/runs/{run_id}"
    assert client.post(f"{base}/pause", json={}).json()["status"] == "paused"

    # 幂等重放：状态不变，返回原 state_version
    again = client.post(f"{base}/pause", json={})
    assert again.status_code == 200 and again.json()["idempotent_replay"] is True

    # 陈旧版本 → 409
    stale = client.post(f"{base}/resume", json={"expected_state_version": 1})
    assert stale.status_code == 409 and stale.json()["code"] == "STALE_STATE_VERSION"
    # 期望状态不符 → 409
    conflict = client.post(f"{base}/resume", json={"expected_state": "running"})
    assert conflict.status_code == 409 and conflict.json()["code"] == "STATE_CONFLICT"

    resumed = client.post(f"{base}/resume", json={})
    assert resumed.json()["status"] == "running"
    with Session(engine) as session:
        run = session.get(ExplorationRun, run_id)
        assert run.state_version == 6  # 4 → pause(5) → resume(6)
        events = session.exec(
            select(event_log.ResearchEvent).where(event_log.ResearchEvent.run_id == run_id)
        ).all()
        types = [e.type for e in events]
        assert "run_paused" in types and "run_resumed" in types

    cancelled = client.post(f"{base}/cancel", json={})
    assert cancelled.json()["status"] == "cancelled"
    # 终态不允许再暂停
    assert client.post(f"{base}/pause", json={}).status_code == 409


def test_retry_requeues_failed_only_and_is_idempotent(client, engine, v2_env):
    run_id = v2_env["run_id"]
    base = f"/api/exploration/runs/{run_id}"
    client.post(f"{base}/cancel", json={})

    # 显式 task_ids 的校验在可重试状态下进行：未知任务 → 404
    missing = client.post(f"{base}/retry", json={"task_ids": ["nope"]})
    assert missing.status_code == 404 and missing.json()["code"] == "TASK_NOT_FOUND"

    first = client.post(f"{base}/retry", json={"idempotency_key": "retry-1"})
    assert first.status_code == 200
    # 版本轨迹：fixture 4 → cancel 5 → retry 6
    assert first.json() == {"run_id": run_id, "status": "pending", "state_version": 6,
                            "retried_tasks": 1, "idempotent_replay": False}
    with Session(engine) as session:
        failed = session.exec(select(ResearchTask).where(
            ResearchTask.task_id == v2_env["failed_task_id"])).one()
        assert failed.state == "pending"
        succeeded = [t for t in session.exec(select(ResearchTask).where(
            ResearchTask.run_id == run_id)).all() if t.kind == "search"]
        assert all(t.state == "succeeded" for t in succeeded)

    replay = client.post(f"{base}/retry", json={"idempotency_key": "retry-1"})
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["retried_tasks"] == 1

    # 幂等重放先于状态检查；非幂等重试在 pending 状态 → 409
    assert client.post(f"{base}/retry", json={}).status_code == 409


# ============================================================
# SSE：持久化事件回放与断点续订
# ============================================================


def test_v2_stream_replays_persisted_events_with_cursor(client, engine, v2_env):
    run_id = v2_env["run_id"]
    with Session(engine) as session:
        events = event_log.replay_events(session, run_id)
        second_id = events[1].event_id
    client.post(f"/api/exploration/runs/{run_id}/cancel", json={})  # 终态：回放后关闭

    with client.stream("GET", f"/api/exploration/runs/{run_id}/stream") as response:
        assert response.status_code == 200
        text = "".join(chunk for chunk in response.iter_text())
    frames = [block for block in text.split("\n\n") if block.strip()]
    assert any(block.startswith(f"id: {events[0].event_id}\nevent: run_created") for block in frames)
    assert any("budget_stop" in block for block in frames)
    assert any("event: run_status" in block for block in frames)

    # Last-Event-ID 断点续订：只回放游标之后的事件
    with client.stream("GET", f"/api/exploration/runs/{run_id}/stream",
                       headers={"Last-Event-ID": second_id}) as response:
        resumed = "".join(chunk for chunk in response.iter_text())
    assert f"id: {events[0].event_id}" not in resumed
    assert f"id: {events[1].event_id}" not in resumed
    assert "run_cancelled" in resumed
    # 查询参数游标等价
    with client.stream("GET", f"/api/exploration/runs/{run_id}/stream?after={second_id}") as response:
        via_query = "".join(chunk for chunk in response.iter_text())
    assert via_query == resumed

    # 未知游标 → 422
    assert client.get(f"/api/exploration/runs/{run_id}/stream?after=deadbeef").status_code == 422


def test_v1_stream_unchanged(client, v1_env):
    with client.stream("GET", f"/api/exploration/runs/{v1_env}/stream") as response:
        assert response.status_code == 200
        text = "".join(chunk for chunk in response.iter_text())
    assert "event: done" in text


# ============================================================
# CORS：新端点的预检与实际请求（允许 Origin 放行、非允许 Origin 拒绝）
# ============================================================


def test_v2_endpoints_cors_contract(client, v2_env):
    from backend.config import settings

    base = f"/api/exploration/runs/{v2_env['run_id']}"
    for method, url in (("POST", f"{base}/pause"), ("POST", f"{base}/retry"),
                        ("GET", f"{base}/tasks")):
        preflight = client.options(url, headers={
            "Origin": settings.frontend_origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "content-type",
        })
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == settings.frontend_origin
    post = client.post(f"{base}/pause", json={},
                      headers={"Origin": settings.frontend_origin})
    assert post.status_code == 200
    assert post.headers["access-control-allow-origin"] == settings.frontend_origin
    untrusted = client.options(f"{base}/pause", headers={
        "Origin": "https://untrusted.invalid", "Access-Control-Request-Method": "POST",
    })
    assert "access-control-allow-origin" not in untrusted.headers


# ============================================================
# 错误格式：{detail, code} 顶层契约
# ============================================================


def test_missing_run_returns_contract_errors(client):
    for method, path in (("GET", "/protocol"), ("GET", "/tasks"), ("GET", "/coverage"),
                         ("GET", "/budget"), ("POST", "/pause"), ("POST", "/resume"),
                         ("POST", "/cancel"), ("POST", "/retry"), ("GET", "/revisions")):
        response = client.request(method, f"/api/exploration/runs/99999/{path}",
                                  json={} if method == "POST" else None)
        assert response.status_code == 404, path
        assert set(response.json()) == {"detail", "code"}
        assert response.json()["code"] == "NOT_FOUND"


# ============================================================
# F1（复审）：CAS 原子性——并发竞争仅一个成功
# ============================================================


def test_concurrent_cas_opposite_targets_only_one_wins(engine, v2_env):
    """两个请求携带同一旧版本竞争相反目标：仅一个成功，另一 409，版本只增一次。

    持有强引用：身份映射是弱引用，丢弃实例会使其被 GC，cas 内部 session.get
    将重读数据库拿到新状态，竞争场景就不成立了。
    """
    from backend.models.research_core import RUN_ACTIVE_STATES_V2, ResearchEvent

    run_id = v2_env["run_id"]  # running / state_version=4
    with Session(engine) as s1, Session(engine) as s2:
        held1 = s1.get(ExplorationRun, run_id)  # noqa: F841 — 保持实例存活
        held2 = s2.get(ExplorationRun, run_id)  # noqa: F841
        run1, replay = event_log.cas_run_state(
            s1, run_id=run_id, new_status="paused", expected_state="running",
            expected_state_version=4, allowed_from=RUN_ACTIVE_STATES_V2, event_type="run_paused",
        )
        assert (run1.status, run1.state_version, replay) == ("paused", 5, False)
        with pytest.raises(event_log.RunStateError) as exc:
            event_log.cas_run_state(
                s2, run_id=run_id, new_status="cancelled", expected_state="running",
                expected_state_version=4, allowed_from=RUN_ACTIVE_STATES_V2,
                event_type="run_cancelled",
            )
        assert exc.value.status_code == 409
    with Session(engine) as session:
        run = session.get(ExplorationRun, run_id)
        assert (run.status, run.state_version) == ("paused", 5)
        events = session.exec(select(ResearchEvent).where(ResearchEvent.run_id == run_id)).all()
        paused = [e for e in events if e.type == "run_paused"]
        cancelled = [e for e in events if e.type == "run_cancelled"]
        assert len(paused) == 1 and paused[0].payload["state_version"] == 5
        assert cancelled == []  # 冲突不追加成功事件


def test_concurrent_cas_same_target_only_one_wins(engine, v2_env):
    """同目标并发：第二个携带同一旧版本的请求按冲突拒绝，不产生第二次版本推进。"""
    from backend.models.research_core import RUN_ACTIVE_STATES_V2

    run_id = v2_env["run_id"]
    with Session(engine) as s1, Session(engine) as s2:
        held1 = s1.get(ExplorationRun, run_id)  # noqa: F841 — 保持实例存活
        held2 = s2.get(ExplorationRun, run_id)  # noqa: F841
        _, replay = event_log.cas_run_state(
            s1, run_id=run_id, new_status="paused", expected_state_version=4,
            allowed_from=RUN_ACTIVE_STATES_V2, event_type="run_paused")
        assert replay is False
        with pytest.raises(event_log.RunStateError):
            event_log.cas_run_state(
                s2, run_id=run_id, new_status="paused", expected_state_version=4,
                allowed_from=RUN_ACTIVE_STATES_V2, event_type="run_paused")
    with Session(engine) as session:
        assert session.get(ExplorationRun, run_id).state_version == 5


def test_cas_replay_distinguishes_same_transition_from_stale(engine, v2_env):
    """幂等重放判定：无前置条件或 expected_state_version+1==当前版本视为同一迁移重放；
    版本落后更多按陈旧请求 409。"""
    from backend.models.research_core import RUN_ACTIVE_STATES_V2

    run_id = v2_env["run_id"]  # running / v4
    with Session(engine) as session:
        _, replay = event_log.cas_run_state(
            session, run_id=run_id, new_status="paused", expected_state_version=4,
            allowed_from=RUN_ACTIVE_STATES_V2, event_type="run_paused")
        assert replay is False
        # 同一迁移（4→5）的重放：返回原结果
        run, replay = event_log.cas_run_state(
            session, run_id=run_id, new_status="paused", expected_state_version=4,
            allowed_from=RUN_ACTIVE_STATES_V2, event_type="run_paused")
        assert replay is True and (run.status, run.state_version) == ("paused", 5)
        # 无前置条件：接受当前状态
        _, replay = event_log.cas_run_state(
            session, run_id=run_id, new_status="paused",
            allowed_from=RUN_ACTIVE_STATES_V2, event_type="run_paused")
        assert replay is True
        # 陈旧版本（3）：当前 5 != 3+1 → 409
        with pytest.raises(event_log.RunStateError) as exc:
            event_log.cas_run_state(
                session, run_id=run_id, new_status="paused", expected_state_version=3,
                allowed_from=RUN_ACTIVE_STATES_V2, event_type="run_paused")
        assert exc.value.code == "STALE_STATE_VERSION"
        assert session.get(ExplorationRun, run_id).state_version == 5


# ============================================================
# F2（复审）：任务幂等键按 run 作用域
# ============================================================


def test_task_idempotency_scoped_to_run(engine):
    from sqlalchemy.exc import IntegrityError

    with Session(engine) as session:
        run_a = ExplorationRun(question="run a")
        run_b = ExplorationRun(question="run b")
        session.add_all([run_a, run_b])
        session.commit()
        session.add(ResearchTask(run_id=run_a.id, kind="extract", idempotency_key="key-1"))
        session.commit()
        # 两个 run 对相同冻结输入各建独立账本
        session.add(ResearchTask(run_id=run_b.id, kind="extract", idempotency_key="key-1"))
        session.commit()
        # 同一 run 内重复调度被唯一索引拦截
        session.add(ResearchTask(run_id=run_a.id, kind="extract", idempotency_key="key-1"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_task_idempotency_index_migrates_from_global(tmp_path):
    """旧 S0 库（全局唯一索引）升级后与新建库行为一致。"""
    db_url = f"sqlite:///{tmp_path / 'idx.db'}"
    init_db(db_url)
    engine = create_engine(db_url)
    with engine.connect() as conn:  # 还原旧形态：run 内唯一索引 → 全局唯一索引
        conn.exec_driver_sql("DROP INDEX IF EXISTS uq_research_tasks_run_idempotency")
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_research_tasks_idempotency "
            "ON research_tasks(idempotency_key) WHERE idempotency_key IS NOT NULL"
        )
        conn.commit()
    _migrate_schema(engine)
    _migrate_schema(engine)  # 幂等
    with engine.connect() as conn:
        names = {row[0] for row in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='research_tasks'")}
    assert "uq_research_tasks_idempotency" not in names
    assert "uq_research_tasks_run_idempotency" in names
    from sqlalchemy.exc import IntegrityError

    with Session(engine) as session:
        run_a = ExplorationRun(question="a")
        run_b = ExplorationRun(question="b")
        session.add_all([run_a, run_b])
        session.commit()
        session.add(ResearchTask(run_id=run_a.id, kind="extract", idempotency_key="same"))
        session.add(ResearchTask(run_id=run_b.id, kind="extract", idempotency_key="same"))
        session.commit()
        session.add(ResearchTask(run_id=run_a.id, kind="extract", idempotency_key="same"))
        with pytest.raises(IntegrityError):
            session.commit()


# ============================================================
# F3（复审）：默认报告接口解析当前选定 revision
# ============================================================


def test_v2_default_report_resolves_selected_revision(client, engine, v2_env):
    run_id = v2_env["run_id"]
    base = f"/api/exploration/runs/{run_id}"
    as_json = client.get(f"{base}/report?format=json")
    assert as_json.status_code == 200
    body = as_json.json()
    assert body["revision_id"] == v2_env["revision_id"]
    assert body["title"] == "RAG survey"
    assert as_json.headers["x-report-revision"] == v2_env["revision_id"]
    assert "# 报告" in client.get(f"{base}/report?format=markdown").text

    # 切换选定版本：默认入口随之变化；指定旧版本仍保持原内容
    with Session(engine) as session:
        old = session.exec(select(ResearchReportRevision).where(
            ResearchReportRevision.revision_id == v2_env["revision_id"])).one()
        old.is_selected = False
        asset2 = ResearchAsset(run_id=run_id, content_kind="report", format="json",
                               inline_payload={"markdown": "# 第二版报告", "title": "second"})
        session.add(asset2)
        session.flush()
        session.add(ResearchReportRevision(run_id=run_id, report_asset_id=asset2.asset_id,
                                           is_selected=True, quality_status="unchecked"))
        session.commit()
        new_rid = session.exec(select(ResearchReportRevision).where(
            ResearchReportRevision.is_selected == True,  # noqa: E712
            ResearchReportRevision.run_id == run_id)).one().revision_id  # type: ignore[arg-type]

    default_now = client.get(f"{base}/report?format=json").json()
    assert default_now["revision_id"] == new_rid and default_now["title"] == "second"
    old_still = client.get(f"{base}/revisions/{v2_env['revision_id']}/report?format=json").json()
    assert old_still["title"] == "RAG survey"


def test_v2_default_report_without_selection_rejects_stale_result(client, engine):
    """无选定 revision 时 404；result 中的历史快照不得静默顶替。"""
    with Session(engine) as session:
        run = ExplorationRun(
            question="有旧快照的 v2", status="completed",
            workflow_version=WORKFLOW_ACADEMIC_SURVEY_V2,
            result={"survey_report": {"markdown": "# 过时快照"}},
        )
        session.add(run)
        session.commit()
        run_id = run.id
    response = client.get(f"/api/exploration/runs/{run_id}/report?format=json")
    assert response.status_code == 404
    assert response.json()["code"] == "REPORT_NOT_FOUND"


# ============================================================
# F4（复审）：隔离报告资产拒绝导出
# ============================================================


def test_quarantined_report_asset_blocks_all_exports(client, engine, v2_env):
    run_id = v2_env["run_id"]
    base = f"/api/exploration/runs/{run_id}"
    revision_id = v2_env["revision_id"]
    assert client.get(f"{base}/report?format=markdown").status_code == 200  # 隔离前可读

    with Session(engine) as session:
        revision = session.exec(select(ResearchReportRevision).where(
            ResearchReportRevision.revision_id == revision_id)).one()
        asset = session.exec(select(ResearchAsset).where(
            ResearchAsset.asset_id == revision.report_asset_id)).one()
        asset.status = "quarantined"
        session.add(asset)
        session.commit()

    for url in (f"{base}/report", f"{base}/revisions/{revision_id}/report"):
        for fmt in ("json", "markdown", "html"):
            response = client.get(f"{url}?format={fmt}")
            assert response.status_code == 403, (url, fmt)
            assert response.json()["code"] == "REPORT_NOT_EXPOSED"
            assert "报告" not in response.text or "不对外暴露" in response.text


def test_report_asset_visibility_rules(client, engine, v2_env):
    run_id = v2_env["run_id"]
    base = f"/api/exploration/runs/{run_id}"
    revision_id = v2_env["revision_id"]
    with Session(engine) as session:
        revision = session.exec(select(ResearchReportRevision).where(
            ResearchReportRevision.revision_id == revision_id)).one()
        asset = session.exec(select(ResearchAsset).where(
            ResearchAsset.asset_id == revision.report_asset_id)).one()
        # superseded 历史版本仍可读（不可变历史）
        asset.status = "superseded"
        session.add(asset)
        session.commit()
    assert client.get(f"{base}/revisions/{revision_id}/report?format=json").status_code == 200

    # 资产归属其他 run → 404
    with Session(engine) as session:
        other = ExplorationRun(question="另一个 run", status="completed")
        session.add(other)
        session.commit()
        revision = session.exec(select(ResearchReportRevision).where(
            ResearchReportRevision.revision_id == revision_id)).one()
        asset = session.exec(select(ResearchAsset).where(
            ResearchAsset.asset_id == revision.report_asset_id)).one()
        asset.status = "active"
        asset.run_id = other.id
        session.add(asset)
        session.commit()
    assert client.get(f"{base}/revisions/{revision_id}/report?format=json").status_code == 404


# ============================================================
# F5（复审）：回滚形态 = S0 契约层（只读 + 写入阻断 + 执行门禁）
# ============================================================


def test_rollback_form_keeps_v2_readonly_and_blocks_writes(client, engine, v2_env, monkeypatch):
    """声明的回滚形态是「保留 S0 契约层」：v2 数据经 HTTP 只读可访问，
    新 v2 执行关闭，旧写入路径（v1 报告重建/删除）不能改动 v2，v1 入口照常。"""
    from backend.routers import exploration

    run_id = v2_env["run_id"]
    base = f"/api/exploration/runs/{run_id}"
    # v2 只读可用（报告/版本/证据/协议）
    assert client.get(f"{base}/revisions").status_code == 200
    assert client.get(f"{base}/report?format=markdown").status_code == 200
    assert client.get(f"{base}/revisions/{v2_env['revision_id']}/report?format=json").status_code == 200
    assert client.get(f"{base}/protocol").status_code == 200
    # 新 v2 执行入口关闭（先取消活动 run，避开「已有运行中任务」的 409）
    cancelled = client.post(f"{base}/cancel", json={})
    assert cancelled.status_code == 200
    gated = client.post("/api/exploration/runs", json={
        "question": "回滚形态不启动新 v2 研究", "workflow_version": "academic_survey_v2"})
    assert gated.json()["code"] == "WORKFLOW_NOT_IMPLEMENTED"
    # 旧写入路径不能覆盖 v2
    assert client.post(f"{base}/report").json()["code"] == "WORKFLOW_NOT_IMPLEMENTED"
    assert client.delete(base).json()["code"] == "WORKFLOW_NOT_IMPLEMENTED"
    # v1 入口照常
    monkeypatch.setattr(exploration, "get_engine", lambda: engine)

    async def fake_run(run_id):
        return None

    monkeypatch.setattr(exploration, "run_exploration", fake_run)
    assert client.post("/api/exploration/runs", json={"question": "v1 照常运行"}).status_code == 200
