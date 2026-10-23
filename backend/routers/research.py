"""
Research 路由

深度研究的 API 端点：创建/查询 run、SSE 进度、图谱查询、RAG 问答。
"""

import asyncio
import logging
from contextlib import suppress
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..services.database import get_engine
from ..models.research import ResearchRun, ResearchGraph
from ..models.workspace import Workspace, WorkspacePaper
from ..models.paper import Paper
from ..models.schemas import (
    StreamEvent,
    ResearchRunCreate, ResearchRunResponse, ResearchGraphResponse,
    RagQueryRequest, RagQueryResponse, RagCitation, RagCitationDebug,
)
from ..utils.streaming import format_sse
from ..services.deep_research.pipeline import run_pipeline
from ..services.deep_research.event_bus import get_event_bus, has_event_bus
from ..services.deep_research.path_context import build_path_context
from ..services.deep_research.vector_store import drop_workspace_collection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/research", tags=["research"])

_COLLECTABLE_PAPER_STATUSES = ("done", "partial", "indexed", "pending")


def _has_collectable_papers(session: Session, ws_id: int, req: ResearchRunCreate) -> bool:
    """复用 stage_collect 的论文选择条件，但不运行 pipeline 代码。

    当用户显式传入 paper_ids 时，必须至少有一篇可收集的 L1；
    不能仅因工作区存在 L2+ 就放行（否则会跑出与勾选无关的图谱）。
    """
    base = (
        select(Paper.id)
        .join(WorkspacePaper, WorkspacePaper.paper_id == Paper.id)
        .where(WorkspacePaper.workspace_id == ws_id)
        .where(Paper.status.in_(_COLLECTABLE_PAPER_STATUSES))
    )

    if req.paper_ids:
        picked_l1 = session.exec(
            base.where(Paper.id.in_(req.paper_ids)).where(Paper.level == 1)
        ).first()
        return picked_l1 is not None

    if not req.include_expanded:
        return session.exec(base.where(Paper.level == 1)).first() is not None
    return session.exec(base).first() is not None


async def _stream_bus_with_heartbeat(stream_iter, timeout: float = 15.0):
    """Yield SSE events while keeping the connection alive without cancelling the subscription."""

    next_event = asyncio.create_task(stream_iter.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait({next_event}, timeout=timeout)
            if not done:
                yield ": keepalive heartbeat\n\n"
                continue

            try:
                evt = next_event.result()
            except StopAsyncIteration:
                break

            yield format_sse(evt)
            next_event = asyncio.create_task(stream_iter.__anext__())
    finally:
        if not next_event.done():
            next_event.cancel()
            with suppress(asyncio.CancelledError):
                await next_event
        aclose = getattr(stream_iter, "aclose", None)
        if aclose is not None:
            with suppress(Exception):
                await aclose()


@router.post("/workspaces/{ws_id}/runs", response_model=ResearchRunResponse)
async def create_run(ws_id: int, req: ResearchRunCreate) -> ResearchRunResponse:
    engine = get_engine()
    with Session(engine) as session:
        ws = session.get(Workspace, ws_id)
        if not ws:
            raise HTTPException(404, detail={"detail": "工作区不存在", "code": "NOT_FOUND"})

        # 拒绝同时存在未结束任务（pending 窗口内连点也会命中，避免双 pipeline 竞态）
        existing = session.exec(
            select(ResearchRun)
            .where(ResearchRun.workspace_id == ws_id)
            .where(ResearchRun.status.in_(["running", "pending"]))
        ).first()
        if existing:
            raise HTTPException(409, detail={"detail": "已有运行中的研究任务", "code": "RESEARCH_RUNNING"})

        # 校验工作区有可用论文，保持与 stage_collect/UI 的选择口径一致。
        if not _has_collectable_papers(session, ws_id, req):
            raise HTTPException(400, detail={"detail": "工作区没有可用于深度研究的论文", "code": "WORKSPACE_EMPTY"})

        from ..services.ai_config import get_research_config
        params = req.model_dump()
        if "backbone_alpha" not in req.model_fields_set:
            params["backbone_alpha"] = get_research_config().get("backbone_alpha", 0.05)
        run = ResearchRun(
            workspace_id=ws_id,
            mode=req.mode,
            status="pending",
            params=params,
        )
        session.add(run)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, detail={"detail": "已有运行中的研究任务", "code": "RESEARCH_RUNNING"}) from None
        session.refresh(run)
        run_id = run.id
        if run_id is None:
            raise HTTPException(500, detail={"detail": "研究任务创建失败", "code": "INTERNAL_ERROR"})

    # 提前创建事件总线，避免前端在 pipeline task 获得调度前订阅到“无 bus”状态。
    get_event_bus(run_id)

    # 异步启动 pipeline（不在 HTTP 请求内阻塞）
    asyncio.create_task(run_pipeline(run_id))

    with Session(engine) as session:
        run = session.get(ResearchRun, run_id)
        if not run:
            raise HTTPException(404, detail={"detail": "Run not found", "code": "NOT_FOUND"})
        return ResearchRunResponse.model_validate(run, from_attributes=True)


