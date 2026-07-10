"""
多路召回 RRF 融合排序。

RRF(d) = Σ_s w_s / (k + rank_s(d))
默认 k=60；按 canonical_key 合并候选，保留多源 provenance。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...models.recall import RecallCandidate, RecallSource

# 融合通道（与 provider RecallSource 解耦，支持 pdf_ref）
CHANNEL_PDF_REF = "pdf_ref"
CHANNEL_BACKWARD = "backward"
CHANNEL_FORWARD = "forward"
CHANNEL_RECOMMEND = "recommend"
CHANNEL_GRAPH_PPR = "graph_ppr"

DEFAULT_RRF_K = 60
DEFAULT_WEIGHTS: dict[str, float] = {
    CHANNEL_PDF_REF: 1.0,
    CHANNEL_BACKWARD: 1.0,
    CHANNEL_FORWARD: 0.9,
    CHANNEL_RECOMMEND: 0.85,
    CHANNEL_GRAPH_PPR: 0.8,
}

_SOURCE_TO_CHANNEL: dict[RecallSource, str] = {
    RecallSource.BACKWARD_REF: CHANNEL_BACKWARD,
    RecallSource.FORWARD_CIT: CHANNEL_FORWARD,
    RecallSource.RECOMMEND: CHANNEL_RECOMMEND,
}


def channel_for_candidate(cand: RecallCandidate) -> str:
    """从 provenance 或 source 推断融合通道。"""
    for tag in cand.provenance:
        if tag.startswith("channel:"):
            return tag.split(":", 1)[1]
    return _SOURCE_TO_CHANNEL.get(cand.source, cand.source.value)


def tag_channel(cand: RecallCandidate, channel: str) -> RecallCandidate:
    """为候选打上 channel provenance（不修改原对象）。"""
    prov = list(cand.provenance)
    channel_tag = f"channel:{channel}"
    if channel_tag not in prov:
        prov.append(channel_tag)
    return cand.model_copy(update={"provenance": prov})


@dataclass
class FusedCandidate:
    """按 canonical_key 合并后的融合候选。"""

    canonical_key: str
    representative: RecallCandidate
    rrf_score: float
    channels: list[str] = field(default_factory=list)
    provenance: list[str] = field(default_factory=list)
    channel_ranks: dict[str, int] = field(default_factory=dict)
    seed_paper_ids: list[str] = field(default_factory=list)
    rerank_score: float | None = None


def _merge_provenance(*lists: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for lst in lists:
        for item in lst:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
    return out


def _pick_representative(existing: RecallCandidate, new: RecallCandidate) -> RecallCandidate:
    """合并时选信息更全的代表候选。"""
    score = lambda c: sum(  # noqa: E731
        1
        for f in (c.doi, c.arxiv_id, c.openalex_id, c.s2_id, c.abstract, c.authors, c.pdf_url)
        if f
    )
    return new if score(new) > score(existing) else existing


def fuse_candidates(
    pools: dict[str, list[RecallCandidate]],
    *,
    k: int = DEFAULT_RRF_K,
    weights: dict[str, float] | None = None,
) -> list[FusedCandidate]:
    """
    对多通道候选做 RRF 融合。

    pools: {channel: [RecallCandidate, ...]}，各通道内顺序即 rank（1-based）。
    遍历通道按字母序，保证输入顺序稳定。
    """
    wmap = {**DEFAULT_WEIGHTS, **(weights or {})}

    # channel -> canonical_key -> (rank, candidate)
    ranked: dict[str, dict[str, tuple[int, RecallCandidate]]] = {}

    for channel in sorted(pools.keys()):
        cands = pools.get(channel) or []
        seen_in_channel: set[str] = set()
        rank = 0
        for cand in cands:
            if not cand.canonical_key:
                continue
            key = cand.canonical_key
            if key in seen_in_channel:
                continue
            seen_in_channel.add(key)
            rank += 1
            ranked.setdefault(channel, {})[key] = (rank, cand)

    # 聚合 RRF 分
    scores: dict[str, float] = {}
    meta: dict[str, dict[str, Any]] = {}

    for channel in sorted(ranked.keys()):
        weight = wmap.get(channel, 1.0)
        for key, (rank, cand) in ranked[channel].items():
            scores[key] = scores.get(key, 0.0) + weight / (k + rank)
            entry = meta.setdefault(
                key,
                {
                    "representative": cand,
                    "channels": [],
                    "provenance": [],
                    "channel_ranks": {},
                    "seed_paper_ids": [],
                },
            )
            entry["channels"].append(channel)
            entry["channel_ranks"][channel] = rank
            entry["provenance"] = _merge_provenance(entry["provenance"], cand.provenance)
            if cand.seed_paper_id and cand.seed_paper_id not in entry["seed_paper_ids"]:
                entry["seed_paper_ids"].append(cand.seed_paper_id)
            entry["representative"] = _pick_representative(entry["representative"], cand)

    fused: list[FusedCandidate] = []
    for key in sorted(scores.keys(), key=lambda x: (-scores[x], x)):
        m = meta[key]
        fused.append(
            FusedCandidate(
                canonical_key=key,
                representative=m["representative"],
                rrf_score=scores[key],
                channels=m["channels"],
                provenance=m["provenance"],
                channel_ranks=m["channel_ranks"],
                seed_paper_ids=m["seed_paper_ids"],
            )
        )
    return fused
