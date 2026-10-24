"""
Exploration 路由 — 问句驱动的领域探索（GOAL.md 核心命题）

创建/查询探索任务、SSE 进度流。产物结构见 models/exploration.py。

academic_survey_v2 契约边界见 document.md §2.7、§5.9：
- 新创建默认 legacy_v1 / exploration_v8（2026-10-23 用户决定，毕设主流程）；显式 academic_survey_v2 走实验 question_loop_v20。
- 新端点仅 GET/POST；旧契约 v2 行或关闭实验开关时拒绝启动执行器。
- 实施差异与验证边界见 docs/workflow_refactor/STATUS.md。

断点续传（legacy v1）：pause 在安全点协作式停止并保留 checkpoint；resume 从
checkpoint/result 快照恢复；v2 按持久化任务/产物/额度与租约接续。
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, Response
from sqlmodel import Session, select
from sqlalchemy import text

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
    ResearchQuestion, ResearchAnswerRevision, ResearchDossierRevision,
)
from ..services import exploration_chat, research_event_log as event_log
from ..services.research_sources import SourceError, seed_options
from ..models.schemas import (
    StreamEvent,
    ExplorationRunCreate, ExplorationRunResponse, ExplorationRunSummary, ResearchRevisionCreate,
    ExplorationMessageCreate, ExplorationMessageResponse,
    PreflightResponse, SourceHealth,
    ProtocolSummaryResponse, ProtocolChangeEntry, RunProtocolResponse,
    ResearchTaskItem, ResearchTaskListResponse,
    CoverageSubquestionEntry, CoverageGapEntry, RunCoverageResponse,
    BudgetResourceEntry, RunBudgetResponse,
    RunAdjustRequest, RunControlRequest, RunControlResponse,
    RunRetryRequest, RunRetryResponse,
    ReportRevisionEntry, ReportRevisionListResponse,
    EvidenceSpanEntry, EvidenceDetailResponse, EvidenceIndexItem, EvidenceIndexResponse,
    ResearchQuestionsResponse, ResearchAnswersResponse, ResearchDossiersResponse,
    ResearchDossierResponse,
)
from ..utils.streaming import format_sse
from ..services.deep_research.explorer import (
    get_exploration_bus, has_exploration_bus, run_exploration, rebuild_survey_report,
    request_pause, cancel_pause_request, drop_exploration_bus, _checkpoint_summary,
    start_worker, has_live_worker, cancel_worker, pause_pending,
)
from ..routers.research import _stream_bus_with_heartbeat
from ..services.deep_research.research_loop import worker as loop_worker
from ..services.deep_research.research_loop.contracts import LOOP_VERSION, SUPPORTED_LOOP_VERSIONS
from ..services.deep_research.research_loop.questions import initial_questions
from ..services.deep_research.research_loop.resources import limits_for
from ..services.deep_research.research_loop import access as loop_access
from ..services.deep_research.research_loop import revisions as loop_revisions

logger = logging.getLogger(__name__)

_TASK_STATE_PATTERN = "^(" + "|".join(TASK_STATES) + ")$"
_RUN_STATE_PATTERN = "^(" + "|".join(RUN_STATES_V2) + ")$"

router = APIRouter(prefix="/api/exploration", tags=["exploration"])
_tasks: set[asyncio.Task] = set()


def _start(coro):
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# 暂停升级看门狗：协作式暂停假设 worker 迟早到达安全点，但 worker 可能卡死在
# 不可中断的调用里（历史案例：429 Retry-After 巨值导致 asyncio.sleep 数小时），
# 旗标永远无人观察，用户会停在「暂停中…」。宽限期过后强制取消 worker；
# worker 的 CancelledError 收尾负责落库 paused 并推 SSE，看门狗只做兜底落库。
PAUSE_ESCALATION_SECONDS = 90.0
PAUSE_FINALIZE_GRACE_SECONDS = 15.0


def _schedule_pause_escalation(run_id: int) -> None:
    async def _escalate() -> None:
        await asyncio.sleep(PAUSE_ESCALATION_SECONDS)
        if not pause_pending(run_id) or not has_live_worker(run_id):
            return  # 安全点已消费 / worker 已自然结束
        logger.warning(
            "exploration run %s: 暂停请求 %.0fs 未被安全点消费，强制取消 worker",
            run_id, PAUSE_ESCALATION_SECONDS,
        )
        cancel_worker(run_id)
        await asyncio.sleep(PAUSE_FINALIZE_GRACE_SECONDS)
        if not pause_pending(run_id):
            return  # worker 收尾已完成（落库 paused + SSE 通知）
        # 兜底：worker 取消后仍未完成收尾（极端：收尾路径再次卡死/被取消）
        logger.error("exploration run %s: 强制取消后仍未收尾，直接落库置 paused", run_id)
        cancel_pause_request(run_id)
        from sqlalchemy import update as sa_update
        try:
            with Session(get_engine()) as session:
                stmt = (
                    sa_update(ExplorationRun)
                    .where(ExplorationRun.id == run_id,
                           ExplorationRun.status.in_(("running", "pending")))
                    .values(status="paused", updated_at=datetime.now(timezone.utc))
                )
                if session.exec(stmt).rowcount == 1:  # type: ignore[attr-defined]
                    session.commit()
        except Exception:
            logger.exception("exploration run %s: 看门狗兜底落库失败", run_id)
        try:
            bus = get_exploration_bus(run_id)
            await bus.publish(StreamEvent(event="paused",
                                          data={"run_id": run_id, "stage": "", "forced": True}))
            drop_exploration_bus(run_id)
        except Exception:
            logger.exception("exploration run %s: 看门狗兜底 SSE 通知失败", run_id)

    _start(_escalate())


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

    if report.get("workflow_revision") in SUPPORTED_LOOP_VERSIONS:
        from ..services.deep_research.research_loop.writer import render_html
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
    try:
        loop_access.require_report(session, run_id, asset.inline_payload)
    except event_log.RunStateError as exc:
        raise HTTPException(exc.status_code, detail={"detail": exc.detail, "code": exc.code}) from exc
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
            if not loop_worker.supported(run):
                raise HTTPException(400, detail={"detail": "旧契约版本不可重生成", "code": "WORKFLOW_NOT_IMPLEMENTED"})
            if run.status not in ("completed", "partial") or loop_worker.leased(run):
                raise HTTPException(409, detail={"detail": "请等待研究执行者退出", "code": "RESEARCH_RUNNING"})
            other = session.exec(select(ExplorationRun).where(ExplorationRun.id != run_id,
                ExplorationRun.status.in_(("pending", "running", "paused")))).first()
            if other:
                raise HTTPException(409, detail={"detail": "已有其他研究任务", "code": "EXPLORATION_RUNNING"})
            chat = session.exec(select(ExplorationMessage).where(
                ExplorationMessage.run_id == run_id, ExplorationMessage.status.in_(("pending", "running")),
            )).first()
            if chat:
                raise HTTPException(409, detail={"detail": "请等待当前追问完成", "code": "CHAT_RUNNING"})
            event_log.cas_run_state(session, run_id=run_id, new_status="pending", allowed_from=("completed", "partial"))
            loop_worker.start(run_id, report_only=True)
            return ExplorationRunResponse.model_validate(run, from_attributes=True)
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
    start_worker(run_id, rebuild_survey_report(run_id))
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
    """v2 输入随 options["workflow"] 快照落盘，显式绑定执行器修订。"""
    if req.workflow_version != WORKFLOW_ACADEMIC_SURVEY_V2:
        return options
    snapshot = dict(options)
    snapshot["workflow"] = {
        "version": req.workflow_version,
        "protocol_options": req.protocol_options or {},
        "budget": req.budget or {},
        "content_cutoff": req.content_cutoff.isoformat() if req.content_cutoff else None,
        "policy_id": req.policy_id,
        "loop_revision": LOOP_VERSION,
    }
    return snapshot


@router.post("/runs", response_model=ExplorationRunResponse)
async def create_exploration(req: ExplorationRunCreate) -> ExplorationRunResponse:
    # Resolve only new requests. Persisted legacy defaults remain unchanged.
    req = req.model_copy(update={"workflow_version": req.workflow_version or WORKFLOW_LEGACY_V1})
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
            if not loop_worker.enabled():
                raise HTTPException(400, detail={"detail": "实验研究执行器已关闭", "code": "WORKFLOW_NOT_IMPLEMENTED"})
            if req.policy_id:
                raise HTTPException(422, detail={"detail": "策略引用尚不支持，请使用显式排除项", "code": "UNSUPPORTED_POLICY"})
            unknown = set(req.protocol_options or {}) - {"questions", "answer_criteria", "expected_evidence"}
            if unknown:
                raise HTTPException(422, detail={"detail": "存在不支持的协议选项", "code": "INVALID_PROTOCOL"})
            try:
                initial_questions(req.question.strip(), req.protocol_options)
                limits_for(req.depth, req.budget, revision=LOOP_VERSION)
            except (ValueError, TypeError) as exc:
                raise HTTPException(422, detail={"detail": str(exc), "code": "INVALID_PROTOCOL"}) from exc
        run = ExplorationRun(
            question=req.question.strip(), depth=req.depth, status="pending",
            options=_v2_options_snapshot(req, options),
            workflow_version=req.workflow_version or WORKFLOW_LEGACY_V1,
            state_version=1 if req.workflow_version == WORKFLOW_ACADEMIC_SURVEY_V2 else 0,
        )
        session.add(run)
        try:
            session.commit()
        except Exception as exc:
            from sqlalchemy.exc import IntegrityError
            session.rollback()
            if isinstance(exc, IntegrityError) and "EXPLORATION_RUNNING" in str(exc.orig):
                raise HTTPException(409, detail={"detail": "已有运行中的探索任务", "code": "EXPLORATION_RUNNING"}) from exc
            raise
        session.refresh(run)
        run_id = run.id
        if run_id is None:
            raise HTTPException(500, detail={"detail": "探索任务创建失败", "code": "INTERNAL_ERROR"})

    if req.workflow_version == WORKFLOW_ACADEMIC_SURVEY_V2:
        with Session(engine) as session:
            event_log.append_event(session, run_id=run_id, type="run_created", payload={"status": "pending", "loop_revision": LOOP_VERSION})
            session.commit()
        loop_worker.start(run_id)
    else:
        get_exploration_bus(run_id)
        start_worker(run_id, run_exploration(run_id))

    with Session(engine) as session:
        run = session.get(ExplorationRun, run_id)
        if not run:
            raise HTTPException(404, detail={"detail": "Run not found", "code": "NOT_FOUND"})
        return ExplorationRunResponse.model_validate(run, from_attributes=True)


def _legacy_revision_options(session: Session, parent: ExplorationRun, req: ResearchRevisionCreate) -> dict:
    """八阶段修正/补充的输入快照：继承父研究来源与排除项，追加用户剔除的文献。"""
    base = dict(parent.options or {})
    old = base.get("exclusions") or {}
    papers = {p.get("key"): p for p in ((parent.result or {}).get("papers") or [])}
    dropped = [papers[k] for k in req.exclude_asset_ids if k in papers]
    unknown = [k for k in req.exclude_asset_ids if k not in papers]
    if unknown:
        raise ValueError(f"排除的文献不在原研究材料中：{', '.join(unknown[:3])}")
    workspace_id = req.workspace_id if req.workspace_id is not None else base.get("workspace_id")
    seeds = req.seed_paper_ids or (base.get("seed_paper_ids") or None if workspace_id else None)
    search_online = base.get("search_online", True) if req.search_online is None else req.search_online
    options = seed_options(
        session, workspace_id, seeds, search_online or not workspace_id,
        exclude_dois=[*(old.get("dois") or []), *(p["doi"] for p in dropped if p.get("doi"))],
        exclude_arxiv_ids=[*(old.get("arxiv_ids") or []), *(p["arxiv_id"] for p in dropped if p.get("arxiv_id"))],
        exclude_titles=[*(old.get("titles") or []),
                        *(p["title"] for p in dropped if p.get("title") and not p.get("doi") and not p.get("arxiv_id"))],
    )
    if not search_online and not workspace_id:
        if not req.reuse_materials:
            raise ValueError("不联网且无工作区时必须复用原研究材料")
        options["search_online"] = False
    options["parent_run_id"] = parent.id
    if req.reuse_materials:
        options["reuse_from_run_id"] = parent.id
    if req.additional_questions:
        options["revision_conditions"] = list(req.additional_questions)
    return options


async def _revise_legacy(parent_id: int, req: ResearchRevisionCreate) -> ExplorationRunResponse:
    question = req.question.strip()
    if req.additional_questions:
        question += "\n补充条件：" + "；".join(q.strip() for q in req.additional_questions)
    engine = get_engine()
    with Session(engine) as session:
        parent = _get_run_or_404(session, parent_id)
        if parent.status in ("running", "pending"):
            raise HTTPException(409, detail={"detail": "原研究仍在运行，请等它结束或先暂停", "code": "INVALID_STATE"})
        active = session.exec(select(ExplorationRun).where(
            ExplorationRun.status.in_(["running", "pending", "paused"]))).first()
        if active:
            raise HTTPException(409, detail={"detail": "已有运行中或暂停的研究，请先处理", "code": "EXPLORATION_RUNNING"})
        try:
            options = _legacy_revision_options(session, parent, req)
        except SourceError as exc:
            raise HTTPException(exc.status, detail={"detail": str(exc), "code": "INVALID_SOURCES"}) from exc
        except ValueError as exc:
            raise HTTPException(422, detail={"detail": str(exc), "code": "INVALID_REVISION"}) from exc
        child = ExplorationRun(question=question, depth=req.depth or parent.depth, status="pending",
                               options=options, workflow_version=WORKFLOW_LEGACY_V1, state_version=0)
        session.add(child)
        session.commit()
        session.refresh(child)
        child_id = child.id
        response = ExplorationRunResponse.model_validate(child, from_attributes=True)
    get_exploration_bus(child_id)
    start_worker(child_id, run_exploration(child_id))
    return response


def _legacy_versions(session: Session, run: ExplorationRun) -> dict:
    """八阶段版本链：沿 parent_run_id 上溯祖先，列出直接子修订。"""
    def row(r: ExplorationRun) -> dict:
        verdict = (((r.result or {}).get("synthesis") or {}).get("verdict") or {}).get("status")
        return {"id": r.id, "question": r.question, "status": r.status, "depth": r.depth,
                "created_at": r.created_at, "verdict": verdict,
                "conditions": (r.options or {}).get("revision_conditions") or [],
                "reused": bool((r.options or {}).get("reuse_from_run_id"))}
    ancestors, cursor, seen = [], (run.options or {}).get("parent_run_id"), {run.id}
    while cursor and cursor not in seen and len(ancestors) < 20:
        seen.add(cursor)
        node = session.get(ExplorationRun, cursor)
        if node is None:
            break
        ancestors.insert(0, row(node))
        cursor = (node.options or {}).get("parent_run_id")
    children = [row(r) for r in session.exec(
        select(ExplorationRun).where(ExplorationRun.workflow_version == WORKFLOW_LEGACY_V1)
        .order_by(ExplorationRun.id)).all() if (r.options or {}).get("parent_run_id") == run.id]
    return {"run_id": run.id, "workflow_version": WORKFLOW_LEGACY_V1, "current": row(run),
            "ancestors": ancestors, "children": children}


@router.post("/runs/{run_id}/revise", response_model=ExplorationRunResponse)
async def revise_research(run_id: int, req: ResearchRevisionCreate):
    with Session(get_engine()) as session:
        parent = _get_run_or_404(session, run_id)
        legacy = (parent.workflow_version or WORKFLOW_LEGACY_V1) == WORKFLOW_LEGACY_V1
    if legacy:
        return await _revise_legacy(run_id, req)
    if not loop_worker.enabled():
        raise HTTPException(400, detail={"detail": "实验研究执行器已关闭", "code": "WORKFLOW_NOT_IMPLEMENTED"})
    with Session(get_engine()) as session:
        try:
            child = loop_revisions.create_revision(session, run_id, req, worker_active=loop_worker.active(run_id))
        except event_log.RunStateError as exc:
            session.rollback()
            raise HTTPException(exc.status_code, detail={"detail": exc.detail, "code": exc.code}) from exc
        except (ValueError, TypeError) as exc:
            session.rollback()
            raise HTTPException(422, detail={"detail": str(exc), "code": "INVALID_REVISION"}) from exc
        response = ExplorationRunResponse.model_validate(child, from_attributes=True)
    loop_worker.start(child.id)
    return response


@router.get("/runs/{run_id}/versions")
def research_versions(run_id: int):
    with Session(get_engine()) as session:
        try:
            run = _get_run_or_404(session, run_id)
            if (run.workflow_version or WORKFLOW_LEGACY_V1) == WORKFLOW_LEGACY_V1:
                return _legacy_versions(session, run)
            return loop_revisions.version_view(session, run_id, worker_active=loop_worker.active(run_id) or loop_worker.leased(run))
        except event_log.RunStateError as exc:
            raise HTTPException(exc.status_code, detail={"detail": exc.detail, "code": exc.code}) from exc


@router.get("/runs", response_model=list[ExplorationRunSummary])
async def list_explorations(limit: int = Query(20, ge=1, le=100)) -> list[ExplorationRunSummary]:
    engine = get_engine()
    with Session(engine) as session:
        runs = session.exec(
            select(ExplorationRun).order_by(ExplorationRun.created_at.desc()).limit(limit)
        ).all()
        # 报告状态用 json_extract 在库内提取：不把 MB 级 result 列物化进 Python
        report_status_rows = session.execute(text(
            "SELECT id, "
            "json_extract(result, '$.survey_report.status') AS rs, "
            "json_extract(result, '$.survey_report.quality_status') AS qs "
            "FROM exploration_runs ORDER BY created_at DESC LIMIT :lim"
        ), {"lim": limit}).all()
        report_status_map = {row.id: (row.rs or "", row.qs or "")
                             for row in report_status_rows}
    return [
        ExplorationRunSummary(
            id=r.id, question=r.question, depth=r.depth, status=r.status,
            provider=r.provider, created_at=r.created_at, options=r.options or {},
            workflow_version=r.workflow_version or WORKFLOW_LEGACY_V1,
            state_version=r.state_version or 0,
            report_status=report_status_map.get(r.id, ("", ""))[0],
            quality_status=report_status_map.get(r.id, ("", ""))[1],
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
        if run.workflow_version == WORKFLOW_ACADEMIC_SURVEY_V2:
            try:
                loop_access.require_report(session, run_id, (run.result or {}).get("survey_report") or {})
            except event_log.RunStateError as exc:
                raise HTTPException(exc.status_code, detail={"detail": exc.detail, "code": exc.code}) from exc
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
            if ((run.options or {}).get("workflow") or {}).get("loop_revision") in SUPPORTED_LOOP_VERSIONS:
                from ..services.deep_research.research_loop.cleanup import remove
                try:
                    return remove(engine, run_id)
                except event_log.RunStateError as exc:
                    raise HTTPException(exc.status_code, detail={"detail": exc.detail, "code": exc.code}) from exc
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


@router.post("/runs/{run_id}/remove")
async def remove_exploration(run_id: int) -> dict:
    return await delete_exploration(run_id)


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
        cursor = request.headers.get("last-event-id") or after
        try:
            with Session(engine) as session:
                event_log.replay_events(session, run_id, after_event_id=cursor, limit=1)
        except ValueError:
            raise HTTPException(422, detail={"detail": "未知的续订游标", "code": "INVALID_CURSOR"})

        async def gen_v2():
            current_cursor = cursor
            while not await request.is_disconnected():
                # State and event snapshot share a read transaction; no replay-to-subscription gap.
                with Session(engine) as session:
                    current = session.get(ExplorationRun, run_id)
                    if current is None:
                        return
                    state = current.status
                    result = current.result
                    error = current.error_message
                    if result:
                        try:
                            loop_access.require_report(session, run_id, result.get("survey_report") or {})
                        except event_log.RunStateError:
                            result = None
                            state, error = "failed", "报告依据当前不可读"
                    replayed = event_log.replay_events(session, run_id, after_event_id=current_cursor, limit=100)
                for row in replayed:
                    current_cursor = row.event_id
                    yield _format_persisted_event(row)
                yield format_sse(StreamEvent(event="run_status", data={"run_id": run_id, "status": state,
                                                                      "active_seconds": current.active_seconds}))
                if len(replayed) == 100:
                    continue
                if state in ("pending", "running") and not loop_worker.active(run_id) and not loop_worker.leased(current):
                    yield format_sse(StreamEvent(event="error", data={"message": "研究进程已中断，可从断点继续研究"}))
                    return
                if state not in ("pending", "running"):
                    if result:
                        yield format_sse(StreamEvent(event="metrics_ready", data=_replay_payload(result)))
                    if state == "paused":
                        yield format_sse(StreamEvent(event="paused", data={"run_id": run_id}))
                    elif state == "failed":
                        yield format_sse(StreamEvent(event="error", data={"message": error or "研究执行失败"}))
                    elif state == "cancelled":
                        yield format_sse(StreamEvent(event="cancelled", data={"run_id": run_id}))
                    else:
                        yield format_sse(StreamEvent(event="done", data={"run_id": run_id, "status": state}))
                    return
                yield ": heartbeat\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(gen_v2(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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
# academic_survey_v2 查询/控制端点；业务写入方为 research_loop 事务仓库
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
        questions = session.exec(select(ResearchQuestion).where(ResearchQuestion.run_id == run_id)).all()
        if questions:
            subquestions = [CoverageSubquestionEntry(subquestion_id=q.question_id, question=q.text,
                status=q.status, open_gaps=len(q.gaps)) for q in questions]
            gaps = [CoverageGapEntry(gap_id=f"{q.question_id}:{idx}", subquestion_id=q.question_id,
                missing_evidence_kind=gap, next_action="按缺口补搜或补读", status="open")
                for q in questions for idx, gap in enumerate(q.gaps)]
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
        protocol = {}
        if run.protocol_asset_id:
            asset = session.exec(
                select(ResearchAsset).where(ResearchAsset.asset_id == run.protocol_asset_id)
            ).first()
            protocol = (asset.inline_payload if asset else None) or {}
        if not protocol and ((run.options or {}).get("workflow") or {}).get("loop_revision") in SUPPORTED_LOOP_VERSIONS:
            workflow_options = ((run.options or {}).get("workflow") or {})
            protocol = {"budget": limits_for(run.depth, workflow_options.get("budget"), revision=workflow_options.get("loop_revision"))}
        limits = {k: v for k, v in (protocol.get("budget") or {}).items() if isinstance(v, int)
                  and k not in ("writing_tokens", "writing_calls")}
        limits.update({f"writing:{kind}": value for kind, value in (
            ("llm_tokens", (protocol.get("budget") or {}).get("writing_tokens")),
            ("llm_calls", (protocol.get("budget") or {}).get("writing_calls"))) if isinstance(value, int)})
        resources = [BudgetResourceEntry.model_validate(entry)
                     for entry in event_log.budget_snapshot(session, run_id, limits)]
        if ((run.options or {}).get("workflow") or {}).get("loop_revision") in SUPPORTED_LOOP_VERSIONS:
            resources = [entry for entry in resources if entry.resource != "wall_clock_minutes"]
            resources.append(BudgetResourceEntry(resource="active_seconds", unit="second",
                used=int(run.active_seconds), limit=int((protocol.get("budget") or {}).get("wall_clock_minutes", 0)) * 60))
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


@router.get("/runs/{run_id}/token-usage")
def get_run_token_usage(run_id: int) -> dict:
    """单次探索 run 的 LLM token 用量：优先读持久用量记录，旧 run 回退 result 内的台账快照。

    归属（计划 §16.5）：优先按 run.run_instance_id（创建时落库的不可复用 UUID）
    精确匹配——数字 run_id 删除后可被 SQLite 复用，实例 ID 保证不串账；身份列
    启用前的旧记录无实例 ID，以当前 run 行 created_at 为下限兜底过滤。
    """
    from ..services import token_usage as usage_service

    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        legacy_report = dict((run.result or {}).get("token_usage") or {})

    data = usage_service.run_usage(
        str(run_id), since=run.created_at,
        run_instance_id=run.run_instance_id or None,
    )
    if not data["recorded"] and legacy_report:
        data = usage_service.legacy_run_usage(legacy_report)
    data["run_id"] = run_id
    return data


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
async def pause_run(run_id: int, req: RunControlRequest) -> RunControlResponse:
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
            _schedule_pause_escalation(run_id)
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


_STAGES_AFTER_RECALL = ("screen", "select", "extract", "synthesize", "report")


@router.post("/runs/{run_id}/adjust")
def adjust_paused_run(run_id: int, req: RunAdjustRequest) -> dict:
    """暂停时干预（八阶段）：剔除文献与追加检索式写入输入快照，续跑时生效。

    召回已完成时，受影响的后续阶段从检查点中撤销，续跑会在缩小/补充后的语料上
    重新筛选、选材、提取与综合；未受影响篇目的逐篇提取缓存保留。
    """
    from datetime import datetime as _dt
    if not req.exclude_keys and not req.add_queries:
        raise HTTPException(422, detail={"detail": "请至少剔除一篇文献或追加一条检索式", "code": "EMPTY_ADJUSTMENT"})
    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        if (run.workflow_version or WORKFLOW_LEGACY_V1) != WORKFLOW_LEGACY_V1:
            raise HTTPException(400, detail={"detail": "问题驱动研究请使用修订功能", "code": "UNSUPPORTED_WORKFLOW"})
        if run.status != "paused" or has_exploration_bus(run_id):
            raise HTTPException(409, detail={"detail": "只能在研究暂停后调整", "code": "INVALID_STATE"})
        papers = {p.get("key"): p for p in ((run.result or {}).get("papers") or [])}
        unknown = [k for k in req.exclude_keys if k not in papers]
        if unknown:
            raise HTTPException(422, detail={"detail": f"文献不在当前材料中：{', '.join(unknown[:3])}",
                                             "code": "INVALID_ADJUSTMENT"})
        options = dict(run.options or {})
        exclusions = {k: list(v) for k, v in (options.get("exclusions") or {}).items()}
        from ..services.paper_identity import normalize_arxiv_id, normalize_doi
        for key in req.exclude_keys:
            paper = papers[key]
            if paper.get("doi") and normalize_doi(paper["doi"]):
                exclusions.setdefault("dois", []).append(normalize_doi(paper["doi"]))
            elif paper.get("arxiv_id") and normalize_arxiv_id(paper["arxiv_id"]):
                exclusions.setdefault("arxiv_ids", []).append(normalize_arxiv_id(paper["arxiv_id"]))
            elif paper.get("title"):
                exclusions.setdefault("titles", []).append(paper["title"].strip())
        if exclusions:
            for field in ("dois", "arxiv_ids"):
                exclusions.setdefault(field, [])
            options["exclusions"] = {k: sorted(set(v)) for k, v in exclusions.items()}
        queries = [q.strip() for q in req.add_queries]
        if queries:
            options["pending_user_queries"] = list(dict.fromkeys([*(options.get("pending_user_queries") or []), *queries]))
        checkpoint = dict(run.checkpoint or {})
        completed = list(checkpoint.get("completed_stages") or [])
        invalidated = [s for s in completed if s in _STAGES_AFTER_RECALL] if "recall" in completed else []
        checkpoint["completed_stages"] = [s for s in completed if s not in invalidated]
        if invalidated:
            report_cp = dict(checkpoint.get("report") or {})
            report_cp.pop("selected_keys", None)
            report_cp["extractions"] = {k: v for k, v in (report_cp.get("extractions") or {}).items()
                                        if k not in set(req.exclude_keys)}
            checkpoint["report"] = report_cp
        intervention = {"at": _dt.now(timezone.utc).isoformat(), "stage": run.current_stage,
                        "excluded": [papers[k].get("title") or k for k in req.exclude_keys],
                        "queries": queries, "note": req.note.strip(), "invalidated_stages": invalidated}
        options["interventions"] = [*(options.get("interventions") or []), intervention]
        run.options = options
        run.checkpoint = checkpoint
        run.updated_at = datetime.now(timezone.utc)
        session.add(run)
        session.commit()
        return {"run_id": run_id, "status": run.status, "intervention": intervention,
                "completed_stages": checkpoint["completed_stages"]}


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
            if not loop_worker.supported(run):
                raise HTTPException(400, detail={"detail": "旧契约运行没有可执行的研究版本，请新建研究", "code": "WORKFLOW_NOT_IMPLEMENTED"})
            if loop_worker.active(run_id) or loop_worker.leased(run):
                raise HTTPException(409, detail={"detail": "执行者尚未退出，请稍后继续", "code": "WORKER_ACTIVE"})
            other = session.exec(select(ExplorationRun).where(ExplorationRun.id != run_id,
                ExplorationRun.status.in_(("pending", "running", "paused")))).first()
            if other:
                raise HTTPException(409, detail={"detail": "已有其他研究任务", "code": "EXPLORATION_RUNNING"})
            try:
                run, replay = event_log.cas_run_state(session, run_id=run_id, new_status="pending",
                    expected_state=req.expected_state, expected_state_version=req.expected_state_version,
                    allowed_from=("paused", "failed", "pending", "running"), event_type="run_resumed")
            except event_log.RunStateError as exc:
                raise HTTPException(exc.status_code, detail={"detail": exc.detail, "code": exc.code}) from exc
            loop_worker.start(run_id)
            return RunControlResponse(run_id=run_id, status=run.status, state_version=run.state_version, idempotent_replay=replay)
        else:
            if run.status == "needs_restart":
                # v8 检查点迁移失败：旧产物与导出保留，续跑无意义，需新建研究
                raise HTTPException(
                    409,
                    detail={"detail": "旧检查点无法迁移到当前流程（候选语料缺失），"
                                      "请新建研究重跑；旧产物与报告导出已保留。",
                            "code": "CHECKPOINT_MIGRATION_FAILED"})
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
    start_worker(run_id, run_exploration(run_id, resume=True))
    return RunControlResponse(
        run_id=run_id, status="running", state_version=state_version, detail=detail,
    )


@router.post("/runs/{run_id}/cancel", response_model=RunControlResponse)
def cancel_run(run_id: int, req: RunControlRequest) -> RunControlResponse:
    """取消请求；保留既有结果（已完成任务产物不删除）。"""
    return _control_run(run_id, req, "cancelled", RUN_ACTIVE_STATES_V2, "run_cancelled", "已请求取消")


@router.post("/runs/{run_id}/retry", response_model=RunRetryResponse)
async def retry_run(run_id: int, req: RunRetryRequest) -> RunRetryResponse:
    """只重做失败/失效任务：重新入队 pending 并记录 run_retry 事件；不重做已成功任务。

    幂等：相同 idempotency_key 的重复请求返回原结果。当前 loop 复用冻结任务
    输入，不扩大预算；无可执行修订的旧契约运行拒绝重试。
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
        if not loop_worker.supported(run):
            raise HTTPException(400, detail={"detail": "旧契约运行没有可执行的研究版本或执行器已关闭", "code": "WORKFLOW_NOT_IMPLEMENTED"})
        if loop_worker.supported(run):
            if loop_worker.active(run_id) or loop_worker.leased(run):
                raise HTTPException(409, detail={"detail": "执行者尚未退出，请稍后重试", "code": "WORKER_ACTIVE"})
            other = session.exec(select(ExplorationRun).where(
                ExplorationRun.id != run_id, ExplorationRun.status.in_(("pending", "running", "paused")),
            )).first()
            if other:
                raise HTTPException(409, detail={"detail": "已有其他研究任务", "code": "EXPLORATION_RUNNING"})
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
        if not targets:
            raise HTTPException(409, detail={"detail": "没有可重试的失败任务", "code": "NO_RETRYABLE_TASKS"})
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
        if loop_worker.supported(run):
            loop_worker.start(run_id)
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
            # 交付状态/叙述来源从版本快照载荷投影；旧 revision 载荷缺失时保持 None
            payloads: dict[str, dict] = {}
            if rows:
                asset_rows = session.exec(select(ResearchAsset).where(
                    ResearchAsset.asset_id.in_([row.report_asset_id for row in rows]),  # type: ignore[attr-defined]
                )).all()
                payloads = {asset.asset_id: asset.inline_payload or {} for asset in asset_rows}
            items = [ReportRevisionEntry(
                revision_id=row.revision_id, parent_revision=row.parent_revision,
                protocol_hash=row.protocol_hash, quality_status=row.quality_status,
                stop_reason=row.stop_reason, is_selected=row.is_selected,
                legacy=True, created_at=row.created_at,
                delivery_status=(payloads.get(row.report_asset_id) or {}).get(
                    "delivery", {}).get("delivery_status"),
                narrative_source=(payloads.get(row.report_asset_id) or {}).get(
                    "delivery", {}).get("narrative_source"),
            ) for row in rows]
            if not items and (run.result or {}).get("survey_report"):
                fallback_delivery = ((run.result or {}).get("survey_report") or {}).get("delivery") or {}
                items = [ReportRevisionEntry(
                    revision_id="legacy", quality_status="unchecked",
                    is_selected=True, legacy=True, created_at=run.updated_at,
                    delivery_status=fallback_delivery.get("delivery_status"),
                    narrative_source=fallback_delivery.get("narrative_source"),
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
            relation = row.fact or row.relation or ""
            if subquestion_id and subquestion_id not in row.question_ids:
                continue
            asset = session.exec(select(ResearchAsset).where(ResearchAsset.asset_id == row.asset_id,
                ResearchAsset.run_id == run_id, ResearchAsset.content_kind == "document",
                ResearchAsset.status.in_(("active", "superseded")))).first()
            if not asset:
                continue
            items.append(EvidenceIndexItem(
                evidence_id=row.evidence_id, evidence_type=row.evidence_kind, subquestion_ids=row.question_ids,
                paper_key=asset.family_key, missing_fields=row.missing_fields,
                fact_summary=relation[:360], verification_status=row.verification.get("status", "unverified"),
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
        if asset is None or asset.run_id != run_id or asset.content_kind != "document" or asset.status not in ("active", "superseded"):
            raise HTTPException(404, detail={"detail": "证据文档不可读", "code": "EVIDENCE_NOT_FOUND"})
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
            evidence_type=evidence.evidence_kind, fact={"text": evidence.fact, **evidence.conditions},
            subquestion_ids=evidence.question_ids, missing_fields=evidence.missing_fields,
            verification=evidence.verification,
            created_at=evidence.created_at,
        )
@router.get("/runs/{run_id}/questions", response_model=ResearchQuestionsResponse)
def get_questions(run_id: int):
    from ..services.deep_research.research_loop import queries
    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        return ResearchQuestionsResponse(run_id=run_id, legacy=run.workflow_version != WORKFLOW_ACADEMIC_SURVEY_V2,
                                         items=queries.questions(session, run_id))


@router.get("/runs/{run_id}/answers", response_model=ResearchAnswersResponse)
def get_answers(run_id: int, cursor: Optional[int] = Query(None, ge=1), limit: int = Query(20, ge=1, le=50)):
    from ..services.deep_research.research_loop import queries
    with Session(get_engine()) as session:
        _get_run_or_404(session, run_id)
        rows, next_cursor = queries.revisions(session, ResearchAnswerRevision, run_id, cursor, limit)
        try:
            for row in rows:
                loop_access.require_answer(session, run_id, row)
        except event_log.RunStateError as exc:
            raise HTTPException(exc.status_code, detail={"detail": exc.detail, "code": exc.code}) from exc
        return ResearchAnswersResponse(run_id=run_id, items=[r.model_dump() for r in rows], next_cursor=next_cursor)


@router.get("/runs/{run_id}/dossiers", response_model=ResearchDossiersResponse)
def get_dossiers(run_id: int, cursor: Optional[int] = Query(None, ge=1), limit: int = Query(20, ge=1, le=50)):
    from ..services.deep_research.research_loop import queries
    with Session(get_engine()) as session:
        _get_run_or_404(session, run_id)
        rows, next_cursor = queries.revisions(session, ResearchDossierRevision, run_id, cursor, limit)
        return ResearchDossiersResponse(run_id=run_id, items=[ResearchDossierResponse(
            revision_id=r.revision_id, protocol_asset_id=r.protocol_asset_id, content_hash=r.content_hash,
            stop_reason=r.stop_reason, created_at=r.created_at, evidence_count=len(r.manifest.get("evidence", {})),
            answer_revision_ids=[a["revision_id"] for a in r.manifest.get("answers", []) if a.get("revision_id")]) for r in rows],
            next_cursor=next_cursor)
