"""
多路召回编排 — 对每个 seed 并行拉取 backward / forward / recommend。

单源失败不阻断；输出每源 hit_count / fail / latency。
pdf_reference 由 expand_arxiv 解析后注入，不在此模块联网。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ...models.paper import Paper
from ...models.recall import RecallCandidate, RecallSource, candidate_from_legacy_reference
from .fusion_ranker import (
    CHANNEL_BACKWARD,
    CHANNEL_FORWARD,
    CHANNEL_RECOMMEND,
    tag_channel,
)
from .providers.base import ResearchProvider

logger = logging.getLogger(__name__)

ALL_ONLINE_CHANNELS = (CHANNEL_BACKWARD, CHANNEL_FORWARD, CHANNEL_RECOMMEND)


@dataclass
class SourceRecallStats:
    """单源召回统计（供 SSE / log）。"""

    channel: str
    seed_id: int | None = None
    hit_count: int = 0
    failed: bool = False
    error: str | None = None
    latency_ms: float = 0.0


@dataclass
class SeedRecallResult:
    seed_id: int
    ext_paper_id: str | None
    pools: dict[str, list[RecallCandidate]] = field(default_factory=dict)
    stats: list[SourceRecallStats] = field(default_factory=list)


def _enabled_channels(config: dict) -> set[str]:
    recall_cfg = (config or {}).get("recall") or {}
    enabled = recall_cfg.get("enabled_sources")
    if enabled is None:
        return set(ALL_ONLINE_CHANNELS)
    return {str(c) for c in enabled if str(c) in ALL_ONLINE_CHANNELS}


def _candidate_limit(config: dict) -> int:
    recall_cfg = (config or {}).get("recall") or {}
    return int(recall_cfg.get("candidate_limit", 50))


async def _timed_call(channel: str, coro, *, seed_id: int | None = None) -> tuple[list[RecallCandidate], SourceRecallStats]:
    t0 = time.perf_counter()
    stats = SourceRecallStats(channel=channel, seed_id=seed_id)
    try:
        result = await coro
        cands = list(result or [])
        stats.hit_count = len(cands)
        return cands, stats
    except Exception as e:
        stats.failed = True
        stats.error = str(e)
        logger.warning("recall channel=%s seed=%s failed: %s", channel, seed_id, e)
        return [], stats
    finally:
        stats.latency_ms = round((time.perf_counter() - t0) * 1000, 2)


async def _recall_backward(
    provider: ResearchProvider,
    ext_paper_id: str,
    *,
    seed: Paper,
    limit: int,
) -> list[RecallCandidate]:
    raw_refs = await provider.get_paper_references(ext_paper_id, limit=limit)
    out: list[RecallCandidate] = []
    for rank, raw in enumerate(raw_refs):
        cand = candidate_from_legacy_reference(
            raw,
            source=RecallSource.BACKWARD_REF,
            source_rank=rank,
            provider_name=provider.name,
            seed_paper_id=str(seed.id),
            seed_relation="references",
        )
        if cand:
            out.append(tag_channel(cand, CHANNEL_BACKWARD))
    return out


async def _recall_forward(
    provider: ResearchProvider,
    ext_paper_id: str,
    *,
    seed: Paper,
    limit: int,
) -> list[RecallCandidate]:
    cands = await provider.get_paper_citations(ext_paper_id, limit=limit)
    return [
        tag_channel(
            c.model_copy(
                update={
                    "seed_paper_id": c.seed_paper_id or str(seed.id),
                    "seed_relation": c.seed_relation or "cited_by",
                }
            ),
            CHANNEL_FORWARD,
        )
        for c in cands
        if c.canonical_key
    ]


async def _recall_recommend(
    provider: ResearchProvider,
    ext_paper_id: str,
    *,
    seed: Paper,
    limit: int,
) -> list[RecallCandidate]:
    cands = await provider.get_recommendations(ext_paper_id, limit=limit)
    return [
        tag_channel(
            c.model_copy(
                update={
                    "seed_paper_id": c.seed_paper_id or str(seed.id),
                    "seed_relation": c.seed_relation or "related",
                }
            ),
            CHANNEL_RECOMMEND,
        )
        for c in cands
        if c.canonical_key
    ]


async def recall_for_seed(
    provider: ResearchProvider,
    seed: Paper,
    config: dict,
) -> SeedRecallResult:
    """对单个 seed 并行召回 backward / forward / recommend。"""
    enabled = _enabled_channels(config)
    limit = _candidate_limit(config)
    result = SeedRecallResult(seed_id=seed.id, ext_paper_id=None)

    if provider.name == "local" or not enabled:
        return result

    ext_id = await provider.find_paper_id(
        title=seed.title or seed.filename or "",
        doi=seed.doi or "",
        arxiv_id=seed.arxiv_id or "",
    )
    result.ext_paper_id = ext_id
    if not ext_id:
        logger.info("recall[seed=%s]: provider could not resolve paper id", seed.id)
        return result

    tasks: dict[str, Any] = {}
    if CHANNEL_BACKWARD in enabled:
        tasks[CHANNEL_BACKWARD] = _recall_backward(provider, ext_id, seed=seed, limit=limit)
    if CHANNEL_FORWARD in enabled:
        tasks[CHANNEL_FORWARD] = _recall_forward(provider, ext_id, seed=seed, limit=limit)
    if CHANNEL_RECOMMEND in enabled:
        tasks[CHANNEL_RECOMMEND] = _recall_recommend(provider, ext_id, seed=seed, limit=limit)

    if not tasks:
        return result

    keys = sorted(tasks.keys())
    gathered = await asyncio.gather(
        *[_timed_call(ch, tasks[ch], seed_id=seed.id) for ch in keys],
        return_exceptions=True,
    )

    for ch, item in zip(keys, gathered):
        if isinstance(item, Exception):
            stat = SourceRecallStats(channel=ch, seed_id=seed.id, failed=True, error=str(item))
            result.stats.append(stat)
            result.pools[ch] = []
            continue
        cands, stat = item
        result.stats.append(stat)
        result.pools[ch] = cands

    return result


async def recall_for_seeds(
    provider: ResearchProvider,
    seeds: list[Paper],
    config: dict,
) -> tuple[dict[str, list[RecallCandidate]], list[SourceRecallStats]]:
    """
    对多个 seed 并行召回，按通道合并候选（保持各 seed 内顺序）。

    返回 (pools, stats)。
    """
    if not seeds:
        return {}, []

    seed_results = await asyncio.gather(
        *[recall_for_seed(provider, seed, config) for seed in seeds],
        return_exceptions=True,
    )

    merged: dict[str, list[RecallCandidate]] = {
        CHANNEL_BACKWARD: [],
        CHANNEL_FORWARD: [],
        CHANNEL_RECOMMEND: [],
    }
    all_stats: list[SourceRecallStats] = []

    for seed, res in zip(seeds, seed_results):
        if isinstance(res, Exception):
            logger.warning("recall_for_seed failed seed=%s: %s", seed.id, res)
            for ch in ALL_ONLINE_CHANNELS:
                all_stats.append(
                    SourceRecallStats(channel=ch, seed_id=seed.id, failed=True, error=str(res))
                )
            continue
        all_stats.extend(res.stats)
        for ch, cands in res.pools.items():
            merged.setdefault(ch, []).extend(cands)

    # 去掉空通道
    merged = {k: v for k, v in merged.items() if v}
    return merged, all_stats


def aggregate_stats(stats: list[SourceRecallStats]) -> list[dict]:
    """按 channel 聚合统计，供 SSE summary。"""
    buckets: dict[str, dict] = {}
    for s in stats:
        b = buckets.setdefault(
            s.channel,
            {"channel": s.channel, "hit_count": 0, "failed": 0, "latency_ms": 0.0, "errors": []},
        )
        b["hit_count"] += s.hit_count
        if s.failed:
            b["failed"] += 1
            if s.error and s.error not in b["errors"]:
                b["errors"].append(s.error)
        b["latency_ms"] = max(b["latency_ms"], s.latency_ms)
    return list(buckets.values())
