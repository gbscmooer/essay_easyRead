"""Discovery v2 runner — mock multi-source RRF fusion (offline)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.models.recall import RecallSource, candidate_from_legacy_reference
from backend.services.deep_research.fusion_ranker import fuse_candidates, tag_channel

from .base import RunnerResult

_PKG_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_FIXTURE = _PKG_ROOT / "fixtures" / "mock" / "discovery_v2_pools.json"

_CHANNEL_SOURCE = {
    "backward": RecallSource.BACKWARD_REF,
    "forward": RecallSource.FORWARD_CIT,
    "recommend": RecallSource.RECOMMEND,
    "pdf_ref": RecallSource.BACKWARD_REF,
}


class DiscoveryV2Runner:
    """Offline replay of multi-channel recall + RRF fusion (discovery v2)."""

    name = "discovery_v2"

    def __init__(self, fixture_path: Path | None = None):
        self.fixture_path = fixture_path or _DEFAULT_FIXTURE
        self._cache: dict[str, dict[str, list[dict[str, Any]]]] | None = None

    def _load(self) -> dict[str, dict[str, list[dict[str, Any]]]]:
        if self._cache is None:
            with open(self.fixture_path, encoding="utf-8") as f:
                self._cache = json.load(f)
        return self._cache

    def run_seed(
        self,
        seed: dict[str, Any],
        corpus: list[dict[str, Any]],
        *,
        top_k: int = 10,
        **_: Any,
    ) -> RunnerResult:
        seed_id = str(seed.get("seed_id") or "")
        channel_refs = self._load().get(seed_id, {})
        pools: dict[str, list] = {}
        key_to_eval_id: dict[str, str] = {}

        for channel, refs in sorted(channel_refs.items()):
            if channel == "rerank_scores":
                continue
            cands = []
            for rank, ref in enumerate(refs):
                source = _CHANNEL_SOURCE.get(channel, RecallSource.BACKWARD_REF)
                cand = candidate_from_legacy_reference(
                    ref,
                    source=source,
                    source_rank=rank,
                    provider_name="mock",
                    seed_paper_id=seed_id,
                    extra_provenance=[f"channel:{channel}"],
                )
                if not cand:
                    continue
                eval_id = str(ref.get("ext_id") or ref.get("eval_id") or "")
                if eval_id and cand.canonical_key:
                    key_to_eval_id[cand.canonical_key] = eval_id
                cands.append(tag_channel(cand, channel))
            if cands:
                pools[channel] = cands

        fused = fuse_candidates(pools)
        ranked: list[str] = []
        seen: set[str] = set()
        for item in fused:
            eval_id = key_to_eval_id.get(item.canonical_key)
            if not eval_id or eval_id in seen:
                continue
            seen.add(eval_id)
            ranked.append(eval_id)
            if len(ranked) >= top_k:
                break

        return RunnerResult(
            seed_id=seed_id,
            ranked_ids=ranked,
            metadata={
                "fixture": str(self.fixture_path),
                "channels": sorted(pools.keys()),
                "fused_count": len(fused),
                "strategy": "rrf_multi_source",
            },
        )
