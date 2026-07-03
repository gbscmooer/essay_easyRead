"""
Research 路由

深度研究的 API 端点：创建/查询 run、SSE 进度、图谱查询、RAG 问答。
"""

import asyncio
import logging
from contextlib import suppress
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from ..services.database import get_engine
from ..models.research import ResearchRun, ResearchGraph
from ..models.workspace import Workspace, WorkspacePaper
from ..models.paper import Paper
from ..models.schemas import (
    StreamEvent,
    ResearchRunCreate, ResearchRunResponse, ResearchGraphResponse,
    RagQueryRequest, RagQueryResponse,
)
from ..utils.streaming import format_sse
from ..services.deep_research.pipeline import run_pipeline
from ..services.deep_research.event_bus import get_event_bus, has_event_bus
from ..services.deep_research.vector_store import query_workspace, drop_workspace_collection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/research", tags=["research"])


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

        # 拒绝同时存在 running 任务
        existing = session.exec(
            select(ResearchRun)
            .where(ResearchRun.workspace_id == ws_id)
            .where(ResearchRun.status == "running")
        ).first()
        if existing:
            raise HTTPException(409, detail={"detail": "已有运行中的研究任务", "code": "RESEARCH_RUNNING"})

        # 校验工作区有可用论文
        done_papers = session.exec(
            select(Paper)
            .join(WorkspacePaper, WorkspacePaper.paper_id == Paper.id)
            .where(WorkspacePaper.workspace_id == ws_id)
            .where(Paper.status == "done")
        ).all()
        if not done_papers:
            raise HTTPException(400, detail={"detail": "工作区没有已完成解析的论文", "code": "WORKSPACE_EMPTY"})

        run = ResearchRun(
            workspace_id=ws_id,
            mode=req.mode,
            status="pending",
            params=req.model_dump(),
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    # 提前创建事件总线，避免前端在 pipeline task 获得调度前订阅到“无 bus”状态。
    get_event_bus(run_id)

    # 异步启动 pipeline（不在 HTTP 请求内阻塞）
    asyncio.create_task(run_pipeline(run_id))

    with Session(engine) as session:
        run = session.get(ResearchRun, run_id)
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
            nodes=g.nodes, edges=g.edges, stats=g.stats, updated_at=g.updated_at,
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


@router.post("/workspaces/{ws_id}/query", response_model=RagQueryResponse)
async def query(ws_id: int, req: RagQueryRequest) -> RagQueryResponse:
    from ..services.deep_research.embedder import embed_query
    from ..services.deep_research.vector_store import query_workspace as ws_query
    from ..services.deep_research.reranker import rerank
    from ..services.ai_config import get_reranker_config

    vec = await embed_query(req.question)

    rcfg = get_reranker_config()
    # rerank 启用时：先召回更大的候选集，再 rerank 取 top_n；否则直接按 req.top_k 走
    use_rerank = bool(rcfg.get("enabled", True))
    final_k = req.top_k
    if use_rerank:
        recall_k = max(int(rcfg.get("candidate_k", 30)), final_k)
        candidates = ws_query(ws_id, vec, top_k=recall_k, paper_ids=req.paper_ids)
        if not candidates:
            raise HTTPException(404, detail={"detail": "未构建索引或无命中", "code": "CHROMA_NOT_INITIALIZED"})
        ranked = await rerank(req.question, [c["snippet"] for c in candidates], top_n=final_k)
        # ranked 已按 score 降序；若 rerank 失败 _fallback 也会返回原序前 N 条
        hits = []
        for r in ranked:
            base = dict(candidates[r["index"]])
            base["score"] = r["score"]
            hits.append(base)
    else:
        hits = ws_query(ws_id, vec, top_k=final_k, paper_ids=req.paper_ids)
        if not hits:
            raise HTTPException(404, detail={"detail": "未构建索引或无命中", "code": "CHROMA_NOT_INITIALIZED"})

    # Graph-RAG context enhancement
    graph_context_str = ""
    if req.mode == "graph":
        try:
            engine = get_engine()
            with Session(engine) as session:
                graph = session.exec(
                    select(ResearchGraph).where(ResearchGraph.workspace_id == ws_id)
                ).first()
            if graph and graph.nodes:
                # Map nodes by paper id
                paper_nodes = {}
                for node in graph.nodes:
                    nid = node.get("id")
                    if nid is not None:
                        try:
                            paper_nodes[int(nid)] = node
                        except ValueError:
                            pass
                
                # Identify top paper per community
                community_top_nodes = {}
                for node in graph.nodes:
                    nid = node.get("id")
                    meta = node.get("meta", {})
                    cluster = meta.get("cluster", -1)
                    if cluster >= 0:
                        importance = meta.get("importance", 0.0)
                        label = node.get("label", "Unknown")
                        if cluster not in community_top_nodes or importance > community_top_nodes[cluster]["importance"]:
                            community_top_nodes[cluster] = {"id": nid, "label": label, "importance": importance}

                graph_contexts = []
                # Check citations between retrieved papers
                edges = graph.edges or []
                cites_relations = []
                retrieved_paper_ids = {h.get("paper_id") for h in hits if h.get("paper_id")}
                
                for edge in edges:
                    src = edge.get("source")
                    tgt = edge.get("target")
                    kind = edge.get("kind")
                    if kind == "cites" and src is not None and tgt is not None:
                        try:
                            src_id = int(src)
                            tgt_id = int(tgt)
                            if src_id in retrieved_paper_ids and tgt_id in retrieved_paper_ids:
                                src_node = paper_nodes.get(src_id)
                                tgt_node = paper_nodes.get(tgt_id)
                                if src_node and tgt_node:
                                    cites_relations.append(f"《{src_node.get('label')}》引用了《{tgt_node.get('label')}》")
                        except ValueError:
                            pass

                for hit in hits:
                    pid = hit.get("paper_id")
                    if pid and pid in paper_nodes:
                        node = paper_nodes[pid]
                        meta = node.get("meta", {})
                        cluster = meta.get("cluster", -1)
                        cluster_label = meta.get("cluster_label", "")
                        importance = meta.get("importance", 0.0)
                        
                        top_paper_info = ""
                        if cluster >= 0 and cluster in community_top_nodes:
                            top_node = community_top_nodes[cluster]
                            try:
                                if int(top_node["id"]) != pid:
                                    top_paper_info = f"，该聚类代表性文献为《{top_node['label']}》（影响力: {top_node['importance']}）"
                            except ValueError:
                                pass
                        
                        context_item = (
                            f"- 论文《{hit['paper_title']}》处于研究子方向：『{cluster_label or '未分类'}』"
                            f"（重要性得分: {importance:.2f}{top_paper_info}）"
                        )
                        if context_item not in graph_contexts:
                            graph_contexts.append(context_item)

                if graph_contexts:
                    graph_context_str = "\n【学术文献网络拓扑与聚类上下文】:\n" + "\n".join(graph_contexts)
                if cites_relations:
                    graph_context_str += "\n\n【文献引用关系】:\n" + "\n".join(cites_relations)
        except Exception as ge:
            logger.warning(f"Graph context enhancement failed: {ge}")

    # 调用 LLM 用 hits 生成回答
    from ..services.ai_analyzer import _create_client
    from ..services.ai_config import get_analysis_config
    cfg = get_analysis_config()
    context = "\n\n".join(
        f"[{i+1}] {h['paper_title']} / {h['section_name']}:\n{h['snippet']}"
        for i, h in enumerate(hits)
    )
    prompt = (
        f"你是一名学术研究助手。基于下列检索到的论文片段与学术文献关系回答用户问题。"
        f"必须使用中文，并用 [编号] 标注引用。\n\n问题：{req.question}\n\n"
        f"资料：\n{context}\n{graph_context_str}"
    )
    client = _create_client(cfg["api_key"], cfg["base_url"])
    try:
        msg = await client.messages.create(
            model=cfg["model"], max_tokens=2048,
            system="你是学术研究助手，必须基于提供的资料与文献网络关系回答，引用编号写在文末。",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        answer = msg.content[0].text.strip()

    finally:
        await client.close()

    return RagQueryResponse(
        answer=answer,
        citations=[
            {"paper_id": h["paper_id"], "paper_title": h["paper_title"],
             "section_name": h["section_name"], "snippet": h["snippet"][:400],
             "score": h["score"]}
            for h in hits
        ],
    )


@router.delete("/workspaces/{ws_id}")
async def clear_research(ws_id: int) -> dict:
    """清空该工作区的图谱与向量索引（不删除 expanded 论文文件）"""
    engine = get_engine()
    with Session(engine) as session:
        g = session.exec(select(ResearchGraph).where(ResearchGraph.workspace_id == ws_id)).first()
        if g:
            session.delete(g)
            session.commit()
    drop_workspace_collection(ws_id)
    return {"message": "已清空"}
