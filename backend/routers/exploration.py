"""
研究路由 — 单循环研究（services/deep_research/loop）的创建、查询、SSE 进度、暂停/继续/取消、修正与追问。
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import text
from sqlalchemy import update as sa_update
from sqlmodel import Session, select

from ..models.exploration import ExplorationMessage, ExplorationRun
from ..models.schemas import (
    ExplorationMessageCreate, ExplorationMessageResponse, ExplorationRunCreate, ExplorationRunResponse,
    ExplorationRunSummary, PreflightResponse, ResearchRevisionCreate, RunAdjustRequest, RunControlResponse,
    SourceHealth, StreamEvent,
)
from ..routers.research import _stream_bus_with_heartbeat
from ..services import exploration_chat
from ..services.database import get_engine
from ..services.deep_research import loop
from ..services.deep_research.loop.export import render_html, render_markdown
from ..services.research_sources import SourceError, seed_options
from ..utils.streaming import format_sse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/exploration", tags=["exploration"])

ACTIVE = ("pending", "running", "paused")
PAUSE_ESCALATION_SECONDS = 90.0
_tasks: set[asyncio.Task] = set()


def _err(status: int, code: str, detail: str) -> HTTPException:
    return HTTPException(status, detail={"detail": detail, "code": code})


def _get_run_or_404(session: Session, run_id: int) -> ExplorationRun:
    run = session.get(ExplorationRun, run_id)
    if not run:
        raise _err(404, "NOT_FOUND", "研究记录不存在")
    return run


def _other_active(session: Session, run_id: int | None = None) -> ExplorationRun | None:
    stmt = select(ExplorationRun).where(ExplorationRun.status.in_(ACTIVE))
    if run_id is not None:
        stmt = stmt.where(ExplorationRun.id != run_id)
    return session.exec(stmt).first()


def _launch(run_id: int, *, resume: bool = False) -> None:
    loop.get_bus(run_id)
    loop.start_worker(run_id, loop.run_research(run_id, resume=resume))


def _checkpoint_summary(checkpoint: dict | None) -> dict | None:
    if not checkpoint:
        return None
    return {"phase": checkpoint.get("phase"), "layer": checkpoint.get("layer"),
            "candidates": len(checkpoint.get("papers") or {}), "expanded": len(checkpoint.get("expanded") or []),
            "queries_done": len(checkpoint.get("queries_done") or []),
            "queries_todo": len(checkpoint.get("queries_todo") or [])}


def _response(run: ExplorationRun) -> ExplorationRunResponse:
    resp = ExplorationRunResponse.model_validate(run, from_attributes=True)
    resp.checkpoint_summary = _checkpoint_summary(run.checkpoint)
    return resp


# ── 预检 ────────────────────────────────────────────────────────────────────

@router.get("/preflight", response_model=PreflightResponse)
async def preflight_sources() -> PreflightResponse:
    from ..services.deep_research.preflight import preflight_research_sources

    healths = await preflight_research_sources()
    return PreflightResponse(checked_at=datetime.now(timezone.utc), sources=[SourceHealth(**h) for h in healths])


# ── 创建 / 列表 / 详情 / 删除 ────────────────────────────────────────────────

@router.post("/runs", response_model=ExplorationRunResponse)
async def create_exploration(req: ExplorationRunCreate) -> ExplorationRunResponse:
    question = req.question.strip()
    if len(question) < 2:
        raise _err(422, "INVALID_QUESTION", "研究问题至少两个字符")
    with Session(get_engine()) as session:
        existing = _other_active(session)
        if existing:
            hint = "已有暂停的研究，请先继续或删除该研究" if existing.status == "paused" else "已有运行中的研究"
            raise _err(409, "EXPLORATION_RUNNING", hint)
        try:
            options = seed_options(session, req.workspace_id, req.seed_paper_ids, req.search_online,
                                   exclude_dois=req.exclude_dois, exclude_arxiv_ids=req.exclude_arxiv_ids,
                                   exclude_titles=req.exclude_titles)
        except SourceError as exc:
            raise _err(exc.status, "INVALID_SOURCES", str(exc)) from exc
        if req.loop:
            options["loop"] = dict(req.loop)
        if req.answer:
            options["answer"] = dict(req.answer)
        run = ExplorationRun(question=question, depth=req.depth, status="pending", options=options)
        session.add(run)
        try:
            session.commit()
        except Exception as exc:
            from sqlalchemy.exc import IntegrityError

            session.rollback()
            if isinstance(exc, IntegrityError) and "EXPLORATION_RUNNING" in str(exc.orig):
                raise _err(409, "EXPLORATION_RUNNING", "已有运行中的研究") from exc
            raise
        session.refresh(run)
        response = _response(run)
    _launch(response.id)
    return response


@router.get("/runs", response_model=list[ExplorationRunSummary])
async def list_explorations(limit: int = Query(20, ge=1, le=100)) -> list[ExplorationRunSummary]:
    with Session(get_engine()) as session:
        runs = session.exec(select(ExplorationRun).order_by(ExplorationRun.created_at.desc()).limit(limit)).all()
        # 选中篇数与作答状态在库内提取：不把大 result 列物化进 Python
        rows = session.execute(text(
            "SELECT id, json_array_length(json_extract(result, '$.papers')) AS n, "
            "json_extract(result, '$.answer.status') AS a FROM exploration_runs ORDER BY created_at DESC LIMIT :lim"
        ), {"lim": limit}).all()
        extra = {r.id: (int(r.n or 0), r.a or "") for r in rows}
    return [ExplorationRunSummary(id=r.id, question=r.question, depth=r.depth, status=r.status, provider=r.provider,
                                  options=r.options or {}, selected=extra.get(r.id, (0, ""))[0],
                                  answer_status=extra.get(r.id, (0, ""))[1], created_at=r.created_at)
            for r in runs]


@router.get("/runs/{run_id}", response_model=ExplorationRunResponse)
async def get_exploration(run_id: int) -> ExplorationRunResponse:
    with Session(get_engine()) as session:
        return _response(_get_run_or_404(session, run_id))


@router.delete("/runs/{run_id}")
async def delete_exploration(run_id: int) -> dict:
    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        if run.status in ("running", "pending") and loop.has_live_worker(run_id):
            raise _err(409, "EXPLORATION_RUNNING", "研究运行中，请先暂停或取消")
        messages = session.exec(select(ExplorationMessage).where(ExplorationMessage.run_id == run_id)).all()
        if any(m.status in ("running", "pending") for m in messages):
            raise _err(409, "CHAT_RUNNING", "追问运行中，暂不能删除")
        for message in messages:
            session.delete(message)
        session.flush()
        session.delete(run)
        session.commit()
    loop.cancel_pause_request(run_id)
    loop.drop_bus(run_id)
    return {"message": "已删除"}


@router.post("/runs/{run_id}/remove")
async def remove_exploration(run_id: int) -> dict:
    return await delete_exploration(run_id)


# ── SSE 进度 ────────────────────────────────────────────────────────────────

def _replay(result: dict | None) -> dict:
    return dict(result or {})


@router.get("/runs/{run_id}/stream")
async def stream_exploration(run_id: int) -> StreamingResponse:
    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        status, result, error, stage = run.status, run.result, run.error_message, run.current_stage

    async def gen():
        if status in ("done", "failed", "paused", "cancelled") or not loop.has_bus(run_id):
            if result:
                yield format_sse(StreamEvent(event="metrics_ready", data=_replay(result)))
                if result.get("answer"):
                    yield format_sse(StreamEvent(event="answer_ready", data=result["answer"]))
            if status == "done":
                yield format_sse(StreamEvent(event="done", data={"run_id": run_id}))
            elif status == "paused":
                yield format_sse(StreamEvent(event="paused", data={"run_id": run_id, "stage": stage or ""}))
            elif status == "cancelled":
                yield format_sse(StreamEvent(event="cancelled", data={"run_id": run_id}))
            elif status == "failed":
                yield format_sse(StreamEvent(event="error", data={"message": error or "研究失败", "stage": stage or ""}))
            else:
                yield format_sse(StreamEvent(event="error", data={
                    "message": "研究进程已中断（可能是服务重启），可从断点继续", "stage": stage or ""}))
            return
        async for chunk in _stream_bus_with_heartbeat(loop.get_bus(run_id).subscribe(), timeout=15.0):
            yield chunk

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                                      "X-Accel-Buffering": "no"})


# ── 暂停 / 继续 / 取消 ──────────────────────────────────────────────────────

def _set_status_if(run_id: int, current: str, new: str) -> bool:
    with Session(get_engine()) as session:
        stmt = (sa_update(ExplorationRun)
                .where(ExplorationRun.id == run_id, ExplorationRun.status == current)
                .values(status=new, updated_at=datetime.now(timezone.utc)))
        ok = session.exec(stmt).rowcount == 1  # type: ignore[attr-defined]
        session.commit()
        return ok


def _schedule_pause_escalation(run_id: int) -> None:
    """worker 卡在不可中断的调用里时，宽限期后强制取消；runner 的 CancelledError 收尾负责落库 paused。"""
    async def escalate() -> None:
        await asyncio.sleep(PAUSE_ESCALATION_SECONDS)
        if loop.pause_pending(run_id) and loop.has_live_worker(run_id):
            logger.warning("run %s: pause not observed in %.0fs, cancelling worker", run_id, PAUSE_ESCALATION_SECONDS)
            loop.cancel_worker(run_id)

    task = asyncio.create_task(escalate())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


@router.post("/runs/{run_id}/pause", response_model=RunControlResponse)
async def pause_run(run_id: int) -> RunControlResponse:
    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        status = run.status
    if status == "paused":
        return RunControlResponse(run_id=run_id, status="paused", detail="已暂停")
    if status not in ("running", "pending"):
        raise _err(409, "INVALID_STATE", f"当前状态 {status} 不可暂停")
    if loop.request_pause(run_id):
        _schedule_pause_escalation(run_id)
        return RunControlResponse(run_id=run_id, status=status, detail="已请求暂停，将在当前动作结束后停止并保留断点")
    loop.cancel_pause_request(run_id)
    if not _set_status_if(run_id, status, "paused"):
        raise _err(409, "STATE_CONFLICT", "状态已变化，请刷新后重试")
    return RunControlResponse(run_id=run_id, status="paused", detail="研究进程已不在运行，已直接标记为暂停，可从断点继续")


@router.post("/runs/{run_id}/resume", response_model=RunControlResponse)
async def resume_run(run_id: int) -> RunControlResponse:
    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        if loop.has_live_worker(run_id):
            raise _err(409, "INVALID_STATE", "研究正在运行中")
        if run.status not in ("failed", "paused", "running", "pending"):
            raise _err(409, "INVALID_STATE", f"当前状态 {run.status} 无需继续")
        if _other_active(session, run_id):
            raise _err(409, "EXPLORATION_RUNNING", "已有其他研究任务")
        status = run.status
    if not _set_status_if(run_id, status, "running"):
        raise _err(409, "STATE_CONFLICT", "状态已变化，请刷新后重试")
    _launch(run_id, resume=True)
    return RunControlResponse(run_id=run_id, status="running", detail="已从断点继续研究")


@router.post("/runs/{run_id}/cancel", response_model=RunControlResponse)
async def cancel_run(run_id: int) -> RunControlResponse:
    """取消：保留已有结果；运行中的 worker 被取消后在收尾里推送 cancelled。"""
    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        status = run.status
    if status not in ACTIVE:
        raise _err(409, "INVALID_STATE", f"当前状态 {status} 不可取消")
    if not _set_status_if(run_id, status, "cancelled"):
        raise _err(409, "STATE_CONFLICT", "状态已变化，请刷新后重试")
    loop.cancel_worker(run_id)
    return RunControlResponse(run_id=run_id, status="cancelled", detail="已取消")


@router.post("/runs/{run_id}/adjust")
def adjust_paused_run(run_id: int, req: RunAdjustRequest) -> dict:
    """暂停时干预：剔除文献（续跑时从候选池移除）、追加检索式（续跑时先执行）。"""
    from ..services.paper_identity import normalize_arxiv_id, normalize_doi

    if not req.exclude_keys and not req.add_queries:
        raise _err(422, "EMPTY_ADJUSTMENT", "请至少剔除一篇文献或追加一条检索式")
    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        if run.status != "paused" or loop.has_live_worker(run_id):
            raise _err(409, "INVALID_STATE", "只能在研究暂停后调整")
        pool = {**((run.checkpoint or {}).get("papers") or {}),
                **{p["key"]: p for p in ((run.result or {}).get("papers") or [])}}
        unknown = [k for k in req.exclude_keys if k not in pool]
        if unknown:
            raise _err(422, "INVALID_ADJUSTMENT", f"文献不在当前材料中：{', '.join(unknown[:3])}")
        options = dict(run.options or {})
        exclusions = {k: list(v) for k, v in (options.get("exclusions") or {}).items()}
        for key in req.exclude_keys:
            paper = pool[key]
            if paper.get("doi") and normalize_doi(paper["doi"]):
                exclusions.setdefault("dois", []).append(normalize_doi(paper["doi"]))
            elif paper.get("arxiv_id") and normalize_arxiv_id(paper["arxiv_id"]):
                exclusions.setdefault("arxiv_ids", []).append(normalize_arxiv_id(paper["arxiv_id"]))
            elif paper.get("title"):
                exclusions.setdefault("titles", []).append(paper["title"].strip())
        if exclusions:
            options["exclusions"] = {k: sorted(set(v)) for k, v in exclusions.items()}
        queries = [q.strip() for q in req.add_queries]
        if queries:
            options["pending_user_queries"] = list(dict.fromkeys([*(options.get("pending_user_queries") or []), *queries]))
        intervention = {"at": datetime.now(timezone.utc).isoformat(), "stage": run.current_stage,
                        "excluded": [pool[k].get("title") or k for k in req.exclude_keys],
                        "queries": queries, "note": req.note.strip()}
        options["interventions"] = [*(options.get("interventions") or []), intervention]
        run.options = options
        run.updated_at = datetime.now(timezone.utc)
        session.add(run)
        session.commit()
        return {"run_id": run_id, "status": run.status, "intervention": intervention}


# ── 完成后修正 ──────────────────────────────────────────────────────────────

@router.post("/runs/{run_id}/revise", response_model=ExplorationRunResponse)
async def revise_research(run_id: int, req: ResearchRevisionCreate) -> ExplorationRunResponse:
    question = req.question.strip()
    with Session(get_engine()) as session:
        parent = _get_run_or_404(session, run_id)
        if parent.status in ("running", "pending"):
            raise _err(409, "INVALID_STATE", "原研究仍在运行，请等它结束或先暂停")
        if _other_active(session):
            raise _err(409, "EXPLORATION_RUNNING", "已有运行中或暂停的研究，请先处理")
        base = dict(parent.options or {})
        old = base.get("exclusions") or {}
        papers = {p.get("key"): p for p in ((parent.result or {}).get("papers") or [])}
        unknown = [k for k in req.exclude_keys if k not in papers]
        if unknown:
            raise _err(422, "INVALID_REVISION", f"排除的文献不在原研究材料中：{', '.join(unknown[:3])}")
        dropped = [papers[k] for k in req.exclude_keys]
        workspace_id = req.workspace_id if req.workspace_id is not None else base.get("workspace_id")
        seeds = req.seed_paper_ids or (base.get("seed_paper_ids") or None if workspace_id else None)
        online = base.get("search_online", True) if req.search_online is None else req.search_online
        if not online and not workspace_id and not req.reuse_materials:
            raise _err(422, "INVALID_REVISION", "不联网且无工作区时必须复用原研究材料")
        try:
            options = seed_options(
                session, workspace_id, seeds, online or not workspace_id,
                exclude_dois=[*(old.get("dois") or []), *(p["doi"] for p in dropped if p.get("doi"))],
                exclude_arxiv_ids=[*(old.get("arxiv_ids") or []), *(p["arxiv_id"] for p in dropped if p.get("arxiv_id"))],
                exclude_titles=[*(old.get("titles") or []),
                                *(p["title"] for p in dropped if p.get("title") and not p.get("doi") and not p.get("arxiv_id"))])
        except SourceError as exc:
            raise _err(exc.status, "INVALID_SOURCES", str(exc)) from exc
        options["search_online"] = bool(online)
        options["parent_run_id"] = parent.id
        for key in ("loop", "answer"):
            if base.get(key):
                options[key] = base[key]
        if req.reuse_materials:
            options["reuse_from_run_id"] = parent.id
        if req.additional_questions:
            options["revision_conditions"] = [q.strip() for q in req.additional_questions]
        child = ExplorationRun(question=question, depth=req.depth or parent.depth, status="pending", options=options)
        session.add(child)
        session.commit()
        session.refresh(child)
        response = _response(child)
    _launch(response.id)
    return response


@router.get("/runs/{run_id}/versions")
def research_versions(run_id: int) -> dict:
    """版本链：沿 parent_run_id 上溯祖先，列出直接子修正。"""
    def row(r: ExplorationRun) -> dict:
        return {"id": r.id, "question": r.question, "status": r.status, "depth": r.depth, "created_at": r.created_at,
                "conditions": (r.options or {}).get("revision_conditions") or [],
                "reused": bool((r.options or {}).get("reuse_from_run_id"))}

    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        ancestors, cursor, seen = [], (run.options or {}).get("parent_run_id"), {run.id}
        while cursor and cursor not in seen and len(ancestors) < 20:
            seen.add(cursor)
            node = session.get(ExplorationRun, cursor)
            if node is None:
                break
            ancestors.insert(0, row(node))
            cursor = (node.options or {}).get("parent_run_id")
        children = [row(r) for r in session.exec(select(ExplorationRun).order_by(ExplorationRun.id)).all()
                    if (r.options or {}).get("parent_run_id") == run.id]
        return {"run_id": run.id, "current": row(run), "ancestors": ancestors, "children": children}


# ── 导出 / 用量 / 追问 ──────────────────────────────────────────────────────

@router.get("/runs/{run_id}/report")
def export_report(run_id: int, format: str = Query("markdown", pattern="^(html|markdown|json)$")):
    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        result = dict(run.result or {})
    if not result.get("answer") and not result.get("papers"):
        raise _err(404, "REPORT_NOT_FOUND", "研究尚未产出结果")
    if format == "json":
        return Response(json.dumps(result, ensure_ascii=False), media_type="application/json")
    content = render_html(result) if format == "html" else render_markdown(result)
    suffix, mime = ("html", "text/html") if format == "html" else ("md", "text/markdown")
    return Response(content, media_type=mime, headers={
        "Content-Disposition": f'attachment; filename="research-{run_id}.{suffix}"',
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'"})


@router.get("/runs/{run_id}/token-usage")
def get_run_token_usage(run_id: int) -> dict:
    from ..services import token_usage as usage_service

    with Session(get_engine()) as session:
        run = _get_run_or_404(session, run_id)
        snapshot = dict(((run.result or {}).get("sources") or {}).get("token_usage") or {})
        created, instance = run.created_at, run.run_instance_id
    data = usage_service.run_usage(str(run_id), since=created, run_instance_id=instance or None)
    if not data["recorded"] and snapshot:
        data = usage_service.legacy_run_usage(snapshot)
    data["run_id"] = run_id
    return data


@router.get("/runs/{run_id}/messages", response_model=list[ExplorationMessageResponse])
def list_messages(run_id: int):
    try:
        return exploration_chat.conversation(run_id)
    except exploration_chat.ExplorationChatError as exc:
        raise _err(exc.status, exc.code, exc.detail) from exc


@router.post("/runs/{run_id}/messages", response_model=ExplorationMessageResponse)
async def post_message(run_id: int, req: ExplorationMessageCreate):
    try:
        message = exploration_chat.create_message(run_id, req.question.strip(), req.search_more, req.selected_keys)
    except exploration_chat.ExplorationChatError as exc:
        raise _err(exc.status, exc.code, exc.detail) from exc
    exploration_chat.start_message(message.id)
    return message
