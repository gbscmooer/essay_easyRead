"""RAG ablation runners — vector / vector+rerank / hybrid (mock scores, offline)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.services.deep_research.hybrid_retriever import (
    CHANNEL_BM25,
    CHANNEL_DENSE,
    CHANNEL_GRAPH,
    fuse_ranked_pools,
)

from .base import RunnerResult

_PKG_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_FIXTURE = _PKG_ROOT / "fixtures" / "mock" / "rag_chunk_pools.json"


def _apply_mock_rerank(
    ranked: list[str],
    rerank_scores: dict[str, float],
    *,
    top_k: int,
) -> list[str]:
    """Sort by mock cross-encoder scores; unknown chunks keep pool order."""
    if not ranked:
        return []
    order = {cid: i for i, cid in enumerate(ranked)}
    sorted_ids = sorted(
        ranked,
        key=lambda cid: (-rerank_scores.get(cid, 0.0), order[cid]),
    )
    out: list[str] = []
    seen: set[str] = set()
    for cid in sorted_ids:
        if cid in seen:
            continue
        seen.add(cid)
        out.append(cid)
        if len(out) >= top_k:
            break
    return out


class _RagFixtureRunner:
    def __init__(self, fixture_path: Path | None = None):
        self.fixture_path = fixture_path or _DEFAULT_FIXTURE
        self._cache: dict[str, dict[str, Any]] | None = None

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._cache is None:
            with open(self.fixture_path, encoding="utf-8") as f:
                self._cache = json.load(f)
        return self._cache

    def _entry(self, query_id: str) -> dict[str, Any]:
        return self._load().get(query_id, {})


class RagVectorRunner(_RagFixtureRunner):
    name = "rag_vector"

    def run_query(
        self,
        query: dict[str, Any],
        *,
        top_k: int = 5,
        **_: Any,
    ) -> RunnerResult:
        query_id = str(query.get("query_id") or "")
        entry = self._entry(query_id)
        ranked = list(entry.get("dense") or [])[:top_k]
        return RunnerResult(
            seed_id=query_id,
            ranked_ids=ranked,
            metadata={"strategy": "dense_only", "fixture": str(self.fixture_path)},
        )


class RagVectorRerankRunner(_RagFixtureRunner):
    name = "rag_vector_rerank"

    def run_query(
        self,
        query: dict[str, Any],
        *,
        top_k: int = 5,
        **_: Any,
    ) -> RunnerResult:
        query_id = str(query.get("query_id") or "")
        entry = self._entry(query_id)
        dense = list(entry.get("dense") or [])
        rerank_scores = {str(k): float(v) for k, v in (entry.get("rerank_scores") or {}).items()}
        ranked = _apply_mock_rerank(dense, rerank_scores, top_k=top_k)
        return RunnerResult(
            seed_id=query_id,
            ranked_ids=ranked,
            metadata={"strategy": "dense_plus_mock_rerank", "fixture": str(self.fixture_path)},
        )


class RagHybridRunner(_RagFixtureRunner):
    name = "rag_hybrid"

    def run_query(
        self,
        query: dict[str, Any],
        *,
        top_k: int = 5,
        **_: Any,
    ) -> RunnerResult:
        query_id = str(query.get("query_id") or "")
        entry = self._entry(query_id)
        pools: dict[str, list[str]] = {}
        if entry.get("dense"):
            pools[CHANNEL_DENSE] = list(entry["dense"])
        if entry.get("bm25"):
            pools[CHANNEL_BM25] = list(entry["bm25"])
        if entry.get("graph"):
            pools[CHANNEL_GRAPH] = list(entry["graph"])

        fused = fuse_ranked_pools(pools)
        fused_ids = [cid for cid, _, _ in fused]
        rerank_scores = {str(k): float(v) for k, v in (entry.get("rerank_scores") or {}).items()}
        ranked = _apply_mock_rerank(fused_ids, rerank_scores, top_k=top_k)
        return RunnerResult(
            seed_id=query_id,
            ranked_ids=ranked,
            metadata={
                "strategy": "hybrid_rrf_plus_mock_rerank",
                "channels": sorted(pools.keys()),
                "fixture": str(self.fixture_path),
            },
        )