@router.get("/runs/{run_id}", response_model=ResearchRunResponse)
async def get_run(run_id: int) -> ResearchRunResponse:
    engine = get_engine()
    with Session(engine) as session:
        run = session.get(ResearchRun, run_id)
        if not run:
            raise HTTPException(404, detail={"detail": "Run not found", "code": "NOT_FOUND"})
        return ResearchRunResponse.model_validate(run, from_attributes=True)


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: int) -> StreamingResponse:
    engine = get_engine()
    with Session(engine) as session:
        run = session.get(ResearchRun, run_id)
        if not run:
            raise HTTPException(404, detail={"detail": "Run not found", "code": "NOT_FOUND"})
        # 提取需要在 session 外使用的值
        run_status = run.status
        run_workspace_id = run.workspace_id
        run_node_count = run.node_count
        run_edge_count = run.edge_count
        run_error_message = run.error_message
        run_current_stage = run.current_stage

    async def gen():
        # 1. 历史已结束的 run：直接回放最终事件后关闭
        if run_status == "done":
            with Session(engine) as s:
                g = s.exec(select(ResearchGraph).where(ResearchGraph.workspace_id == run_workspace_id)).first()
                if g:
                    yield format_sse(StreamEvent(event="graph_ready",
                        data={"nodes": g.nodes, "edges": g.edges, "stats": g.stats}))
                    if g.synthesis:
                        yield format_sse(StreamEvent(event="synthesis_ready", data=g.synthesis))
                yield format_sse(StreamEvent(event="done",
                    data={"run_id": run_id,
                          "node_count": run_node_count,
                          "edge_count": run_edge_count}))
            return
        if run_status == "failed":
            yield format_sse(StreamEvent(event="error",
                data={"message": run_error_message or "Unknown error",
                      "stage": run_current_stage or ""}))
            return

        if not has_event_bus(run_id):
            yield format_sse(StreamEvent(event="error",
                data={"message": "研究任务未在当前进程中运行，请重新创建任务",
                      "stage": run_current_stage or ""}))
            return

        # 2. 进行中 / 未开始：订阅 event_bus，加入 15s 超时 heartbeat 保活
        bus = get_event_bus(run_id)
        stream_iter = bus.subscribe()
        async for chunk in _stream_bus_with_heartbeat(stream_iter, timeout=15.0):
            yield chunk


    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/workspaces/{ws_id}/graph", response_model=ResearchGraphResponse)
async def get_graph(ws_id: int) -> ResearchGraphResponse:
    engine = get_engine()
    with Session(engine) as session:
        g = session.exec(select(ResearchGraph).where(ResearchGraph.workspace_id == ws_id)).first()
        if not g:
            raise HTTPException(404, detail={"detail": "尚未构建图谱", "code": "NOT_FOUND"})
        return ResearchGraphResponse(
            workspace_id=g.workspace_id, run_id=g.run_id,
            nodes=g.nodes, edges=g.edges, stats=g.stats, synthesis=g.synthesis,
            updated_at=g.updated_at,
        )


@router.get("/workspaces/{ws_id}/latest-run", response_model=ResearchRunResponse)
async def latest_run(ws_id: int) -> ResearchRunResponse:
    engine = get_engine()
    with Session(engine) as session:
        run = session.exec(
            select(ResearchRun).where(ResearchRun.workspace_id == ws_id)
            .order_by(ResearchRun.created_at.desc())
        ).first()
        if not run:
            raise HTTPException(404, detail={"detail": "无历史运行", "code": "NOT_FOUND"})
        return ResearchRunResponse.model_validate(run, from_attributes=True)


