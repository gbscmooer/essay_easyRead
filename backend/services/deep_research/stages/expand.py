"""
Stage 2 — expand

多路召回（backward / forward / recommend / pdf_ref）+ RRF 融合，
再经 find_or_register + download_for_paper 登记扩展论文。

不再按 citation_count 单源排序；PDF 仅影响 download_status，不作召回过滤。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Awaitable

from sqlmodel import Session, create_engine

from ....config import settings
from ....models.paper import Paper
from ...ai_config import get_reranker_config, get_research_config
from ...paper_download import download_for_paper
from ...paper_identity import PaperWork, find_or_register
from ..fusion_ranker import CHANNEL_GRAPH_PPR, FusedCandidate, fuse_candidates
from ..graph_retriever import GraphRetrieverConfig, parse_paper_node_id, retrieve_as_candidates
from ..providers import get_provider
from ..recall_orchestrator import SourceRecallStats, aggregate_stats, recall_for_seeds
from ..reference_store import upsert_references
from ..reranker import rerank

logger = logging.getLogger(__name__)

_engine = None
SEM = asyncio.Semaphore(3)


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})
    return _engine


def _discovery_v2_enabled(params: dict) -> bool:
    """Feature flag: multi-source RRF fusion vs legacy backward+citation sort."""
    if "discovery_v2" in params:
        return bool(params["discovery_v2"])
    return bool(get_research_config().get("discovery_v2", True))


def _recall_config(params: dict) -> dict:
    """合并 run params 与 ai_config research 段。"""
    cfg = dict(get_research_config())
    recall = dict(cfg.get("recall") or {})
    if params.get("recall_enabled_sources") is not None:
        recall["enabled_sources"] = params["recall_enabled_sources"]
    if params.get("recall_candidate_limit") is not None:
        recall["candidate_limit"] = params["recall_candidate_limit"]
    cfg["recall"] = recall
    return cfg


async def collect_online_recall(
    seeds: list[Paper],
    params: dict,
) -> tuple[dict[str, list], list[SourceRecallStats]]:
    """供 pipeline 与 PDF 引用解析并行预取在线召回池。"""
    config = _recall_config(params)
    provider = get_provider(config)
    if provider.name == "local":
        return {}, []
    return await recall_for_seeds(provider, seeds, config)


def _fused_to_work(fused: FusedCandidate, parent: Paper, next_level: int) -> PaperWork:
    c = fused.representative
    legacy_ext = None
    if c.doi:
        legacy_ext = f"DOI:{c.doi}"
    elif not legacy_ext and c.arxiv_id:
        legacy_ext = f"arxiv:{c.arxiv_id}"
    return PaperWork(
        title=c.title,
        venue=c.extra.get("venue_name"),
        bibliography={**c.extra, "providers": [p.split(":", 1)[1] for p in fused.provenance if p.startswith("provider:")]},
        authors=c.authors,
        year=c.year,
        doi=c.doi,
        arxiv_id=c.arxiv_id,
        openalex_id=c.openalex_id,
        s2_id=c.s2_id,
        external_id=legacy_ext,
        url=c.pdf_url,
        source="expanded",
        level=next_level,
        parent_paper_id=parent.id,
        filename=f"{(c.title or 'untitled')[:60]}.pdf",
        abstract=c.abstract,
        citation_count=c.citation_count,
    )


async def _register_and_download(
    fused: FusedCandidate,
    citing_seed: Paper,
    ws_id: int,
    next_level: int,
) -> tuple[Paper | None, bool]:
    """find_or_register stub + 尝试下载 PDF。返回 (paper, is_new)。"""
    work = _fused_to_work(fused, citing_seed, next_level)

    with Session(_get_engine(), expire_on_commit=False) as s:
        paper, is_new = find_or_register(s, work, workspace_id=ws_id)

        upsert_references(
            s,
            citing_seed.id,
            [{
                "cited_paper_id": paper.id,
                "cited_doi": fused.representative.doi,
                "cited_arxiv_id": fused.representative.arxiv_id,
                "cited_title": fused.representative.title,
                "source": "recall_fusion",
                "confidence": min(1.0, fused.rrf_score),
            }],
        )

    async with SEM:
        with Session(_get_engine(), expire_on_commit=False) as s:
            path = await download_for_paper(s, paper.id)

    with Session(_get_engine(), expire_on_commit=False) as s:
        paper = s.get(Paper, paper.id)
    return paper, is_new


def _resolve_citing_seed(
    fused: FusedCandidate,
    seeds_by_id: dict[int, Paper],
    seeds: list[Paper],
) -> Paper:
    for sid in fused.seed_paper_ids:
        try:
            pid = int(sid)
            if pid in seeds_by_id:
                return seeds_by_id[pid]
        except (TypeError, ValueError):
            continue
    return seeds[0]


async def _rerank_fused_papers(
    fused_list: list[FusedCandidate],
    seeds: list[Paper],
    params: dict,
) -> list[FusedCandidate]:
    """可选论文级 cross-encoder：只重排 RRF Top-50，失败时保持原顺序。"""
    cfg = get_reranker_config()
    if not cfg.get("enabled", True) or len(fused_list) < 2:
        return fused_list

    head = fused_list[:50]
    intent = str(params.get("research_intent") or "").strip()
    if not intent:
        seed_titles = [str(seed.title or seed.filename or "").strip() for seed in seeds]
        intent = "相关工作：" + "；".join(title for title in seed_titles if title)
    documents = [
        "\n".join(
            part
            for part in (
                fused.representative.title,
                fused.representative.abstract or "",
            )
            if part
        )
        for fused in head
    ]
    ranked = await rerank(intent, documents, top_n=len(head))
    if len(ranked) != len(head):
        return fused_list

    reranked: list[FusedCandidate] = []
    seen: set[int] = set()
    for item in ranked:
        index = int(item.get("index", -1))
        if index < 0 or index >= len(head) or index in seen:
            return fused_list
        seen.add(index)
        candidate = head[index]
        candidate.rerank_score = float(item.get("score", 0.0))
        reranked.append(candidate)
    return reranked + fused_list[len(head):]


def _legacy_ref_to_work(ref: dict, parent: Paper, next_level: int) -> PaperWork:
    ext_id = ref.get("ext_id") or ref.get("s2_id") or ""
    return PaperWork(
        title=ref.get("title") or "untitled",
        authors=ref.get("authors"),
        year=ref.get("year"),
        doi=ref.get("doi"),
        arxiv_id=ref.get("arxiv_id"),
        openalex_id=ref.get("openalex_id") if str(ext_id).upper().startswith("W") else None,
        s2_id=ext_id if ext_id and not str(ext_id).upper().startswith("W") else ref.get("s2_id"),
        external_id=str(ext_id) if ext_id else None,
        url=ref.get("pdf_url"),
        source="expanded",
        level=next_level,
        parent_paper_id=parent.id,
        filename=f"{(ref.get('title') or 'untitled')[:60]}.pdf",
        abstract=ref.get("abstract"),
        citation_count=ref.get("citation_count"),
    )


def _rank_legacy_refs(refs: list[dict], *, top_k: int) -> list[str]:
    """v1 expand: keep pdf_url, sort by citation_count desc."""
    filtered = [r for r in refs if r.get("pdf_url")]
    filtered.sort(key=lambda r: r.get("citation_count") or 0, reverse=True)
    ranked: list[str] = []
    for ref in filtered[:top_k]:
        ext_id = ref.get("ext_id") or ref.get("doi") or ref.get("arxiv_id")
        if ext_id:
            ranked.append(str(ext_id))
    return ranked


async def _register_legacy_ref(
    ref: dict,
    citing_seed: Paper,
    ws_id: int,
    next_level: int,
) -> tuple[Paper | None, bool]:
    work = _legacy_ref_to_work(ref, citing_seed, next_level)
    with Session(_get_engine(), expire_on_commit=False) as s:
        paper, is_new = find_or_register(s, work, workspace_id=ws_id)
        upsert_references(
            s,
            citing_seed.id,
            [{
                "cited_paper_id": paper.id,
                "cited_doi": ref.get("doi"),
                "cited_arxiv_id": ref.get("arxiv_id"),
                "cited_title": ref.get("title"),
                "source": "legacy_expand",
                "confidence": float(ref.get("citation_count") or 0),
            }],
        )

    async with SEM:
        with Session(_get_engine(), expire_on_commit=False) as s:
            path = await download_for_paper(s, paper.id)

    with Session(_get_engine(), expire_on_commit=False) as s:
        paper = s.get(Paper, paper.id)
    return paper, is_new


async def stage_expand_legacy(
    ws_id: int,
    seeds: list,
    params: dict,
    on_paper_added: Callable[..., Awaitable[None]],
    *,
    next_level: int = 2,
) -> tuple[list[Paper], list[dict]]:
    """v1 单源 backward 引用 + pdf_url 过滤 + citation_count 排序。"""
    top_k = int(params.get("expand_top_k", 5))
    total_limit = int(params.get("expand_total_limit", 50))
    config = _recall_config(params)
    provider = get_provider(config)
    logger.info("expand stage legacy (level→%s) provider=%s", next_level, provider.name)

    if provider.name == "local":
        return [], []

    expanded: list[Paper] = []
    expanded_count = 0
    seen_paper_ids: set[int] = set()
    stats: list[dict] = []

    for seed in seeds:
        ext_id = await provider.find_paper_id(
            title=seed.title or seed.filename or "",
            doi=getattr(seed, "doi", None) or "",
            arxiv_id=getattr(seed, "arxiv_id", None) or "",
        )
        if not ext_id:
            continue
        try:
            refs = await provider.get_paper_references(ext_id, limit=50)
        except Exception as e:
            logger.warning("legacy expand backward failed seed=%s: %s", seed.id, e)
            stats.append({"channel": "backward", "hit_count": 0, "failed": 1, "error": str(e)})
            continue

        stats.append({"channel": "backward", "hit_count": len(refs), "failed": 0})
        ranked_ids = _rank_legacy_refs(refs, top_k=top_k)
        ref_by_ext: dict[str, dict] = {}
        for ref in refs:
            rid = ref.get("ext_id") or ref.get("doi") or ref.get("arxiv_id")
            if rid:
                ref_by_ext[str(rid)] = ref

        per_seed = 0
        for rid in ranked_ids:
            if expanded_count >= total_limit or per_seed >= top_k:
                break
            ref = ref_by_ext.get(rid)
            if not ref:
                continue
            paper, is_new = await _register_legacy_ref(ref, seed, ws_id, next_level)
            if not paper or paper.id in seen_paper_ids:
                continue
            seen_paper_ids.add(paper.id)
            expanded.append(paper)
            per_seed += 1
            if is_new:
                expanded_count += 1
            await on_paper_added(
                "paper_added",
                paper_id=paper.id,
                title=paper.title,
                level=paper.level,
                source_paper_id=seed.id,
                total_expanded=expanded_count,
                is_new=is_new,
                legacy=True,
            )

    return expanded, stats


async def stage_expand(
    ws_id: int,
    seeds: list,
    params: dict,
    on_paper_added: Callable[..., Awaitable[None]],
    *,
    next_level: int = 2,
    pdf_pools: dict[str, list] | None = None,
    graph: dict | None = None,
    online_recall: tuple[dict[str, list], list[SourceRecallStats]] | None = None,
) -> tuple[list[Paper], list[dict]]:
    """
    多路召回 + RRF 融合后登记扩展论文。

    pdf_pools: 可选 pdf_ref 通道候选（来自 expand_arxiv 解析）。
    返回 (expanded_papers, recall_stats_summary)。
    """
    if not _discovery_v2_enabled(params):
        return await stage_expand_legacy(
            ws_id, seeds, params, on_paper_added, next_level=next_level,
        )

    top_k = int(params.get("expand_top_k", 5))
    total_limit = int(params.get("expand_total_limit", 50))

    config = _recall_config(params)
    provider = get_provider(config)
    logger.info("expand stage (level→%s) provider=%s recall fusion", next_level, provider.name)

    if provider.name == "local" and not pdf_pools and not graph:
        return [], []

    if online_recall is None:
        online_pools, stats = await recall_for_seeds(provider, seeds, config)
    else:
        online_pools, stats = online_recall

    pools: dict[str, list] = {}
    pools.update(online_pools)
    if pdf_pools:
        for ch, cands in pdf_pools.items():
            pools.setdefault(ch, []).extend(cands)

    recall_cfg = config.get("recall") or {}
    enabled_sources = set(recall_cfg.get("enabled_sources") or [])
    if graph and CHANNEL_GRAPH_PPR in enabled_sources:
        graph_started = time.perf_counter()
        try:
            graph_paper_ids = {
                pid
                for node in (graph.get("nodes") or [])
                if (pid := parse_paper_node_id(node.get("id"))) is not None
            }
            with Session(_get_engine()) as session:
                papers_by_id = {
                    pid: paper
                    for pid in graph_paper_ids
                    if (paper := session.get(Paper, pid)) is not None
                }
            graph_candidates = retrieve_as_candidates(
                graph,
                [seed.id for seed in seeds],
                papers_by_id,
                config=GraphRetrieverConfig(
                    top_k=max(1, int(recall_cfg.get("candidate_limit", 50))),
                ),
            )
            if graph_candidates:
                pools[CHANNEL_GRAPH_PPR] = graph_candidates
            stats.append(SourceRecallStats(
                channel=CHANNEL_GRAPH_PPR,
                hit_count=len(graph_candidates),
                latency_ms=round((time.perf_counter() - graph_started) * 1000, 2),
            ))
        except Exception as exc:
            logger.warning("graph_ppr discovery recall failed: %s", exc)
            stats.append(SourceRecallStats(
                channel=CHANNEL_GRAPH_PPR,
                failed=True,
                error=str(exc),
                latency_ms=round((time.perf_counter() - graph_started) * 1000, 2),
            ))

    fused_list = fuse_candidates(
        pools,
        k=int(recall_cfg.get("rrf_k", 60)),
        weights=recall_cfg.get("weights"),
    )
    candidate_limit = max(1, int(recall_cfg.get("candidate_limit", 50)))
    fused_list = await _rerank_fused_papers(
        fused_list[:candidate_limit],
        seeds,
        params,
    )

    logger.info(
        "expand fusion: pools=%s fused=%d stats=%s",
        {k: len(v) for k, v in pools.items()},
        len(fused_list),
        aggregate_stats(stats),
    )

    expanded: list[Paper] = []
    expanded_count = 0
    seen_paper_ids: set[int] = set()
    per_seed_count: dict[int, int] = {}
    seeds_by_id = {s.id: s for s in seeds}

    for fused in fused_list:
        if expanded_count >= total_limit:
            break
        citing = _resolve_citing_seed(fused, seeds_by_id, seeds)
        if per_seed_count.get(citing.id, 0) >= top_k:
            continue
        paper, is_new = await _register_and_download(fused, citing, ws_id, next_level)
        if not paper:
            continue
        if paper.id in seen_paper_ids:
            continue
        seen_paper_ids.add(paper.id)
        expanded.append(paper)
        per_seed_count[citing.id] = per_seed_count.get(citing.id, 0) + 1
        if is_new:
            expanded_count += 1
        await on_paper_added(
            "paper_added",
            paper_id=paper.id,
            title=paper.title,
            level=paper.level,
            source_paper_id=citing.id,
            total_expanded=expanded_count,
            is_new=is_new,
            rrf_score=round(fused.rrf_score, 6),
            channels=fused.channels,
            channel_ranks=fused.channel_ranks,
            fusion_contributions={
                channel: round(
                    float((recall_cfg.get("weights") or {}).get(channel, 1.0))
                    / (int(recall_cfg.get("rrf_k", 60)) + rank),
                    8,
                )
                for channel, rank in fused.channel_ranks.items()
            },
            rerank_score=fused.rerank_score,
        )

    return expanded, aggregate_stats(stats)
