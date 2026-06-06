"""
Pipeline — 深度研究 6 阶段编排

collect → [expand] → chunk → embed → graph → persist
"""

import logging
from datetime import datetime, timezone
from sqlmodel import Session, create_engine

from ...config import settings
from ...models.research import ResearchRun
from ...models.schemas import StreamEvent
from .event_bus import get_event_bus, drop_event_bus
from .stages.collect import stage_collect
from .stages.expand import stage_expand
from .stages.chunk import stage_chunk
from .stages.embed import stage_embed
from .stages.graph import stage_graph
from .stages.persist import stage_persist

logger = logging.getLogger(__name__)

_engine = None


def _eng():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})
    return _engine


def _set_stage(run_id: int, stage: str, status: str = "running", **counters):
    with Session(_eng()) as s:
        run = s.get(ResearchRun, run_id)
        run.status = status
        run.current_stage = stage
        for k, v in counters.items():
            setattr(run, k, v)
        run.updated_at = datetime.now(timezone.utc)
        s.add(run)
        s.commit()


async def run_pipeline(run_id: int):
    bus = get_event_bus(run_id)

    async def emit(event: str, **data):
        await bus.publish(StreamEvent(event=event, data=data))

    try:
        with Session(_eng()) as s:
            run = s.get(ResearchRun, run_id)
            params = dict(run.params or {})
            ws_id = run.workspace_id

        _set_stage(run_id, "collect", "running")
        await emit("stage_start", stage="collect")
        papers = await stage_collect(ws_id, params)
        _set_stage(run_id, "collect", "running", paper_count=len(papers))
        await emit("stage_done", stage="collect", summary={"count": len(papers)})

        if params.get("mode") == "online":
            _set_stage(run_id, "expand")
            await emit("stage_start", stage="expand")
            deep = max(1, int(params.get("deep", 1)))
            # 多跳扩展：seeds=papers(L1) → L2 → L3 …
            # 每一跳的 seeds 是上一跳"新下载"的论文，保证只往外走不重复
            seeds_this_hop = papers
            all_expanded = []
            for hop in range(deep):
                next_level = 2 + hop  # 第 1 跳 → level=2，第 2 跳 → level=3
                new_papers = await stage_expand(
                    ws_id, seeds_this_hop, params,
                    on_paper_added=emit, next_level=next_level,
                )
                if not new_papers:
                    break
                all_expanded.extend(new_papers)
                seeds_this_hop = new_papers
            papers = papers + all_expanded
            _set_stage(run_id, "expand", "running", expanded_count=len(all_expanded))
            await emit("stage_done", stage="expand",
                       summary={"total": len(papers), "expanded": len(all_expanded), "deep": deep})

        _set_stage(run_id, "chunk")
        await emit("stage_start", stage="chunk")
        chunks = await stage_chunk(papers)
        _set_stage(run_id, "chunk", "running", chunk_count=len(chunks))
        await emit("stage_done", stage="chunk", summary={"count": len(chunks)})

        _set_stage(run_id, "embed", embed_total=len(chunks))
        await emit("stage_start", stage="embed")
        async for done in stage_embed(ws_id, chunks, rebuild=params.get("rebuild", False)):
            _set_stage(run_id, "embed", embed_done=done, embed_total=len(chunks))
            await emit("embed_progress", done=done, total=len(chunks))
        await emit("stage_done", stage="embed", summary={"total": len(chunks)})

        _set_stage(run_id, "graph")
        await emit("stage_start", stage="graph")
        graph = await stage_graph(
            ws_id, papers,
            similarity_threshold=params.get("similarity_threshold", 0.78),
            enable_shared_method=params.get("enable_shared_method", False),
        )
        _set_stage(run_id, "graph", "running",
                   node_count=len(graph["nodes"]), edge_count=len(graph["edges"]))
        await emit("graph_ready", **graph)
        await emit("stage_done", stage="graph",
                   summary={"nodes": len(graph["nodes"]), "edges": len(graph["edges"])})

        _set_stage(run_id, "persist")
        await emit("stage_start", stage="persist")
        await stage_persist(ws_id, run_id, graph)
        await emit("stage_done", stage="persist", summary={})

        _set_stage(run_id, "persist", "done")
        await emit("done", run_id=run_id,
                   node_count=len(graph["nodes"]), edge_count=len(graph["edges"]))

    except Exception as e:
        logger.exception(f"Pipeline run {run_id} failed")
        err_msg = str(e)
        current_stage = ""
        try:
            with Session(_eng()) as s:
                r = s.get(ResearchRun, run_id)
                if r is not None:
                    current_stage = r.current_stage or ""
                    r.status = "failed"
                    r.error_message = err_msg
                    s.commit()
        except Exception:
            logger.exception("Failed to persist failed-state for run %s", run_id)
        try:
            await emit("error", message=err_msg, stage=current_stage)
        except Exception:
            logger.exception("Failed to emit error event for run %s", run_id)
    finally:
        await bus.close()
        drop_event_bus(run_id)
