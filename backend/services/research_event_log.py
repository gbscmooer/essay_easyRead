"""academic_survey_v2 持久化事件、运行状态 CAS 与预算聚合

分工边界：
- 本模块提供事件追加/回放、run 状态 compare-and-swap、live-bus 注册缝、
  预算账本聚合、重试幂等查询。
- question_loop_v1 通过事务仓库写任务/租约/预算；SSE 直接轮询持久化事件游标。
- SSE 只是订阅持久化事件（research_events），不是任务运行的唯一状态来源。

事务约定：append_event 只挂到 session，由调用方提交；cas_run_state 自带提交，
状态变更与事件在同一个事务里落库。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlmodel import Session, select

from ..models.exploration import ExplorationRun
from ..models.research_core import (
    WORKFLOW_ACADEMIC_SURVEY_V2,
    ResearchBudgetEvent,
    ResearchEvent,
)


class RunStateError(Exception):
    """运行状态操作失败（路由层映射为 {detail, code} HTTP 错误）"""

    def __init__(self, status_code: int, code: str, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


# ============================================================
# 持久化事件
# ============================================================


def append_event(
    session: Session,
    *,
    run_id: int,
    type: str,
    payload: Optional[dict] = None,
    task_id: Optional[str] = None,
    payload_version: int = 1,
    event_id: Optional[str] = None,
) -> ResearchEvent:
    """追加持久化事件（不提交；调用方在同一事务内提交）。"""
    row = ResearchEvent(
        run_id=run_id,
        task_id=task_id,
        type=type,
        payload_version=payload_version,
        payload=payload or {},
    )
    if event_id:
        row.event_id = event_id
    session.add(row)
    return row


def replay_events(
    session: Session,
    run_id: int,
    after_event_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[ResearchEvent]:
    """按自增序回放事件；after_event_id 为空从头开始。

    未知的 after_event_id 抛 ValueError（调用方按游标无效处理，不静默重放全部）。
    """
    after_seq = 0
    if after_event_id:
        anchor = session.exec(
            select(ResearchEvent).where(
                ResearchEvent.run_id == run_id, ResearchEvent.event_id == after_event_id
            )
        ).first()
        if anchor is None or anchor.id is None:
            raise ValueError(f"unknown event cursor: {after_event_id}")
        after_seq = anchor.id
    statement = (
        select(ResearchEvent)
        .where(ResearchEvent.run_id == run_id, ResearchEvent.id > after_seq)
        .order_by(ResearchEvent.id)
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list(session.exec(statement).all())


def latest_events(session: Session, run_id: int, type: str, limit: int = 5) -> list[ResearchEvent]:
    """最近的指定类型事件（倒序取再正序返回，供 coverage 最近动作等投影）。"""
    rows = session.exec(
        select(ResearchEvent)
        .where(ResearchEvent.run_id == run_id, ResearchEvent.type == type)
        .order_by(ResearchEvent.id.desc())  # type: ignore[arg-type]
        .limit(limit)
    ).all()
    return list(reversed(rows))


# ============================================================
# live-bus 注册缝（S3 接入实时事件；在此之前 v2 活动流无法实时订阅）
# ============================================================

_live_buses: dict[int, Any] = {}


def register_live_bus(run_id: int, bus: Any) -> None:
    """S3 worker 启动时注册实时事件源（异步迭代器/事件总线）。"""
    _live_buses[run_id] = bus


def get_live_bus(run_id: int) -> Optional[Any]:
    return _live_buses.get(run_id)


def drop_live_bus(run_id: int) -> None:
    _live_buses.pop(run_id, None)


# ============================================================
# 运行状态 compare-and-swap（计划 §7.1）
# ============================================================


def guarded_transition(
    session: Session,
    *,
    run_id: int,
    from_status: str,
    from_version: int,
    new_status: str,
) -> bool:
    """原子条件状态更新：仅当运行仍处于 from_status/from_version 时推进。

    数据库端以旧状态+旧版本为 UPDATE 守卫并校验受影响行数；Python 侧的前置
    检查只是快速失败，本函数才是并发权威（两个请求竞争同一旧版本时仅一个
    返回 True）。调用方负责在同一事务内追加事件并提交；失败时应回滚会话。
    """
    from sqlalchemy import update as sa_update

    stmt = (
        sa_update(ExplorationRun)
        .where(
            ExplorationRun.id == run_id,
            ExplorationRun.status == from_status,
            ExplorationRun.state_version == from_version,
        )
        .values(
            status=new_status,
            state_version=from_version + 1,
            updated_at=datetime.now(timezone.utc),
        )
    )
    return session.exec(stmt).rowcount == 1  # type: ignore[attr-defined]


def cas_run_state(
    session: Session,
    *,
    run_id: int,
    new_status: str,
    expected_state: Optional[str] = None,
    expected_state_version: Optional[int] = None,
    allowed_from: Optional[tuple[str, ...]] = None,
    event_type: str = "run_state_changed",
    payload: Optional[dict] = None,
) -> tuple[ExplorationRun, bool]:
    """事务内 CAS 更新运行状态；返回 (run, idempotent_replay)。

    并发权威是 guarded_transition 的数据库条件更新（WHERE 旧状态+旧版本，
    校验受影响行数）；Python 前置检查只做快速失败，冲突不追加成功事件。

    幂等重放判定（区别于陈旧请求）：
    - 当前状态已是目标状态，且请求未携带前置条件 → 重放成功，不递增版本；
    - 携带 expected_state_version 且其值 +1 == 当前版本 → 同一迁移已发生，
      视为该请求的重放，返回原结果；
    - 其余情况（版本落后更多/状态不符）按陈旧或冲突请求返回 409。
    """
    run = session.get(ExplorationRun, run_id)
    if run is None:
        raise RunStateError(404, "NOT_FOUND", "研究记录不存在")
    if run.workflow_version != WORKFLOW_ACADEMIC_SURVEY_V2:
        raise RunStateError(400, "UNSUPPORTED_WORKFLOW", "此操作仅支持 academic_survey_v2 工作流")

    current_status, current_version = run.status, run.state_version
    if current_status == new_status:
        if expected_state_version is None or current_version == expected_state_version + 1:
            return run, True
        raise RunStateError(
            409, "STALE_STATE_VERSION",
            f"状态已推进（当前 state_version={current_version}），该请求按陈旧版本拒绝",
        )
    if allowed_from is not None and current_status not in allowed_from:
        raise RunStateError(
            409, "INVALID_STATE", f"当前状态 {current_status} 不允许此操作（允许：{'/'.join(allowed_from)}）"
        )
    if expected_state is not None and current_status != expected_state:
        raise RunStateError(409, "STATE_CONFLICT", f"expected_state 与当前状态不一致（当前 {current_status}）")
    if expected_state_version is not None and current_version != expected_state_version:
        raise RunStateError(
            409, "STALE_STATE_VERSION", f"状态版本过旧（当前 state_version={current_version}）"
        )

    new_version = current_version + 1
    applied = guarded_transition(
        session, run_id=run_id, from_status=current_status,
        from_version=current_version, new_status=new_status,
    )
    if not applied:
        session.rollback()
        raise RunStateError(
            409, "STATE_CONFLICT", "并发状态变更冲突，请以当前状态重试"
        )
    run.status, run.state_version = new_status, new_version
    session.add(run)
    event_payload = {"status": new_status, "state_version": new_version}
    if payload:
        event_payload.update(payload)
    append_event(session, run_id=run_id, type=event_type, payload=event_payload)
    session.commit()
    session.refresh(run)
    return run, False


# ============================================================
# 预算账本聚合（口径冻结见 models/research_core.ResearchBudgetEvent）
# ============================================================


def budget_snapshot(session: Session, run_id: int, limits: Optional[dict] = None) -> list[dict]:
    """按资源聚合 limit/used/reserved。

    used = Σ delta_used；reserved = Σ(delta_reserved − delta_used − delta_refunded)，整体下限 0。
    usage_known=False 表示存在"响应丢失、按上界记账"的窗口，费用不可精确核验。
    """
    limits = limits or {}
    rows = session.exec(select(ResearchBudgetEvent).where(ResearchBudgetEvent.run_id == run_id)).all()
    acc: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = acc.setdefault(
            row.resource, {"reserved": 0, "used": 0, "refunded": 0, "unit": row.unit, "usage_known": True}
        )
        entry["reserved"] += row.delta_reserved
        entry["used"] += row.delta_used
        entry["refunded"] += row.delta_refunded
        if not row.usage_known:
            entry["usage_known"] = False
    result = []
    for resource in sorted(set(limits) | set(acc)):
        entry = acc.get(resource, {"reserved": 0, "used": 0, "refunded": 0, "unit": "count", "usage_known": True})
        outstanding = max(entry["reserved"] - entry["used"] - entry["refunded"], 0)
        result.append({
            "resource": resource,
            "limit": limits.get(resource),
            "used": entry["used"],
            "reserved": outstanding,
            "unit": entry["unit"],
            "usage_known": entry["usage_known"],
        })
    return result


def find_retry_event(session: Session, run_id: int, idempotency_key: str) -> Optional[ResearchEvent]:
    """重试幂等登记处：相同 idempotency_key 的 run_retry 事件即原结果。"""
    rows = session.exec(
        select(ResearchEvent).where(ResearchEvent.run_id == run_id, ResearchEvent.type == "run_retry")
    ).all()
    for row in rows:
        if (row.payload or {}).get("idempotency_key") == idempotency_key:
            return row
    return None
