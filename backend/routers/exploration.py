"""
Exploration 路由 — 问句驱动的领域探索（GOAL.md 核心命题）

创建/查询探索任务、SSE 进度流。产物结构见 models/exploration.py。
"""

import asyncio
import logging
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse, Response
from sqlmodel import Session, select

from ..services.database import get_engine
from ..models.exploration import ExplorationRun, ExplorationMessage
from ..services import exploration_chat
from ..services.research_sources import SourceError, seed_options
from ..models.schemas import (
    StreamEvent,
    ExplorationRunCreate, ExplorationRunResponse, ExplorationRunSummary,
    ExplorationMessageCreate, ExplorationMessageResponse,
)
from ..utils.streaming import format_sse
from ..services.deep_research.explorer import (
    get_exploration_bus, has_exploration_bus, run_exploration, rebuild_survey_report,
)
from ..routers.research import _stream_bus_with_heartbeat

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/exploration", tags=["exploration"])
_tasks: set[asyncio.Task] = set()


def _start(coro):
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


@router.get("/runs/{run_id}/report")
def export_report(run_id: int, format: str = Query("html", pattern="^(html|markdown|json)$")):
    from ..services.deep_research.survey_report import render_html
    with Session(get_engine()) as session:
        run = session.get(ExplorationRun, run_id)
        report = (run.result or {}).get("survey_report") if run else None
    if not report:
        raise HTTPException(404, detail={"detail": "报告尚未生成", "code": "REPORT_NOT_FOUND"})
    if format == "json":
        return report
    content = render_html(report) if format == "html" else report["markdown"]
    suffix, mime = ("html", "text/html") if format == "html" else ("md", "text/markdown")
    return Response(content, media_type=mime, headers={
        "Content-Disposition": f'attachment; filename="research-{run_id}.{suffix}"',
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'",
    })


@router.post("/runs/{run_id}/report", response_model=ExplorationRunResponse)
async def regenerate_report(run_id: int):
    with Session(get_engine()) as session:
        run = session.get(ExplorationRun, run_id)
        if not run:
            raise HTTPException(404, detail={"detail": "研究记录不存在", "code": "NOT_FOUND"})
        active = session.exec(select(ExplorationRun).where(ExplorationRun.status.in_(["pending", "running"]))).first()
        chat = session.exec(select(ExplorationMessage).where(
            ExplorationMessage.run_id == run_id, ExplorationMessage.status.in_(["pending", "running"]),
        )).first()
        if active or chat:
            raise HTTPException(409, detail={"detail": "请等待当前研究或追问完成", "code": "RESEARCH_RUNNING"})
        if not (run.result or {}).get("papers"):
            raise HTTPException(400, detail={"detail": "暂无可用于生成报告的材料", "code": "NO_MATERIALS"})
        run.status, run.current_stage, run.error_message = "pending", "report", None
        session.add(run)
        session.commit()
        session.refresh(run)
        response = ExplorationRunResponse.model_validate(run, from_attributes=True)
    get_exploration_bus(run_id)
    _start(rebuild_survey_report(run_id))
    return response


@router.get("/runs/{run_id}/messages", response_model=list[ExplorationMessageResponse])
def list_messages(run_id: int):
    try:
        return exploration_chat.conversation(run_id)
    except exploration_chat.ExplorationChatError as exc:
        raise HTTPException(exc.status, detail={"detail": exc.detail, "code": exc.code}) from exc


@router.post("/runs/{run_id}/messages", response_model=ExplorationMessageResponse)
async def post_message(run_id: int, req: ExplorationMessageCreate):
    try:
        message = exploration_chat.create_message(run_id, req.question.strip(), req.search_more, req.selected_keys)
    except exploration_chat.ExplorationChatError as exc:
        raise HTTPException(exc.status, detail={"detail": exc.detail, "code": exc.code}) from exc
    exploration_chat.start_message(message.id)
    return message


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

        if len(req.question.strip()) < 2:
            raise HTTPException(422, detail={"detail": "研究问题至少两个字符", "code": "INVALID_QUESTION"})
        try:
            options = seed_options(session, req.workspace_id, req.seed_paper_ids, req.search_online)
        except SourceError as exc:
            raise HTTPException(exc.status, detail={"detail": str(exc), "code": "INVALID_SOURCES"}) from exc
        run = ExplorationRun(question=req.question.strip(), depth=req.depth, status="pending", options=options)
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id
        if run_id is None:
            raise HTTPException(500, detail={"detail": "探索任务创建失败", "code": "INTERNAL_ERROR"})

    # 提前建总线：前端在后台任务获得调度前订阅也不至于拿到「无 bus」
    get_exploration_bus(run_id)
    _start(run_exploration(run_id))

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
            provider=r.provider, created_at=r.created_at, options=r.options or {},
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
        messages = session.exec(select(ExplorationMessage).where(ExplorationMessage.run_id == run_id)).all()
        if any(m.status in ("running", "pending") for m in messages):
            raise HTTPException(409, detail={"detail": "追问运行中，暂不能删除", "code": "CHAT_RUNNING"})
        for message in messages:
            session.delete(message)
        session.flush()
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
