"""Information-retrieval metrics — pure functions, no I/O."""

from __future__ import annotations

import math
from typing import Hashable, Mapping, Sequence


def _relevance_at_rank(
    ranked: Sequence[Hashable],
    relevant: set[Hashable],
    k: int,
) -> list[int]:
    return [1 if doc in relevant else 0 for doc in ranked[:k]]


def precision_at_k(
    ranked: Sequence[Hashable],
    relevant: set[Hashable],
    k: int,
) -> float:
    """Precision@K = |relevant ∩ top-K| / K."""
    if k <= 0:
        return 0.0
    hits = sum(_relevance_at_rank(ranked, relevant, k))
    return hits / k


def recall_at_k(
    ranked: Sequence[Hashable],
    relevant: set[Hashable],
    k: int,
) -> float:
    """Recall@K = |relevant ∩ top-K| / |relevant|."""
    if not relevant or k <= 0:
        return 0.0
    hits = sum(_relevance_at_rank(ranked, relevant, k))
    return hits / len(relevant)


def dcg_at_k(
    ranked: Sequence[Hashable],
    relevant: set[Hashable],
    k: int,
    *,
    gains: Mapping[Hashable, float] | None = None,
) -> float:
    """Discounted cumulative gain at K."""
    total = 0.0
    for i, doc in enumerate(ranked[:k], start=1):
        if gains is not None:
            gain = gains.get(doc, 0.0)
        else:
            gain = 1.0 if doc in relevant else 0.0
        if gain:
            total += gain / math.log2(i + 1)
    return total


def ndcg_at_k(
    ranked: Sequence[Hashable],
    relevant: set[Hashable],
    k: int,
    *,
    gains: Mapping[Hashable, float] | None = None,
) -> float:
    """Normalized DCG@K."""
    if not relevant or k <= 0:
        return 0.0
    dcg = dcg_at_k(ranked, relevant, k, gains=gains)
    if gains is not None:
        ideal_order = sorted(
            gains.keys(),
            key=lambda doc: gains.get(doc, 0.0),
            reverse=True,
        )
    else:
        ideal_order = list(relevant)
    idcg = dcg_at_k(ideal_order, relevant, k, gains=gains)
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def mrr(
    ranked: Sequence[Hashable],
    relevant: set[Hashable],
) -> float:
    """Mean reciprocal rank for a single query (first relevant hit)."""
    if not relevant:
        return 0.0
    for i, doc in enumerate(ranked, start=1):
        if doc in relevant:
            return 1.0 / i
    return 0.0
