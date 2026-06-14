"""
Pipeline — 深度研究 6 阶段编排

collect → [expand] → chunk → embed → graph → persist
"""

import asyncio
import logging
from datetime import datetime, timezone
from sqlmodel import Session, create_engine, select

from ...config import settings
from ...models.research import ResearchRun
from ...models.schemas import StreamEvent
from .event_bus import get_event_bus, drop_event_bus
from .stages.collect import stage_collect
from .stages.expand import stage_expand
from .stages.expand_arxiv import stage_expand_arxiv
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


async def _auto_digest(papers, *, max_papers: int = 100) -> int:
    """对未精读且未速读、且有全文的论文批量「图谱速读」，写回 Paper.graph_digest。

    在 graph 阶段之前调用，使新生成的 facet 立刻参与 unified provider 的可解释关系。
    限并发 + 单轮上限，失败单篇跳过。返回成功速读的篇数。
    """
    from ..ai_config import get_analysis_config
    from ...models.analysis import AnalysisResult
    from ...models.paper import Paper
    from ..ai_analyzer import generate_graph_digest

    cfg = get_analysis_config()
    if not cfg.get("api_key"):
        logger.info("auto_digest: 未配置解读 API Key，跳过")
        return 0

    pids = [p.id for p in papers]
    if not pids:
        return 0
    with Session(_eng()) as s:
        analyzed = set(s.exec(select(AnalysisResult.paper_id).where(AnalysisResult.paper_id.in_(pids))).all())
        rows = s.exec(select(Paper).where(Paper.id.in_(pids))).all()
        todo = [
            {"id": p.id, "text": p.full_text, "title": p.title or p.filename or ""}
            for p in rows
            if p.id not in analyzed and not p.graph_digest and (p.full_text or "").strip()
        ]
    todo = todo[:max_papers]
    if not todo:
        return 0

    sem = asyncio.Semaphore(4)

    async def _one(item) -> int:
        """单篇速读并**立即落盘**（逐篇短事务）。被中断时已完成的不丢，再跑自动跳过。"""
        async with sem:
            try:
                d = await generate_graph_digest(
                    item["text"], cfg["api_key"], cfg.get("base_url", ""), cfg["model"], title=item["title"],
                )
            except Exception as e:
                logger.warning(f"auto_digest: paper {item['id']} failed: {e}")
                return 0
            try:
                with Session(_eng()) as s:
                    p = s.get(Paper, item["id"])
                    if p:
                        p.graph_digest = d
                        p.updated_at = datetime.now(timezone.utc)
                        s.add(p)
                        s.commit()
                return 1
            except Exception as e:
                logger.warning(f"auto_digest: persist paper {item['id']} failed: {e}")
                return 0

    n = sum(await asyncio.gather(*[_one(it) for it in todo]))
    logger.info(f"auto_digest: {n}/{len(todo)} 篇速读成功")
    return n


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

        seed_citation_contexts: dict[int, list[dict]] = {}

        if params.get("mode") == "online":
            _set_stage(run_id, "expand")
            await emit("stage_start", stage="expand")
            deep = max(1, int(params.get("deep", 1)))
            # 多跳扩展：seeds=papers 中的 L1 → L2 → L3 …
            # collect 已经把已有 L2+ 也带进 papers，但扩展第一跳的 seed 必须是 L1
            seeds_this_hop = [p for p in papers if (getattr(p, "level", 1) or 1) == 1]
            all_expanded = []
            seen_ids = {p.id for p in papers}
            for hop in range(deep):
                next_level = 2 + hop  # 第 1 跳 → level=2，第 2 跳 → level=3
                new_papers = await stage_expand(
                    ws_id, seeds_this_hop, params,
                    on_paper_added=emit, next_level=next_level,
                )
                if not new_papers:
                    break
                # 去重：expand 现在也会回灌"已存在"的引用论文，避免在 papers 里重复
                fresh = [p for p in new_papers if p.id not in seen_ids]
                seen_ids.update(p.id for p in fresh)
                all_expanded.extend(fresh)
                seeds_this_hop = fresh
            papers = papers + all_expanded
            _set_stage(run_id, "expand", "running", expanded_count=len(all_expanded))
            await emit("stage_done", stage="expand",
                       summary={"total": len(papers), "expanded": len(all_expanded), "deep": deep})

            # arXiv 引用列表扩展（补充来源，与 S2/OpenAlex 互补）
            from ..ai_config import get_arxiv_expand_config
            arxiv_cfg = get_arxiv_expand_config()
            logger.info(f"arxiv_expand enabled={arxiv_cfg.get('enabled', True)}")
            if arxiv_cfg.get("enabled", True):
                try:
                    await emit("stage_start", stage="expand_arxiv")
                    l1_seeds = [p for p in papers if (getattr(p, "level", 1) or 1) == 1]
                    logger.info(f"arxiv_expand: l1_seeds={len(l1_seeds)}")
                    arxiv_papers, arxiv_contexts = await stage_expand_arxiv(ws_id, l1_seeds, emit, run_params=params)
                    seed_citation_contexts = arxiv_contexts
                    fresh_arxiv = [p for p in arxiv_papers if p.id not in seen_ids]
                    seen_ids.update(p.id for p in fresh_arxiv)
                    all_expanded.extend(fresh_arxiv)
                    papers = papers + fresh_arxiv
                    await emit("stage_done", stage="expand_arxiv",
                               summary={"arxiv_added": len(fresh_arxiv), "total": len(papers)})
                except Exception as arxiv_err:
                    logger.exception(f"arxiv_expand stage failed (non-fatal): {arxiv_err}")
                    await emit("stage_done", stage="expand_arxiv",
                               summary={"arxiv_added": 0, "total": len(papers), "error": str(arxiv_err)})

        _set_stage(run_id, "chunk")
        await emit("stage_start", stage="chunk")
        chunks = await stage_chunk(papers, citation_contexts=seed_citation_contexts)
        _set_stage(run_id, "chunk", "running", chunk_count=len(chunks))
        await emit("stage_done", stage="chunk", summary={"count": len(chunks)})

        _set_stage(run_id, "embed", embed_total=len(chunks))
        await emit("stage_start", stage="embed")
        async for done in stage_embed(ws_id, chunks, rebuild=params.get("rebuild", False)):
            _set_stage(run_id, "embed", embed_done=done, embed_total=len(chunks))
            await emit("embed_progress", done=done, total=len(chunks))
        await emit("stage_done", stage="embed", summary={"total": len(chunks)})

        # 5.5) 图谱速读：对未精读/未速读的论文做轻量解读，让叶子也能参与可解释 facet 关系。
        # 由 research.auto_digest 开关控制（默认开）。失败不影响图谱构建。
        from ..ai_config import load_config
        if (load_config().get("research", {}) or {}).get("auto_digest", True):
            _set_stage(run_id, "digest")
            await emit("stage_start", stage="digest")
            try:
                n_dig = await _auto_digest(papers)
                await emit("stage_done", stage="digest", summary={"digested": n_dig})
            except Exception as de:
                logger.warning(f"auto_digest failed (non-fatal): {de}")
                await emit("stage_done", stage="digest", summary={"digested": 0, "error": str(de)})

        _set_stage(run_id, "graph")
        await emit("stage_start", stage="graph")
        graph = await stage_graph(
            ws_id, papers,
            similarity_threshold=params.get("similarity_threshold", 0.78),
            enable_shared_method=params.get("enable_shared_method", False),
            backbone_alpha=float(params.get("backbone_alpha", 0.25)),
            citation_contexts=seed_citation_contexts,
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

        # 本轮新下载的联网扩展论文也补索引（提取正文 full_text），使其可被论文库全局搜索命中
        try:
            import asyncio as _asyncio
            from ..auto_indexer import reindex_missing
            _asyncio.create_task(reindex_missing())
        except Exception:
            logger.warning("auto_index: 研究运行后触发补索引失败", exc_info=True)

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