# ── 引用回填（存量数据修复：PaperReference 空表 → 图谱无 cites 边）──────────
from ..services.material_activity import backfill_jobs as _BACKFILL_JOBS


async def _backfill_worker(ws_id: int, force: bool) -> None:
    from ..services.deep_research.reference_backfill import (
        backfill_workspace_references,
        rebuild_workspace_graph,
    )

    job = _BACKFILL_JOBS[ws_id]
    try:
        stats = await backfill_workspace_references(ws_id, force=force, progress=job)
        job.update({"phase": "rebuild"})
        graph_stats = await rebuild_workspace_graph(ws_id)
        job.update({"status": "done", "stats": stats, "graph_stats": graph_stats})
    except Exception as exc:
        logger.exception("reference backfill failed for ws %s", ws_id)
        job.update({"status": "failed", "error": str(exc)})


@router.post("/workspaces/{ws_id}/backfill-references")
async def start_reference_backfill(ws_id: int, force: bool = False) -> dict:
    """回填工作区论文引用（Semantic Scholar）并重建图谱。后台执行，轮询 status 端点。"""
    with Session(get_engine()) as session:
        if not session.get(Workspace, ws_id):
            raise HTTPException(404, detail={"detail": "工作区不存在", "code": "NOT_FOUND"})
    job = _BACKFILL_JOBS.get(ws_id)
    if job and job.get("status") == "running":
        raise HTTPException(409, detail={"detail": "引用回填已在进行中", "code": "ALREADY_RUNNING"})
    _BACKFILL_JOBS[ws_id] = {"status": "running", "phase": "identity", "done": 0, "total": 0}
    asyncio.create_task(_backfill_worker(ws_id, force))
    return {"status": "started"}


@router.get("/workspaces/{ws_id}/backfill-references/status")
async def reference_backfill_status(ws_id: int) -> dict:
    return _BACKFILL_JOBS.get(ws_id) or {"status": "idle"}


