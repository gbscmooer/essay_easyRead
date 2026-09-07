"""
Exploration 路由 — 问句驱动的领域探索（GOAL.md 核心命题）

创建/查询探索任务、SSE 进度流。产物结构见 models/exploration.py。
"""

import asyncio
import logging
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from ..services.database import get_engine
from ..models.exploration import ExplorationRun
from ..models.schemas import (
    StreamEvent,
    ExplorationRunCreate, ExplorationRunResponse, ExplorationRunSummary,
)
from ..utils.streaming import format_sse
from ..services.deep_research.explorer import (
    get_exploration_bus, has_exploration_bus, run_exploration,
)
from ..routers.research import _stream_bus_with_heartbeat

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/exploration", tags=["exploration"])


def _replay_payload(result: dict | None) -> dict:
    """done 回放用的产物（去掉 synthesis，它由独立的 synthesis_ready 事件携带）。"""
    if not result:
        return {}
    return {k: v for k, v in result.items() if k != "synthesis"}


@router.post("/runs", response_model=ExplorationRunResponse)
async def create_exploration(req: ExplorationRunCreate) -> ExplorationRunResponse:
    engine = get_engine()
    with Session(engine) as session:
        # 同一时刻只允许一个探索任务（检索配额与 LLM 成本控制）
        existing = session.exec(
            select(ExplorationRun)
            .where(ExplorationRun.status.in_(["running", "pending"]))
        ).first()
        if existing:
            raise HTTPException(409, detail={"detail": "已有运行中的探索任务", "code": "EXPLORATION_RUNNING"})

        run = ExplorationRun(question=req.question.strip(), depth=req.depth, status="pending")
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id
        if run_id is None:
            raise HTTPException(500, detail={"detail": "探索任务创建失败", "code": "INTERNAL_ERROR"})

    # 提前建总线：前端在后台任务获得调度前订阅也不至于拿到「无 bus」
    get_exploration_bus(run_id)
    asyncio.create_task(run_exploration(run_id))

    with Session(engine) as session:
        run = session.get(ExplorationRun, run_id)
        if not run:
            raise HTTPException(404, detail={"detail": "Run not found", "code": "NOT_FOUND"})
        return ExplorationRunResponse.model_validate(run, from_attributes=True)


@router.get("/runs", response_model=list[ExplorationRunSummary])
async def list_explorations(limit: int = Query(20, ge=1, le=100)) -> list[ExplorationRunSummary]:
    engine = get_engine()
    with Session(engine) as session:
        runs = session.exec(
            select(ExplorationRun).order_by(ExplorationRun.created_at.desc()).limit(limit)
        ).all()
    return [
        ExplorationRunSummary(
            id=r.id, question=r.question, depth=r.depth, status=r.status,
            provider=r.provider, created_at=r.created_at,
        )
        for r in runs
    ]


@router.get("/runs/{run_id}", response_model=ExplorationRunResponse)
async def get_exploration(run_id: int) -> ExplorationRunResponse:
    engine = get_engine()
    with Session(engine) as session:
        run = session.get(ExplorationRun, run_id)
        if not run:
            raise HTTPException(404, detail={"detail": "Run not found", "code": "NOT_FOUND"})
        return ExplorationRunResponse.model_validate(run, from_attributes=True)


@router.delete("/runs/{run_id}")
async def delete_exploration(run_id: int) -> dict:
    engine = get_engine()
    with Session(engine) as session:
        run = session.get(ExplorationRun, run_id)
        if not run:
            raise HTTPException(404, detail={"detail": "Run not found", "code": "NOT_FOUND"})
        if run.status in ("running", "pending"):
            raise HTTPException(409, detail={"detail": "任务运行中，暂不能删除", "code": "EXPLORATION_RUNNING"})
        session.delete(run)
        session.commit()
    return {"message": "已删除"}


@router.get("/runs/{run_id}/stream")
async def stream_exploration(run_id: int) -> StreamingResponse:
    engine = get_engine()
    with Session(engine) as session:
        run = session.get(ExplorationRun, run_id)
        if not run:
            raise HTTPException(404, detail={"detail": "Run not found", "code": "NOT_FOUND"})
        run_status = run.status
        run_result = run.result
        run_error = run.error_message
        run_stage = run.current_stage

    async def gen():
        # 1. 历史已结束的 run：回放产物事件后关闭
        if run_status == "done":
            if run_result:
                yield format_sse(StreamEvent(event="metrics_ready", data=_replay_payload(run_result)))
                if run_result.get("synthesis"):
                    yield format_sse(StreamEvent(event="synthesis_ready", data=run_result["synthesis"]))
            yield format_sse(StreamEvent(event="done", data={"run_id": run_id}))
            return
        if run_status == "failed":
            yield format_sse(StreamEvent(event="error", data={
                "message": run_error or "Unknown error", "stage": run_stage or "",
            }))
            return

        if not has_exploration_bus(run_id):
            yield format_sse(StreamEvent(event="error", data={
                "message": "探索任务未在当前进程中运行，请重新创建任务",
                "stage": run_stage or "",
            }))
            return

        # 2. 进行中：订阅事件总线 + 15s 心跳保活
        bus = get_exploration_bus(run_id)
        stream_iter = bus.subscribe()
        async for chunk in _stream_bus_with_heartbeat(stream_iter, timeout=15.0):
            yield chunk

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
