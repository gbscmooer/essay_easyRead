"""Identity-quality metrics for discovery results — pure functions."""

from __future__ import annotations

from typing import Hashable, Sequence


def wrong_id_rate(id_pairs: Sequence[tuple[Hashable, Hashable]]) -> float:
    """Fraction of (predicted_id, expected_canonical_id) pairs that disagree."""
    if not id_pairs:
        return 0.0
    wrong = sum(1 for predicted, expected in id_pairs if predicted != expected)
    return wrong / len(id_pairs)


def duplicate_rate(ranked: Sequence[Hashable]) -> float:
    """Fraction of positions that repeat an earlier item in the ranked list."""
    if len(ranked) <= 1:
        return 0.0
    seen: set[Hashable] = set()
    duplicates = 0
    for item in ranked:
        if item in seen:
            duplicates += 1
        else:
            seen.add(item)
    return duplicates / len(ranked)