@router.post("/workspaces/{ws_id}/query", response_model=RagQueryResponse)
async def query(ws_id: int, req: RagQueryRequest) -> RagQueryResponse:
    from ..services.deep_research.embedder import embed_query
    from ..services.deep_research.hybrid_retriever import HybridRetriever
    from ..services.ai_config import get_research_config, get_reranker_config

    from ..services.material_lifecycle import workspace_paper_ids
    from ..services.ai_config import get_analysis_config
    from ..services.deep_research.vector_store import workspace_index_available
    engine = get_engine()
    with Session(engine) as session:
        if session.get(Workspace, ws_id) is None:
            raise HTTPException(404, detail={"detail": "工作区不存在", "code": "NOT_FOUND"})
        allowed_ids = workspace_paper_ids(session, ws_id, req.paper_ids)
    if not allowed_ids or not workspace_index_available(ws_id):
        raise HTTPException(404, detail={"detail": "未构建索引或所选资料不在工作区", "code": "CHROMA_NOT_INITIALIZED"})
    cfg = get_analysis_config()
    if not cfg.get("api_key"):
        raise HTTPException(400, detail={"detail": "请先在配置页面设置 API Key", "code": "NO_API_KEY"})
    vec = await embed_query(req.question)

    rcfg = get_reranker_config()
    research_cfg = get_research_config()
    hybrid_cfg = dict(research_cfg.get("hybrid_retrieval") or {})
    use_rerank = bool(rcfg.get("enabled", True))
    final_k = req.top_k
    recall_k = max(int(rcfg.get("candidate_k", 30)), final_k * 4)

    graph = None
    engine = get_engine()
    if req.mode == "graph":
        try:
            with Session(engine) as session:
                g = session.exec(
                    select(ResearchGraph).where(ResearchGraph.workspace_id == ws_id)
                ).first()
            if g:
                graph = {"nodes": g.nodes, "edges": g.edges, "stats": g.stats}
        except Exception as ge:
            logger.warning(f"Graph load for hybrid retrieval failed: {ge}")

    retriever = HybridRetriever(ws_id)
    result = await retriever.retrieve(
        req.question,
        vec,
        top_k=final_k,
        recall_k=recall_k,
        paper_ids=allowed_ids,
        mode=req.mode,
        use_rerank=use_rerank,
        graph=graph,
        rrf_k=int(hybrid_cfg.get("rrf_k", 60)),
        weights=dict(hybrid_cfg.get("weights") or {}),
        graph_config=dict(hybrid_cfg.get("graph") or {}),
        original_only=True,
    )
    from ..services.material_sources import verified_pdf_hits
    hits = await verified_pdf_hits([h for h in result.hits if int(h.get("paper_id", -1)) in allowed_ids])
    debug_by_chunk = {d.get("chunk_id"): d for d in result.debug}
    retrieval_debug = [debug_by_chunk.get(h.get("chunk_id"), {}) for h in hits]

    if not hits:
        raise HTTPException(404, detail={"detail": "没有可定位到当前 PDF 原文的命中，请重建索引或补充 PDF", "code": "NO_ORIGINAL_EVIDENCE"})

    hit_paper_ids = {int(h["paper_id"]) for h in hits if h.get("paper_id") is not None}
    paper_statuses: dict[int, str] = {}
    if hit_paper_ids:
        with Session(engine) as session:
            rows = session.exec(select(Paper).where(Paper.id.in_(hit_paper_ids))).all()
            paper_statuses = {
                int(p.id): (p.download_status or "none")
                for p in rows
                if p.id is not None
            }

    # Graph-RAG context enhancement: PathRAG 解释性 context，不参与精排
    path_evidence = []
    if req.mode == "graph":
        try:
            path_context = build_path_context(graph, hits)
            path_evidence = path_context.evidence or []
        except Exception as ge:
            logger.warning(f"Graph context enhancement failed: {ge}")

    # The request uses one configuration snapshot and only original excerpts.
    from ..services.research_answer import generate_audited_answer

    try:
        answer, answer_status, evidence_audit = await generate_audited_answer(req.question, hits, cfg)
    except Exception as e:
        logger.warning("RAG query failed (%s)", type(e).__name__)
        raise HTTPException(status_code=500, detail={"detail": "AI 回答未完成，请检查模型配置或稍后重试", "code": "LLM_ERROR"})

    with Session(engine) as session:
        current_ids = workspace_paper_ids(session, ws_id)
        if any(int(hit["paper_id"]) not in current_ids for hit in hits):
            raise HTTPException(409, detail={"detail": "回答期间工作区资料已变化，请重新提问", "code": "MATERIAL_CHANGED"})
    from ..services.material_sources import document_versions_match
    if not await document_versions_match(hits):
        raise HTTPException(409, detail={"detail": "回答期间 PDF 版本已变化，请重新构建索引并提问", "code": "MATERIAL_CHANGED"})
    return RagQueryResponse(
        answer=answer, answer_status=answer_status, evidence_audit=evidence_audit,
        citations=[
            RagCitation(
                paper_id=h["paper_id"],
                paper_title=h["paper_title"],
                section_name=h["section_name"],
                snippet=h["snippet"],
                score=h["score"],
                download_status=paper_statuses.get(int(h["paper_id"]), "none"),
                metadata_only=False,
                source_kind=h["source_kind"], document_hash=h["document_hash"],
                page_number=h["page_number"], start_char=h["start_char"], end_char=h["end_char"],
                debug=RagCitationDebug(**d) if (d := (retrieval_debug[i] if i < len(retrieval_debug) else None)) else None,
            )
            for i, h in enumerate(hits)
        ],
        path_evidence=path_evidence,
        debug=[RagCitationDebug(**d) for d in retrieval_debug] if retrieval_debug else None,
    )


@router.delete("/workspaces/{ws_id}")
async def clear_research(ws_id: int) -> dict:
    """清空该工作区的图谱与向量索引（不删除 expanded 论文文件）"""
    engine = get_engine()
    with Session(engine) as session:
        from ..services.material_lifecycle import ensure_idle, MaterialBusyError
        try:
            ensure_idle(session, [ws_id])
        except MaterialBusyError as exc:
            raise HTTPException(409, detail={"detail": str(exc), "code": "MATERIAL_BUSY"}) from None
        g = session.exec(select(ResearchGraph).where(ResearchGraph.workspace_id == ws_id)).first()
        if g:
            session.delete(g)
            session.commit()
    drop_workspace_collection(ws_id)
    return {"message": "已清空"}
