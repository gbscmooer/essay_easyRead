"""
Exploration 路由 — 问句驱动的领域探索（GOAL.md 核心命题）

创建/查询探索任务、SSE 进度流。产物结构见 models/exploration.py。

academic_survey_v2（S0 冻结接口边界，见 docs/ACADEMIC_SURVEY_AGENT_PLAN_2026-09-08.md §7.2）：
- 现有接口默认保留 v1 行为；只有显式 workflow_version=academic_survey_v2 使用新链路。
- 新端点仅 GET/POST；v2 执行器接入前创建返回 WORKFLOW_NOT_IMPLEMENTED（S3 替换该门禁）。

断点续传（legacy v1）：pause 在安全点协作式停止并保留 checkpoint；resume 从
checkpoint/result 快照恢复，跳过已完成阶段与已提取篇目；v2 worker 接入前拒绝 resume。
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, Response
from sqlmodel import Session, select

from ..services.database import get_engine
from ..models.exploration import ExplorationRun, ExplorationMessage
from ..models.research_core import (
    RUN_ACTIVE_STATES_V2,
    RUN_RETRYABLE_STATES_V2,
    RUN_STATES_V2,
    TASK_STATES,
    WORKFLOW_ACADEMIC_SURVEY_V2,
    WORKFLOW_LEGACY_V1,
    ResearchAsset,
    ResearchEvidence,
    ResearchReportRevision,
    ResearchTask,
)
from ..services import exploration_chat, research_event_log as event_log
from ..services.research_sources import SourceError, seed_options
from ..models.schemas import (
    StreamEvent,
    ExplorationRunCreate, ExplorationRunResponse, ExplorationRunSummary,
    ExplorationMessageCreate, ExplorationMessageResponse,
    PreflightResponse, SourceHealth,
    ProtocolSummaryResponse, ProtocolChangeEntry, RunProtocolResponse,
    ResearchTaskItem, ResearchTaskListResponse,
    CoverageSubquestionEntry, CoverageGapEntry, RunCoverageResponse,
    BudgetResourceEntry, RunBudgetResponse,
    RunControlRequest, RunControlResponse,
    RunRetryRequest, RunRetryResponse,
    ReportRevisionEntry, ReportRevisionListResponse,
    EvidenceSpanEntry, EvidenceDetailResponse, EvidenceIndexItem, EvidenceIndexResponse,
)
from ..utils.streaming import format_sse
from ..services.deep_research.explorer import (
    get_exploration_bus, has_exploration_bus, run_exploration, rebuild_survey_report,
    request_pause, cancel_pause_request, drop_exploration_bus, _checkpoint_summary,
)
from ..routers.research import _stream_bus_with_heartbeat

logger = logging.getLogger(__name__)

_TASK_STATE_PATTERN = "^(" + "|".join(TASK_STATES) + ")$"
_RUN_STATE_PATTERN = "^(" + "|".join(RUN_STATES_V2) + ")$"

router = APIRouter(prefix="/api/exploration", tags=["exploration"])
_tasks: set[asyncio.Task] = set()


def _start(coro):
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


@router.get("/preflight", response_model=PreflightResponse)
async def preflight_sources() -> PreflightResponse:
    """数据源预检：用带权限的真实最小请求探测各源（调查开始前可见 401/403/断网）。"""
    from datetime import datetime, timezone

    from ..services.deep_research.preflight import preflight_research_sources

    healths = await preflight_research_sources()
    return PreflightResponse(
        checked_at=datetime.now(timezone.utc),
        sources=[SourceHealth(**h) for h in healths],
    )


def _render_report_response(report: dict, run_id: int, revision_label: str, format: str,
                            revision_id: str):
    """报告导出的共用渲染（v1 当前报告与 v2 revision 报共享；只读不落库）。

    响应附 X-Report-Revision 头标明实际服务的版本（v1 为 "legacy"）。
    JSON 内容为解析等价序列化（v1 由 dict 直返改为显式序列化以便带头）。
    """
    headers = {
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'",
        "X-Report-Revision": revision_id,
    }
    if format == "json":
        return Response(content=json.dumps(report, ensure_ascii=False), media_type="application/json",
                        headers=headers)
    from ..services.deep_research.survey_report import render_html

    content = render_html(report) if format == "html" else report.get("markdown", "")
    suffix, mime = ("html", "text/html") if format == "html" else ("md", "text/markdown")
    headers["Content-Disposition"] = f'attachment; filename="research-{run_id}-{revision_label}.{suffix}"'
    return Response(content, media_type=mime, headers=headers)


# 报告资产可读状态：不可变历史（superseded）仍可读；orphaned 属未登记临时资产不可读
_REPORT_READABLE_ASSET_STATUSES = ("active", "superseded")


def _load_exposable_report(session: Session, run_id: int, asset_id: str) -> dict:
    """按 v2 契约加载可对外导出的报告资产内容（F4：隔离资产拒绝导出）。

    - 资产缺失 / 不属于该 run / kind 不是 report / payload 为空 → 404
    - quarantined（身份冲突或污染隔离）→ 403，不泄漏正文
    - orphaned（事务未登记）→ 404；superseded 历史版本 → 正常可读
    """
    asset = session.exec(
        select(ResearchAsset).where(ResearchAsset.asset_id == asset_id)
    ).first()
    if asset is None or asset.run_id != run_id or asset.content_kind != "report":
        raise HTTPException(404, detail={"detail": "报告内容缺失", "code": "REPORT_NOT_FOUND"})
    if asset.status == "quarantined":
        raise HTTPException(
            403,
            detail={"detail": "报告资产已被隔离，不对外暴露内容", "code": "REPORT_NOT_EXPOSED"},
        )
    if asset.status not in _REPORT_READABLE_ASSET_STATUSES or not asset.inline_payload:
        raise HTTPException(404, detail={"detail": "报告内容缺失", "code": "REPORT_NOT_FOUND"})
    return asset.inline_payload


def _selected_revision(session: Session, run_id: int) -> Optional[ResearchReportRevision]:
    return session.exec(
        select(ResearchReportRevision)
        .where(ResearchReportRevision.run_id == run_id, ResearchReportRevision.is_selected == True)  # noqa: E712
        .order_by(ResearchReportRevision.id.desc())  # type: ignore[arg-type]
    ).first()


@router.get("/runs/{run_id}/report")
def export_report(run_id: int, format: str = Query("html", pattern="^(html|markdown|json)$")):
    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        if (run.workflow_version or WORKFLOW_LEGACY_V1) == WORKFLOW_ACADEMIC_SURVEY_V2:
            # F3：v2 默认入口解析当前选定 revision；不用 result 历史快照静默顶替
            revision = _selected_revision(session, run_id)
            if revision is None:
                raise HTTPException(
                    404, detail={"detail": "尚无选定报告版本", "code": "REPORT_NOT_FOUND"})
            report = dict(_load_exposable_report(session, run_id, revision.report_asset_id))
            report["revision_id"] = revision.revision_id
            return _render_report_response(report, run_id, revision.revision_id[:8], format,
                                           revision_id=revision.revision_id)
        report = (run.result or {}).get("survey_report")
        if not report:
            raise HTTPException(404, detail={"detail": "报告尚未生成", "code": "REPORT_NOT_FOUND"})
        return _render_report_response(report, run_id, "legacy", format, revision_id="legacy")


@router.post("/runs/{run_id}/report", response_model=ExplorationRunResponse)
async def regenerate_report(run_id: int):
    with Session(get_engine()) as session:
        run = session.get(ExplorationRun, run_id)
        if not run:
            raise HTTPException(404, detail={"detail": "研究记录不存在", "code": "NOT_FOUND"})
        if run.workflow_version == WORKFLOW_ACADEMIC_SURVEY_V2:
            # v2 报告重建=创建新 revision（计划 §7.2），由 S6 接入；旧路径不得覆盖 v2 产物
            raise HTTPException(
                400,
                detail={"detail": "academic_survey_v2 报告以不可变 revision 重建，待 S6 接入",
                        "code": "WORKFLOW_NOT_IMPLEMENTED"},
            )
        active = session.exec(select(ExplorationRun).where(ExplorationRun.status.in_(["pending", "running", "paused"]))).first()
        chat = session.exec(select(ExplorationMessage).where(
            ExplorationMessage.run_id == run_id, ExplorationMessage.status.in_(["pending", "running"]),
        )).first()
        if active or chat:
            raise HTTPException(409, detail={"detail": "请等待当前研究或追问完成", "code": "RESEARCH_RUNNING"})
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


def _v2_options_snapshot(req: ExplorationRunCreate, options: dict) -> dict:
    """v2 输入随 options["workflow"] 快照落盘（S0 冻结；沿用 options 保存输入快照的约定）。"""
    if req.workflow_version != WORKFLOW_ACADEMIC_SURVEY_V2:
        return options
    snapshot = dict(options)
    snapshot["workflow"] = {
        "version": req.workflow_version,
        "protocol_options": req.protocol_options or {},
        "budget": req.budget or {},
        "content_cutoff": req.content_cutoff.isoformat() if req.content_cutoff else None,
        "policy_id": req.policy_id,
    }
    return snapshot


@router.post("/runs", response_model=ExplorationRunResponse)
async def create_exploration(req: ExplorationRunCreate) -> ExplorationRunResponse:
    engine = get_engine()
    with Session(engine) as session:
        # 同一时刻只允许一个探索任务（检索配额与 LLM 成本控制）；暂停的研究同样占位
        existing = session.exec(
            select(ExplorationRun)
            .where(ExplorationRun.status.in_(["running", "pending", "paused"]))
        ).first()
        if existing:
            hint = "已有暂停的研究，请先继续或删除该研究" if existing.status == "paused" else "已有运行中的探索任务"
            raise HTTPException(409, detail={"detail": hint, "code": "EXPLORATION_RUNNING"})

        if len(req.question.strip()) < 2:
            raise HTTPException(422, detail={"detail": "研究问题至少两个字符", "code": "INVALID_QUESTION"})
        try:
            options = seed_options(session, req.workspace_id, req.seed_paper_ids, req.search_online,
                                   exclude_dois=req.exclude_dois, exclude_arxiv_ids=req.exclude_arxiv_ids,
                                   exclude_titles=req.exclude_titles)
        except SourceError as exc:
            raise HTTPException(exc.status, detail={"detail": str(exc), "code": "INVALID_SOURCES"}) from exc
        if req.workflow_version == WORKFLOW_ACADEMIC_SURVEY_V2:
            # S0 冻结的 v2 门禁：输入校验契约已生效，执行器接入由 S3/S5 替换此分支
            raise HTTPException(
                400,
                detail={"detail": "academic_survey_v2 执行器尚未接入（等待 S3/S5），当前请使用默认工作流",
                        "code": "WORKFLOW_NOT_IMPLEMENTED"},
            )
        run = ExplorationRun(
            question=req.question.strip(), depth=req.depth, status="pending",
            options=_v2_options_snapshot(req, options),
            workflow_version=req.workflow_version or WORKFLOW_LEGACY_V1,
            state_version=1 if req.workflow_version == WORKFLOW_ACADEMIC_SURVEY_V2 else 0,
        )
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
            workflow_version=r.workflow_version or WORKFLOW_LEGACY_V1,
            state_version=r.state_version or 0,
        )
        for r in runs
    ]


def _v2_summary_fields(session: Session, run: ExplorationRun) -> dict:
    """单 run 响应的 v2 摘要（协议摘要 + 当前选定 revision）；v1 返回 None 字段。"""
    fields: dict = {"protocol": None, "current_revision": None}
    if run.workflow_version != WORKFLOW_ACADEMIC_SURVEY_V2 or run.id is None:
        return fields
    if run.protocol_asset_id:
        asset = session.exec(
            select(ResearchAsset).where(ResearchAsset.asset_id == run.protocol_asset_id)
        ).first()
        if asset and asset.inline_payload:
            proto = asset.inline_payload
            fields["protocol"] = ProtocolSummaryResponse(
                protocol_id=proto.get("protocol_id"),
                version=int(proto.get("version") or 1),
                hash=proto.get("hash"),
                question=proto.get("question") or run.question,
                subquestion_count=len(proto.get("subquestions") or []),
            )
    revision = session.exec(
        select(ResearchReportRevision)
        .where(ResearchReportRevision.run_id == run.id, ResearchReportRevision.is_selected == True)  # noqa: E712
        .order_by(ResearchReportRevision.id.desc())  # type: ignore[arg-type]
    ).first()
    if revision:
        fields["current_revision"] = ReportRevisionEntry(
            revision_id=revision.revision_id,
            parent_revision=revision.parent_revision,
            protocol_hash=revision.protocol_hash,
            quality_status=revision.quality_status,
            stop_reason=revision.stop_reason,
            is_selected=revision.is_selected,
            legacy=False,
            created_at=revision.created_at,
        )
    return fields


@router.get("/runs/{run_id}", response_model=ExplorationRunResponse)
async def get_exploration(run_id: int) -> ExplorationRunResponse:
    engine = get_engine()
    with Session(engine) as session:
        run = session.get(ExplorationRun, run_id)
        if not run:
            raise HTTPException(404, detail={"detail": "Run not found", "code": "NOT_FOUND"})
        response = ExplorationRunResponse.model_validate(run, from_attributes=True)
        response.checkpoint_summary = _checkpoint_summary(run.checkpoint)
        for key, value in _v2_summary_fields(session, run).items():
            setattr(response, key, value)
        return response


@router.delete("/runs/{run_id}")
async def delete_exploration(run_id: int) -> dict:
    engine = get_engine()
    with Session(engine) as session:
        run = session.get(ExplorationRun, run_id)
        if not run:
            raise HTTPException(404, detail={"detail": "Run not found", "code": "NOT_FOUND"})
        if run.workflow_version == WORKFLOW_ACADEMIC_SURVEY_V2:
            # v2 删除需级联清理任务/账本/资产与磁盘文件（S3 接入）；先只读保护
            raise HTTPException(
                400,
                detail={"detail": "academic_survey_v2 运行的删除（含资产级联清理）待 S3 接入",
                        "code": "WORKFLOW_NOT_IMPLEMENTED"},
            )
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
    # 已暂停（worker 已退出）的 run 允许删除：清掉残留的暂停旗标与事件总线
    cancel_pause_request(run_id)
    drop_exploration_bus(run_id)
    return {"message": "已删除"}


def _format_persisted_event(row) -> str:
    """v2 持久化事件的 SSE 帧：带 event_id，支持 Last-Event-ID 断点续订。"""
    data = json.dumps(dict(row.payload or {}), ensure_ascii=False)
    return f"id: {row.event_id}\nevent: {row.type}\ndata: {data}\n\n"


@router.get("/runs/{run_id}/stream")
async def stream_exploration(
    run_id: int,
    request: Request,
    after: Optional[str] = Query(None, description="v2 断点续订游标（event_id）；与 Last-Event-ID 等价"),
) -> StreamingResponse:
    engine = get_engine()
    with Session(engine) as session:
        run = session.get(ExplorationRun, run_id)
        if not run:
            raise HTTPException(404, detail={"detail": "Run not found", "code": "NOT_FOUND"})
        run_status = run.status
        run_result = run.result
        run_error = run.error_message
        run_stage = run.current_stage
        run_workflow = run.workflow_version or WORKFLOW_LEGACY_V1

    if run_workflow == WORKFLOW_ACADEMIC_SURVEY_V2:
        # v2：先回放持久化事件（research_events 是唯一状态来源之一），
        # 活动运行再接 live-bus（S3 通过 research_event_log.register_live_bus 注册）。
        cursor = request.headers.get("last-event-id") or after
        try:
            with Session(engine) as session:
                replayed = event_log.replay_events(session, run_id, after_event_id=cursor)
                frames = [_format_persisted_event(row) for row in replayed]
        except ValueError:
            raise HTTPException(422, detail={"detail": "未知的续订游标", "code": "INVALID_CURSOR"})
        live_bus = event_log.get_live_bus(run_id) if run_status in RUN_ACTIVE_STATES_V2 else None

        async def gen_v2():
            for frame in frames:
                yield frame
            yield format_sse(StreamEvent(event="run_status", data={
                "run_id": run_id, "status": run_status,
            }))
            if run_status in RUN_ACTIVE_STATES_V2:
                if live_bus is None:
                    yield format_sse(StreamEvent(event="error", data={
                        "message": "v2 worker 未接入实时事件（等待 S3）", "stage": "stream",
                    }))
                    return
                stream_iter = live_bus.subscribe()
                async for chunk in _stream_bus_with_heartbeat(stream_iter, timeout=15.0):
                    yield chunk

        return StreamingResponse(
            gen_v2(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

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
        if run_status == "paused":
            yield format_sse(StreamEvent(event="paused", data={"run_id": run_id, "stage": run_stage or ""}))
            return

        if not has_exploration_bus(run_id):
            yield format_sse(StreamEvent(event="error", data={
                "message": "研究进程已中断（可能是服务重启或断网），可从断点继续研究",
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


# ============================================================
# academic_survey_v2 端点（S0 冻结；业务写入方为 S1–S6）
# v1 运行走 legacy 投影：protocol/tasks/budget 给 legacy 标记与空集合，
# 控制类操作对 v1 一律 400 UNSUPPORTED_WORKFLOW（不改变 v1 行为）。
# ============================================================


def _get_run_or_404(session: Session, run_id: int) -> ExplorationRun:
    run = session.get(ExplorationRun, run_id)
    if not run:
        raise HTTPException(404, detail={"detail": "研究记录不存在", "code": "NOT_FOUND"})
    return run


def _require_v2(run: ExplorationRun) -> None:
    if (run.workflow_version or WORKFLOW_LEGACY_V1) != WORKFLOW_ACADEMIC_SURVEY_V2:
        raise HTTPException(
            400,
            detail={"detail": "此操作仅支持 academic_survey_v2 工作流", "code": "UNSUPPORTED_WORKFLOW"},
        )


@router.get("/runs/{run_id}/protocol", response_model=RunProtocolResponse)
def get_run_protocol(run_id: int) -> RunProtocolResponse:
    """冻结协议及变更记录；不返回隐藏金标。v1 返回 legacy 投影（输入快照，无子问题）。"""
    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        workflow = run.workflow_version or WORKFLOW_LEGACY_V1
        if workflow != WORKFLOW_ACADEMIC_SURVEY_V2:
            return RunProtocolResponse(
                run_id=run_id, workflow_version=workflow, legacy=True,
                protocol=None, legacy_options=run.options or {},
                changes=[ProtocolChangeEntry(
                    version=0, change_reason="legacy_import", created_at=run.created_at,
                )],
            )
        assets = session.exec(
            select(ResearchAsset)
            .where(ResearchAsset.run_id == run_id, ResearchAsset.content_kind == "protocol")
            .order_by(ResearchAsset.id)  # type: ignore[arg-type]
        ).all()
        current = None
        if run.protocol_asset_id:
            current = next((a for a in assets if a.asset_id == run.protocol_asset_id), None)
        if current is None and assets:
            current = assets[-1]
        changes = [
            ProtocolChangeEntry(
                version=int((a.inline_payload or {}).get("version") or idx + 1),
                protocol_id=(a.inline_payload or {}).get("protocol_id"),
                change_reason=(a.inline_payload or {}).get("change_reason"),
                created_at=a.created_at,
            )
            for idx, a in enumerate(assets)
        ]
        return RunProtocolResponse(
            run_id=run_id, workflow_version=workflow, legacy=False,
            protocol=current.inline_payload if current else None,
            legacy_options=None, changes=changes,
        )


@router.get("/runs/{run_id}/tasks", response_model=ResearchTaskListResponse)
def list_run_tasks(
    run_id: int,
    cursor: Optional[str] = Query(None, description="分页游标（上一页最后一行的内部序号字符串）"),
    state: Optional[str] = Query(None, pattern=_TASK_STATE_PATTERN),
    kind: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
) -> ResearchTaskListResponse:
    """分页任务账本；v1 运行固定空列表（legacy=true）。"""
    from sqlalchemy import func

    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        workflow = run.workflow_version or WORKFLOW_LEGACY_V1
        if workflow != WORKFLOW_ACADEMIC_SURVEY_V2:
            return ResearchTaskListResponse(
                run_id=run_id, workflow_version=workflow, legacy=True,
                items=[], next_cursor=None, total=0,
            )
        after_seq = 0
        if cursor is not None:
            try:
                after_seq = int(cursor)
            except ValueError:
                raise HTTPException(422, detail={"detail": "无效的任务分页游标", "code": "INVALID_CURSOR"})
        filters = [ResearchTask.run_id == run_id]
        if state:
            filters.append(ResearchTask.state == state)
        if kind:
            filters.append(ResearchTask.kind == kind)
        total = session.exec(select(func.count()).select_from(ResearchTask).where(*filters)).one()
        statement = (
            select(ResearchTask)
            .where(*filters, ResearchTask.id > after_seq)
            .order_by(ResearchTask.id)  # type: ignore[arg-type]
            .limit(limit + 1)
        )
        rows = session.exec(statement).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = str(rows[-1].id) if has_more and rows and rows[-1].id is not None else None
        items = [
            ResearchTaskItem(
                task_id=row.task_id, run_id=row.run_id, kind=row.kind,
                subquestion_id=row.subquestion_id, state=row.state,
                dependencies=row.dependencies or [], idempotency_key=row.idempotency_key,
                error_code=row.error_code, created_at=row.created_at, updated_at=row.updated_at,
            )
            for row in rows
        ]
        return ResearchTaskListResponse(
            run_id=run_id, workflow_version=workflow, legacy=False,
            items=items, next_cursor=next_cursor, total=total,
        )


def _latest_asset(session: Session, run_id: int, content_kind: str) -> Optional[ResearchAsset]:
    return session.exec(
        select(ResearchAsset)
        .where(ResearchAsset.run_id == run_id, ResearchAsset.content_kind == content_kind,
               ResearchAsset.status == "active")
        .order_by(ResearchAsset.id.desc())  # type: ignore[arg-type]
    ).first()


@router.get("/runs/{run_id}/coverage", response_model=RunCoverageResponse)
def get_run_coverage(run_id: int) -> RunCoverageResponse:
    """按子问题覆盖、缺口与最近补充动作。v1 投影 result.survey_report.coverage 原样数据。"""
    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        workflow = run.workflow_version or WORKFLOW_LEGACY_V1
        if workflow != WORKFLOW_ACADEMIC_SURVEY_V2:
            legacy_summary = ((run.result or {}).get("survey_report") or {}).get("coverage")
            return RunCoverageResponse(
                run_id=run_id, workflow_version=workflow, legacy=True,
                updated_at=run.updated_at, subquestions=[], gaps=[], recent_actions=[],
                legacy_summary=legacy_summary,
            )
        snapshot = _latest_asset(session, run_id, "coverage_snapshot")
        payload = (snapshot.inline_payload if snapshot else None) or {}
        if payload.get("subquestions") is not None:
            subquestions = [CoverageSubquestionEntry.model_validate(d) for d in payload["subquestions"]]
        else:
            protocol_assets = session.exec(
                select(ResearchAsset)
                .where(ResearchAsset.run_id == run_id, ResearchAsset.content_kind == "protocol")
                .order_by(ResearchAsset.id)  # type: ignore[arg-type]
            ).all()
            current = next((a for a in protocol_assets if a.asset_id == run.protocol_asset_id),
                           protocol_assets[-1] if protocol_assets else None)
            proto = (current.inline_payload if current else None) or {}
            subquestions = [
                CoverageSubquestionEntry(subquestion_id=s.get("subquestion_id", f"sq{idx + 1}"),
                                         question=s.get("question", ""), status=s.get("status", "open"))
                for idx, s in enumerate(proto.get("subquestions") or [])
            ]
        gaps = [CoverageGapEntry.model_validate(d) for d in payload.get("gaps") or []]
        recent = [
            {
                "task_id": row.task_id,
                "type": row.type,
                "payload": row.payload or {},
                "at": row.created_at.isoformat(),
            }
            for row in event_log.latest_events(session, run_id, "task_state_changed", limit=5)
        ]
        return RunCoverageResponse(
            run_id=run_id, workflow_version=workflow, legacy=False,
            updated_at=snapshot.created_at if snapshot else None,
            subquestions=subquestions, gaps=gaps, recent_actions=recent,
            legacy_summary=None,
        )


@router.get("/runs/{run_id}/budget", response_model=RunBudgetResponse)
def get_run_budget(run_id: int) -> RunBudgetResponse:
    """各资源 limit/used/reserved 与停止原因；不返回凭证。聚合口径见 research_core。"""
    from ..models.research_core import ResearchEvent as _Event

    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        workflow = run.workflow_version or WORKFLOW_LEGACY_V1
        if workflow != WORKFLOW_ACADEMIC_SURVEY_V2:
            return RunBudgetResponse(
                run_id=run_id, workflow_version=workflow, legacy=True,
                resources=[], stop_reason=None,
            )
        protocol = None
        if run.protocol_asset_id:
            asset = session.exec(
                select(ResearchAsset).where(ResearchAsset.asset_id == run.protocol_asset_id)
            ).first()
            protocol = (asset.inline_payload if asset else None) or {}
        limits = {k: v for k, v in (protocol.get("budget") or {}).items() if isinstance(v, int)}
        resources = [BudgetResourceEntry.model_validate(entry)
                     for entry in event_log.budget_snapshot(session, run_id, limits)]
        revision = session.exec(
            select(ResearchReportRevision)
            .where(ResearchReportRevision.run_id == run_id)
            .order_by(ResearchReportRevision.id.desc())  # type: ignore[arg-type]
        ).first()
        stop_reason = revision.stop_reason if revision else None
        if stop_reason is None:
            stop_events = session.exec(
                select(_Event)
                .where(_Event.run_id == run_id, _Event.type == "budget_stop")
                .order_by(_Event.id.desc())  # type: ignore[arg-type]
            ).first()
            stop_reason = (stop_events.payload or {}).get("reason") if stop_events else None
        return RunBudgetResponse(
            run_id=run_id, workflow_version=workflow, legacy=False,
            resources=resources, stop_reason=stop_reason,
        )


def _control_run(run_id: int, req: RunControlRequest, target: str, allowed_from: tuple[str, ...],
                 event_type: str, detail: str) -> RunControlResponse:
    with Session(get_engine()) as session:
        try:
            run, replay = event_log.cas_run_state(
                session, run_id=run_id, new_status=target,
                expected_state=req.expected_state,
                expected_state_version=req.expected_state_version,
                allowed_from=allowed_from, event_type=event_type,
            )
        except event_log.RunStateError as exc:
            raise HTTPException(exc.status_code, detail={"detail": exc.detail, "code": exc.code})
        return RunControlResponse(
            run_id=run_id, status=run.status, state_version=run.state_version,
            idempotent_replay=replay, detail=detail,
        )


@router.post("/runs/{run_id}/pause", response_model=RunControlResponse)
def pause_run(run_id: int, req: RunControlRequest) -> RunControlResponse:
    """safe-point 暂停请求：v2 走 CAS 契约；v1 置协作旗标，worker 在安全点
    （阶段/召回轮/agent 迭代/报告逐篇提取边界）观察后停止并持久化断点。
    本进程无活跃 worker 的僵尸 run（如服务重启遗留）直接落库置 paused。"""
    from sqlalchemy import update as sa_update

    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        if (run.workflow_version or WORKFLOW_LEGACY_V1) == WORKFLOW_ACADEMIC_SURVEY_V2:
            try:
                _, replay = event_log.cas_run_state(
                    session, run_id=run_id, new_status="paused",
                    expected_state=req.expected_state,
                    expected_state_version=req.expected_state_version,
                    allowed_from=RUN_ACTIVE_STATES_V2, event_type="run_paused",
                )
                return RunControlResponse(
                    run_id=run_id, status="paused", state_version=run.state_version,
                    idempotent_replay=replay, detail="已请求在安全点暂停",
                )
            except event_log.RunStateError as exc:
                raise HTTPException(exc.status_code, detail={"detail": exc.detail, "code": exc.code})
        if run.status == "paused":
            return RunControlResponse(
                run_id=run_id, status="paused", state_version=run.state_version,
                idempotent_replay=True, detail="已在安全点暂停",
            )
        if run.status not in ("running", "pending"):
            raise HTTPException(
                409, detail={"detail": f"当前状态 {run.status} 不可暂停", "code": "INVALID_STATE"})
        if request_pause(run_id):
            return RunControlResponse(
                run_id=run_id, status=run.status, state_version=run.state_version,
                detail="已请求暂停，将在最近的安全点（阶段/轮次/篇目边界）停止并保留断点",
            )
        stmt = (
            sa_update(ExplorationRun)
            .where(ExplorationRun.id == run_id, ExplorationRun.status == run.status)
            .values(status="paused", updated_at=datetime.now(timezone.utc))
        )
        if session.exec(stmt).rowcount != 1:  # type: ignore[attr-defined]
            raise HTTPException(409, detail={"detail": "状态已变化，请刷新后重试", "code": "STATE_CONFLICT"})
        session.commit()
        return RunControlResponse(
            run_id=run_id, status="paused", state_version=run.state_version,
            detail="研究进程已不在运行（可能服务重启过），已直接标记为暂停，可从断点继续",
        )


@router.post("/runs/{run_id}/resume", response_model=RunControlResponse)
async def resume_run(run_id: int, req: RunControlRequest) -> RunControlResponse:
    """从断点继续研究（v2 走 CAS 契约；v1 重启 worker 并复用 checkpoint/result 快照）。

    v1 允许从 failed/paused 继续；状态是 running/pending 但本进程无活跃 worker 的
    僵尸 run 同样可续（服务重启或断网遗留）。同一时刻仍只允许一个探索任务。
    """
    engine = get_engine()
    with Session(engine) as session:
        run = _get_run_or_404(session, run_id)
        if (run.workflow_version or WORKFLOW_LEGACY_V1) == WORKFLOW_ACADEMIC_SURVEY_V2:
            raise HTTPException(
                status_code=400,
                detail={
                    "detail": "academic_survey_v2 执行器尚未接入，不能恢复运行",
                    "code": "WORKFLOW_NOT_IMPLEMENTED",
                },
            )
        else:
            has_live_worker = has_exploration_bus(run_id)
            if run.status in ("running", "pending") and has_live_worker:
                raise HTTPException(
                    409, detail={"detail": "研究正在运行中，无需继续", "code": "INVALID_STATE"})
            if run.status not in ("failed", "paused", "running", "pending"):
                raise HTTPException(
                    409, detail={"detail": f"已完成的研究无需继续（当前状态 {run.status}）",
                                 "code": "INVALID_STATE"})
            other = session.exec(
                select(ExplorationRun).where(
                    ExplorationRun.id != run_id,
                    ExplorationRun.status.in_(["running", "pending"]),
                )
            ).first()
            if other:
                raise HTTPException(409, detail={"detail": "已有运行中的探索任务", "code": "EXPLORATION_RUNNING"})
            from sqlalchemy import update as sa_update

            stmt = (
                sa_update(ExplorationRun)
                .where(ExplorationRun.id == run_id, ExplorationRun.status == run.status)
                .values(status="running", error_message=None,
                        updated_at=datetime.now(timezone.utc))
            )
            if session.exec(stmt).rowcount != 1:  # type: ignore[attr-defined]
                raise HTTPException(409, detail={"detail": "状态已变化，请刷新后重试", "code": "STATE_CONFLICT"})
            session.commit()
            detail = "已从断点继续研究"
            state_version = run.state_version

    get_exploration_bus(run_id)
    _start(run_exploration(run_id, resume=True))
    return RunControlResponse(
        run_id=run_id, status="running", state_version=state_version, detail=detail,
    )


@router.post("/runs/{run_id}/cancel", response_model=RunControlResponse)
def cancel_run(run_id: int, req: RunControlRequest) -> RunControlResponse:
    """取消请求；保留既有结果（已完成任务产物不删除）。"""
    return _control_run(run_id, req, "cancelled", RUN_ACTIVE_STATES_V2, "run_cancelled", "已请求取消")


@router.post("/runs/{run_id}/retry", response_model=RunRetryResponse)
def retry_run(run_id: int, req: RunRetryRequest) -> RunRetryResponse:
    """只重做失败/失效任务：重新入队 pending 并记录 run_retry 事件；不重做已成功任务。

    幂等：相同 idempotency_key 的重复请求返回原结果。下游失效重算（revision 失效）
    由 S3/S6 根据依赖图处理，本端点不隐式扩大范围。
    """
    from datetime import datetime, timezone

    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        _require_v2(run)
        if req.idempotency_key:
            prior = event_log.find_retry_event(session, run_id, req.idempotency_key)
            if prior is not None:
                payload = prior.payload or {}
                return RunRetryResponse(
                    run_id=run_id, status=run.status, state_version=run.state_version,
                    retried_tasks=int(payload.get("retried_tasks") or 0), idempotent_replay=True,
                )
        if run.status not in RUN_RETRYABLE_STATES_V2:
            raise HTTPException(
                409,
                detail={"detail": f"当前状态 {run.status} 不允许重试（允许：{'/'.join(RUN_RETRYABLE_STATES_V2)}）",
                        "code": "INVALID_STATE"},
            )
        retry_states = ("failed",) if req.failed_only else ("failed", "skipped", "cancelled")
        if req.task_ids:
            tasks = session.exec(
                select(ResearchTask).where(
                    ResearchTask.run_id == run_id, ResearchTask.task_id.in_(req.task_ids)
                )
            ).all()
            known = {t.task_id for t in tasks}
            if set(req.task_ids) - known:
                raise HTTPException(404, detail={"detail": "存在未知的任务 ID", "code": "TASK_NOT_FOUND"})
            targets = [t for t in tasks if t.state in retry_states]
        else:
            targets = session.exec(
                select(ResearchTask).where(
                    ResearchTask.run_id == run_id, ResearchTask.state.in_(retry_states)
                )
            ).all()
        now = datetime.now(timezone.utc)
        # 与 cas_run_state 相同的原子守卫：并发状态变更时整体回滚，不重排任务。
        # 注意 ORM-enabled UPDATE 会把新值同步回内存实例，版本推进一律用本地
        # 捕获的 previous_version 计算，不读 run.state_version。
        previous_version = run.state_version
        applied = event_log.guarded_transition(
            session, run_id=run_id, from_status=run.status,
            from_version=previous_version, new_status="pending",
        )
        if not applied:
            session.rollback()
            raise HTTPException(
                409,
                detail={"detail": "并发状态变更冲突，请以当前状态重试", "code": "STATE_CONFLICT"},
            )
        run.status, run.state_version = "pending", previous_version + 1
        run.updated_at = now
        session.add(run)
        for task in targets:
            task.state = "pending"
            task.lease_owner = None
            task.lease_expires_at = None
            task.updated_at = now
            session.add(task)
            event_log.append_event(
                session, run_id=run_id, task_id=task.task_id, type="task_state_changed",
                payload={"state": "pending", "reason": "run_retry"},
            )
        event_log.append_event(
            session, run_id=run_id, type="run_retry",
            payload={
                "retried_tasks": len(targets),
                "task_ids": [t.task_id for t in targets],
                "idempotency_key": req.idempotency_key,
                "state_version": previous_version + 1,
            },
        )
        session.commit()
        session.refresh(run)
        return RunRetryResponse(
            run_id=run_id, status=run.status, state_version=run.state_version,
            retried_tasks=len(targets), idempotent_replay=False,
        )


@router.get("/runs/{run_id}/revisions", response_model=ReportRevisionListResponse)
def list_run_revisions(
    run_id: int,
    cursor: Optional[str] = Query(None, description="倒序分页游标（上一页最后一行的内部序号字符串）"),
    limit: int = Query(20, ge=1, le=100),
) -> ReportRevisionListResponse:
    """不可变报告版本列表；v1 有报告时投影单个 revision_id="legacy" 的伪版本。"""
    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        workflow = run.workflow_version or WORKFLOW_LEGACY_V1
        if workflow != WORKFLOW_ACADEMIC_SURVEY_V2:
            before_seq = None
            if cursor is not None:
                try:
                    before_seq = int(cursor)
                except ValueError:
                    raise HTTPException(422, detail={"detail": "无效的版本分页游标", "code": "INVALID_CURSOR"})
            statement = select(ResearchReportRevision).where(
                ResearchReportRevision.run_id == run_id,
                ResearchReportRevision.workflow_version == WORKFLOW_LEGACY_V1,
            )
            if before_seq is not None:
                statement = statement.where(ResearchReportRevision.id < before_seq)
            rows = session.exec(statement.order_by(ResearchReportRevision.id.desc()).limit(limit + 1)).all()  # type: ignore[arg-type]
            has_more = len(rows) > limit
            rows = rows[:limit]
            items = [ReportRevisionEntry(
                revision_id=row.revision_id, parent_revision=row.parent_revision,
                protocol_hash=row.protocol_hash, quality_status=row.quality_status,
                stop_reason=row.stop_reason, is_selected=row.is_selected,
                legacy=True, created_at=row.created_at,
            ) for row in rows]
            if not items and (run.result or {}).get("survey_report"):
                items = [ReportRevisionEntry(
                    revision_id="legacy", quality_status="unchecked",
                    is_selected=True, legacy=True, created_at=run.updated_at,
                )]
            return ReportRevisionListResponse(
                run_id=run_id, workflow_version=workflow, legacy=True,
                items=items,
                next_cursor=str(rows[-1].id) if has_more and rows and rows[-1].id is not None else None,
            )
        before_seq = None
        if cursor is not None:
            try:
                before_seq = int(cursor)
            except ValueError:
                raise HTTPException(422, detail={"detail": "无效的版本分页游标", "code": "INVALID_CURSOR"})
        statement = select(ResearchReportRevision).where(ResearchReportRevision.run_id == run_id)
        if before_seq is not None:
            statement = statement.where(ResearchReportRevision.id < before_seq)
        rows = session.exec(
            statement.order_by(ResearchReportRevision.id.desc()).limit(limit + 1)  # type: ignore[arg-type]
        ).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = str(rows[-1].id) if has_more and rows and rows[-1].id is not None else None
        items = [
            ReportRevisionEntry(
                revision_id=row.revision_id, parent_revision=row.parent_revision,
                protocol_hash=row.protocol_hash, quality_status=row.quality_status,
                stop_reason=row.stop_reason, is_selected=row.is_selected,
                legacy=False, created_at=row.created_at,
            )
            for row in rows
        ]
        return ReportRevisionListResponse(
            run_id=run_id, workflow_version=workflow, legacy=False,
            items=items, next_cursor=next_cursor,
        )


@router.get("/runs/{run_id}/revisions/{revision_id}/report")
def get_revision_report(
    run_id: int,
    revision_id: str,
    format: str = Query("html", pattern="^(html|markdown|json)$"),
):
    """指定版本报告；v1 的 "legacy" 伪版本返回当前报告（与旧 GET /runs/{id}/report 等价）。

    v2 资产统一走 _load_exposable_report：quarantined 403、orphaned/跨 run/错误
    kind 404、superseded 历史版本可读（F4）。
    """
    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        workflow = run.workflow_version or WORKFLOW_LEGACY_V1
        if workflow != WORKFLOW_ACADEMIC_SURVEY_V2:
            if revision_id == "legacy":
                report = (run.result or {}).get("survey_report")
                if not report:
                    raise HTTPException(404, detail={"detail": "报告尚未生成", "code": "REPORT_NOT_FOUND"})
                return _render_report_response(report, run_id, "legacy", format, revision_id="legacy")
            revision = session.exec(select(ResearchReportRevision).where(
                ResearchReportRevision.run_id == run_id,
                ResearchReportRevision.revision_id == revision_id,
                ResearchReportRevision.workflow_version == WORKFLOW_LEGACY_V1,
            )).first()
            if not revision:
                raise HTTPException(404, detail={"detail": "报告版本不存在", "code": "REVISION_NOT_FOUND"})
            report = _load_exposable_report(session, run_id, revision.report_asset_id)
            return _render_report_response(report, run_id, revision.revision_id[:8], format,
                                           revision_id=revision.revision_id)
        revision = session.exec(
            select(ResearchReportRevision).where(
                ResearchReportRevision.run_id == run_id,
                ResearchReportRevision.revision_id == revision_id,
            )
        ).first()
        if not revision:
            raise HTTPException(404, detail={"detail": "报告版本不存在", "code": "REVISION_NOT_FOUND"})
        report = _load_exposable_report(session, run_id, revision.report_asset_id)
        return _render_report_response(report, run_id, revision.revision_id[:8], format,
                                       revision_id=revision.revision_id)


@router.get("/runs/{run_id}/evidence", response_model=EvidenceIndexResponse)
def list_evidence(
    run_id: int,
    subquestion_id: Optional[str] = Query(None, max_length=100),
    cursor: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
) -> EvidenceIndexResponse:
    """按子问题分页返回小型证据索引；详情由 evidence_id 单独读取。"""
    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        workflow = run.workflow_version or WORKFLOW_LEGACY_V1
        if workflow != WORKFLOW_ACADEMIC_SURVEY_V2:
            from ..services.deep_research.report_evidence import ReportEvidenceStore

            report = (run.result or {}).get("survey_report") or {}
            store = ReportEvidenceStore.from_dict(report.get("evidence_store"))
            page = store.index(subquestion_id=subquestion_id, cursor=cursor, limit=limit)
            return EvidenceIndexResponse(run_id=run_id, workflow_version=workflow, **page)

        rows = session.exec(
            select(ResearchEvidence).where(ResearchEvidence.run_id == run_id)
            .order_by(ResearchEvidence.id)  # type: ignore[arg-type]
        ).all()
        items = []
        for row in rows:
            relation = row.relation or ""
            if subquestion_id and subquestion_id not in relation:
                continue
            items.append(EvidenceIndexItem(
                evidence_id=row.evidence_id, evidence_type="relation", paper_key=None,
                fact_summary=relation[:360], verification_status="unverified",
                location_status=((row.spans or [{}])[0]).get("location_status"),
            ))
        selected = items[cursor:cursor + limit]
        return EvidenceIndexResponse(
            run_id=run_id, workflow_version=workflow, evidence_version="database",
            subquestion_id=subquestion_id, cursor=cursor, limit=limit, total=len(items),
            next_cursor=cursor + limit if cursor + limit < len(items) else None, items=selected,
        )


@router.get("/runs/{run_id}/evidence/{evidence_id}", response_model=EvidenceDetailResponse)
def get_evidence(run_id: int, evidence_id: str) -> EvidenceDetailResponse:
    """许可范围内的摘录、文档版本与页/表定位。

    403 语义（S2 内容许可）：资产被隔离（quarantined）时不通过 API 暴露内容。
    v1 证据从当前报告的共享证据存储读取；旧报告没有该存储时返回 404。
    """
    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        if (run.workflow_version or WORKFLOW_LEGACY_V1) != WORKFLOW_ACADEMIC_SURVEY_V2:
            from ..services.deep_research.report_evidence import ReportEvidenceStore

            report = (run.result or {}).get("survey_report") or {}
            records = ReportEvidenceStore.from_dict(report.get("evidence_store")).details([evidence_id])
            if not records:
                raise HTTPException(404, detail={"detail": "证据不存在", "code": "EVIDENCE_NOT_FOUND"})
            record = records[0]
            source = record.get("source") or {}
            return EvidenceDetailResponse(
                evidence_id=evidence_id, run_id=run_id, asset_id=None,
                spans=[EvidenceSpanEntry.model_validate(span) for span in record.get("spans") or []],
                relation=None, evidence_type=record.get("evidence_type"),
                fact=record.get("fact") or {}, subquestion_ids=record.get("subquestion_ids") or [],
                missing_fields=record.get("missing_fields") or [],
                comparability=record.get("comparability") or {},
                verification=record.get("verification") or {},
                document={"content_hash": source.get("document_hash"),
                          "paper_key": source.get("paper_key"),
                          "source_kind": source.get("source_kind")},
                created_at=None,
            )
        evidence = session.exec(
            select(ResearchEvidence).where(
                ResearchEvidence.run_id == run_id, ResearchEvidence.evidence_id == evidence_id
            )
        ).first()
        if not evidence:
            raise HTTPException(404, detail={"detail": "证据不存在", "code": "EVIDENCE_NOT_FOUND"})
        asset = session.exec(
            select(ResearchAsset).where(ResearchAsset.asset_id == evidence.asset_id)
        ).first()
        if asset is not None and asset.status == "quarantined":
            raise HTTPException(
                403,
                detail={"detail": "证据所属文档已被隔离，不对外暴露内容", "code": "EVIDENCE_NOT_EXPOSED"},
            )
        document = {}
        if asset is not None:
            document = {
                "asset_id": asset.asset_id,
                "content_hash": asset.content_hash,
                "format": asset.format,
                "parse_status": asset.parse_status,
                "family_key": asset.family_key,
                "version_key": asset.version_key,
                "retrieved_at": asset.retrieved_at.isoformat() if asset.retrieved_at else None,
                "public_available_at": asset.public_available_at.isoformat() if asset.public_available_at else None,
            }
        spans = [EvidenceSpanEntry.model_validate(span) for span in evidence.spans or []]
        return EvidenceDetailResponse(
            evidence_id=evidence.evidence_id, run_id=evidence.run_id, asset_id=evidence.asset_id,
            spans=spans, relation=evidence.relation, document=document,
            created_at=evidence.created_at,
        )
