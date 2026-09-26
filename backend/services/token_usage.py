"""Persistent LLM token usage statistics: buffered writes + SQL aggregations.

The LLM Core reports every successful call's normalized usage through a
non-blocking in-process recorder installed here; a daemon thread flushes the
buffer to SQLite. 统计丢失或落库失败绝不影响业务调用。
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, func, or_
from sqlmodel import Session, select

from ..models.token_usage import LLMUsageRecord
from .database import get_engine
from .llm import set_usage_recorder

logger = logging.getLogger(__name__)

_FLUSH_INTERVAL_S = 5.0
_MAX_MODELS = 8

_pending: deque[dict] = deque()
_pending_lock = threading.Lock()
_flusher_started = threading.Event()
_flusher_stop = threading.Event()

_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens",
                 "cache_write_tokens", "reasoning_tokens")

# 旧记录 elapsed_ms=0（未计时）：TPS 对 0 耗时不可计算，返回 None 而不是假值
_ELAPSED_FIELDS = ("elapsed_ms",)


def _tps(output_tokens: int, elapsed_ms: int) -> float | None:
    if elapsed_ms <= 0 or output_tokens <= 0:
        return None
    return round(output_tokens / (elapsed_ms / 1000.0), 1)


def _enqueue(record: dict) -> None:
    """LLM Core 的 recorder 回调：仅做内存缓冲，绝不阻塞事件循环。"""
    with _pending_lock:
        _pending.append(dict(record))


def install() -> None:
    """安装全局 recorder 并启动后台落库线程（进程内幂等）。"""
    set_usage_recorder(_enqueue)
    if _flusher_started.is_set():
        return
    _flusher_started.set()
    threading.Thread(target=_flush_loop, name="llm-usage-flusher", daemon=True).start()


def shutdown() -> None:
    """停止后台线程并清空缓冲（应用退出时调用）。"""
    _flusher_stop.set()
    flush_pending()


def _flush_loop() -> None:
    while not _flusher_stop.wait(_FLUSH_INTERVAL_S):
        try:
            flush_pending()
        except Exception as exc:  # 统计落库失败不影响业务
            logger.warning(f"LLM 用量定时落库失败（忽略）: {exc}")


def flush_pending() -> int:
    """把缓冲中的用量记录一次性写入 SQLite，返回写入条数。"""
    with _pending_lock:
        batch = list(_pending)
        _pending.clear()
    if not batch:
        return 0
    rows = [
        LLMUsageRecord(
            category=str(item.get("category") or "other")[:64],
            stage=str(item.get("stage") or "")[:64],
            run_id=str(item["run_id"])[:128] if item.get("run_id") else None,
            run_instance_id=str(item.get("run_instance_id") or "")[:64],
            operation=str(item.get("operation") or "")[:32],
            status="error" if str(item.get("status") or "") == "error" else "ok",
            error_code=str(item.get("error_code") or "")[:64],
            model=str(item.get("model") or "")[:128],
            api_format=str(item.get("api_format") or "")[:32],
            **{key: max(0, int(item.get(key) or 0)) for key in (*_TOKEN_FIELDS, *_ELAPSED_FIELDS)},
        )
        for item in batch
    ]
    try:
        with Session(get_engine()) as session:
            session.add_all(rows)
            session.commit()
    except Exception as exc:
        logger.warning(f"LLM 用量批量写入失败，丢弃 {len(rows)} 条（忽略）: {exc}")
    return len(rows)


def _empty_counters() -> dict[str, int | None]:
    return {
        "calls": 0,
        "failed_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "elapsed_ms": 0,
        "tps": None,
    }


def _with_total(row: dict[str, int]) -> dict[str, int]:
    row["total_tokens"] = row["input_tokens"] + row["output_tokens"]
    return row


def _grouped(session: Session, key_expr, since: datetime | None) -> list[tuple[Any, dict]]:
    stmt = select(
        key_expr,
        func.count(),
        func.coalesce(func.sum(LLMUsageRecord.input_tokens), 0),
        func.coalesce(func.sum(LLMUsageRecord.output_tokens), 0),
        func.coalesce(func.sum(LLMUsageRecord.cached_input_tokens), 0),
        func.coalesce(func.sum(LLMUsageRecord.cache_write_tokens), 0),
        func.coalesce(func.sum(LLMUsageRecord.reasoning_tokens), 0),
        func.coalesce(func.sum(LLMUsageRecord.elapsed_ms), 0),
        func.coalesce(func.sum(case(
            (LLMUsageRecord.elapsed_ms > 0, LLMUsageRecord.output_tokens),
            else_=0,
        )), 0),
        func.coalesce(func.sum(case(
            (LLMUsageRecord.status == "error", 1), else_=0,
        )), 0),
    ).group_by(key_expr)
    if since is not None:
        stmt = stmt.where(LLMUsageRecord.created_at >= since)
    result: list[tuple[Any, dict]] = []
    for row in session.exec(stmt).all():
        elapsed_ms = int(row[7])
        counters = _with_total({
            "calls": int(row[1]),
            "failed_calls": int(row[9]),
            "input_tokens": int(row[2]),
            "output_tokens": int(row[3]),
            "cached_input_tokens": int(row[4]),
            "cache_write_tokens": int(row[5]),
            "reasoning_tokens": int(row[6]),
            "elapsed_ms": elapsed_ms,
            "timed_output_tokens": int(row[8]),
            # 分子只计有计时记录的输出，避免未计时旧记录虚高 TPS
            "tps": _tps(int(row[8]), elapsed_ms),
        })
        result.append((row[0], counters))
    return result


def usage_summary(days: int | None = 30) -> dict:
    """全局用量统计：合计 + 按业务类目 + 按天 + 按模型。days=None 表示全部。"""
    flush_pending()
    since = None
    if days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    with Session(get_engine()) as session:
        by_category = [
            {"category": str(key or "other"), **counters}
            for key, counters in _grouped(session, LLMUsageRecord.category, since)
        ]
        by_category.sort(key=lambda row: row["total_tokens"], reverse=True)
        by_day = [
            {"date": str(key), **counters}
            for key, counters in _grouped(
                session, func.date(LLMUsageRecord.created_at), since,
            )
        ]
        by_day.sort(key=lambda row: row["date"])
        models = [
            {"model": str(key or ""), **counters}
            for key, counters in _grouped(session, LLMUsageRecord.model, since)
        ]
        models.sort(key=lambda row: row["total_tokens"], reverse=True)
    totals = _empty_counters()
    for row in by_category:
        for field_name in ("calls", "failed_calls", *_TOKEN_FIELDS, "elapsed_ms"):
            totals[field_name] += row[field_name]
    totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    totals["tps"] = _tps(
        sum(int(row.get("timed_output_tokens") or 0) for row in by_category),
        totals["elapsed_ms"],
    )
    return {
        "since": since.isoformat() if since else None,
        "days": days,
        "totals": totals,
        "by_category": by_category,
        "by_day": by_day,
        "models": models[:_MAX_MODELS],
    }


def usage_records(days: int | None = 30, limit: int = 50, offset: int = 0) -> dict:
    """逐次调用明细（created_at 倒序分页）：设置页「查看详细信息」。

    成功调用（status=ok）含归一化 usage；失败尝试（status=error，token 0）
    也逐行列出——失败计数可见（计划 §16.5），error_code 只存异常类名。
    """
    flush_pending()
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    since = None
    if days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    with Session(get_engine()) as session:
        total_stmt = select(func.count()).select_from(LLMUsageRecord)
        rows_stmt = (
            select(LLMUsageRecord)
            .order_by(LLMUsageRecord.created_at.desc(), LLMUsageRecord.id.desc())
            .offset(offset)
            .limit(limit)
        )
        if since is not None:
            total_stmt = total_stmt.where(LLMUsageRecord.created_at >= since)
            rows_stmt = rows_stmt.where(LLMUsageRecord.created_at >= since)
        total = int(session.exec(total_stmt).one())
        rows = session.exec(rows_stmt).all()
    return {
        "since": since.isoformat() if since else None,
        "days": days,
        "total": total,
        "limit": limit,
        "offset": offset,
        "records": [
            {
                "id": row.id,
                "created_at": row.created_at.isoformat(),
                "category": row.category or "other",
                "stage": row.stage or "",
                "run_id": row.run_id,
                "run_instance_id": row.run_instance_id or "",
                "operation": row.operation or "",
                "status": row.status or "ok",
                "error_code": row.error_code or "",
                "model": row.model or "",
                "api_format": row.api_format or "",
                **_with_total({
                    "input_tokens": row.input_tokens,
                    "output_tokens": row.output_tokens,
                    "cached_input_tokens": row.cached_input_tokens,
                    "cache_write_tokens": row.cache_write_tokens,
                    "reasoning_tokens": row.reasoning_tokens,
                    "elapsed_ms": row.elapsed_ms,
                    "tps": _tps(row.output_tokens, row.elapsed_ms),
                }),
            }
            for row in rows
        ],
    }


def run_usage(run_id: str, since: datetime | None = None,
              run_instance_id: str | None = None) -> dict:
    """单个运行（探索 run / 解析任务等）的用量统计：合计 + 按阶段拆分。

    归属优先级（计划 §16.5）：
    - run_instance_id 精确匹配当前运行实例（数字 run_id 删除复用后不串账）；
    - 无实例 ID 的旧记录（身份列启用前落库）只能按 since 时间下限兜底——
      旧 run 的记录必然早于新 run 的 created_at，时间下限足够隔离；
    - 同时保留 since 的另一作用：过滤同实例下更早的历史窗口。

    失败尝试（status=error）计入 calls 与 failed_calls，token/耗时贡献为 0；
    TPS 分子只计有计时（elapsed_ms>0）记录的输出：旧未计时记录的输出混入
    分子会把 TPS 虚高（用户 2026-09-18 审查实测 94.9 tok/s 被高估）。
    """
    flush_pending()
    run_id = str(run_id)
    with Session(get_engine()) as session:
        stmt = select(
            LLMUsageRecord.stage,
            func.count(),
            func.coalesce(func.sum(LLMUsageRecord.input_tokens), 0),
            func.coalesce(func.sum(LLMUsageRecord.output_tokens), 0),
            func.coalesce(func.sum(LLMUsageRecord.cached_input_tokens), 0),
            func.coalesce(func.sum(LLMUsageRecord.cache_write_tokens), 0),
            func.coalesce(func.sum(LLMUsageRecord.reasoning_tokens), 0),
            func.coalesce(func.sum(LLMUsageRecord.elapsed_ms), 0),
            # 有计时记录的输出合计（TPS 分子）
            func.coalesce(func.sum(case(
                (LLMUsageRecord.elapsed_ms > 0, LLMUsageRecord.output_tokens),
                else_=0,
            )), 0),
            # 失败尝试计数（token 为 0，只影响 calls/failed_calls）
            func.coalesce(func.sum(case(
                (LLMUsageRecord.status == "error", 1), else_=0,
            )), 0),
        ).where(LLMUsageRecord.run_id == run_id)
        if run_instance_id:
            # 实例精确匹配；空实例列的旧记录按时间下限兜底（见 docstring）
            stmt = stmt.where(or_(
                LLMUsageRecord.run_instance_id == run_instance_id,
                LLMUsageRecord.run_instance_id == "",
            ))
        if since is not None:
            stmt = stmt.where(LLMUsageRecord.created_at >= since)
        stmt = stmt.group_by(LLMUsageRecord.stage)
        by_stage = [
            {
                "stage": str(row[0] or ""),
                **_with_total({
                    "calls": int(row[1]),
                    "failed_calls": int(row[9]),
                    "input_tokens": int(row[2]),
                    "output_tokens": int(row[3]),
                    "cached_input_tokens": int(row[4]),
                    "cache_write_tokens": int(row[5]),
                    "reasoning_tokens": int(row[6]),
                    "elapsed_ms": int(row[7]),
                    # 有计时记录的输出合计：本行 TPS 分子，同时供 totals 汇总
                    "timed_output_tokens": int(row[8]),
                    "tps": _tps(int(row[8]), int(row[7])),
                }),
            }
            for row in session.exec(stmt).all()
        ]
    by_stage.sort(key=lambda row: row["total_tokens"], reverse=True)
    totals = _empty_counters()
    for row in by_stage:
        for field_name in ("calls", "failed_calls", *_TOKEN_FIELDS, "elapsed_ms"):
            totals[field_name] += row[field_name]
    totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    totals["tps"] = _tps(
        sum(int(row.get("timed_output_tokens") or 0) for row in by_stage),
        totals["elapsed_ms"],
    )
    return {
        "run_id": run_id,
        "run_instance_id": run_instance_id,
        "totals": totals,
        "by_stage": by_stage,
        "recorded": totals["calls"] > 0,
        "source": "records",
    }


def legacy_run_usage(report: dict) -> dict:
    """把旧 run 的 result.token_usage 台账快照转换成 run_usage 同构返回。"""
    def _row(source: dict) -> dict[str, int | None]:
        return _with_total({
            "calls": int(source.get("calls") or 0),
            "failed_calls": 0,
            "input_tokens": int(source.get("input_tokens") or 0),
            "output_tokens": int(source.get("output_tokens") or 0),
            "cached_input_tokens": int(source.get("cached_input_tokens") or 0),
            "cache_write_tokens": int(source.get("cache_write_tokens") or 0),
            "reasoning_tokens": 0,
            "elapsed_ms": 0,
            "timed_output_tokens": 0,
            "tps": None,
        })

    by_stage = [
        {"stage": str(stage), **_row(row)}
        for stage, row in (report or {}).items()
        if stage != "total" and isinstance(row, dict)
    ]
    by_stage.sort(key=lambda row: row["total_tokens"], reverse=True)
    total = report.get("total") if isinstance(report.get("total"), dict) else {}
    totals = _row(total)
    return {
        "run_id": None,
        "totals": totals,
        "by_stage": by_stage,
        "recorded": totals["calls"] > 0,
        "source": "legacy_result",
    }
