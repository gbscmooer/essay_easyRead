"""
Graph sparsification with the original disparity filter and display bridges.

Serrano, Boguna & Vespignani (2009), PNAS, doi:10.1073/pnas.0808904106.
At endpoint i, p_ij = w_ij / sum_j(w_ij) and significance is
(1 - p_ij) ** (degree_i - 1) < alpha. Undirected edges use the OR rule.
Degree-one edges are retained by convention; they have no local null test.

No baseline subtraction or target-degree tuning is performed. A uniform graph
may have no significant edges. Optional maximum-spanning-forest edges connect
the display only and are returned separately as bridge keys, never evidence
of significance. Mutual kNN is a separate neighborhood construction, not a
fallback hidden inside the statistical filter.
"""

from __future__ import annotations

import math
import warnings
from collections import defaultdict

import networkx as nx


def _build_adj(
    pairs: list[tuple[int, int, float]], min_sim: float
) -> tuple[dict[int, dict[int, float]], dict[tuple[int, int], float]]:
    """Canonical positive finite edges; duplicate/reversed pairs use the maximum."""
    if not math.isfinite(min_sim) or min_sim < 0:
        raise ValueError("min_sim must be finite and nonnegative")
    adj: dict[int, dict[int, float]] = defaultdict(dict)
    sim_of: dict[tuple[int, int], float] = {}
    for a, b, s in pairs:
        if a == b or not math.isfinite(s) or s <= 0 or s < min_sim:
            continue
        key = (min(a, b), max(a, b))
        if s <= sim_of.get(key, 0.0):
            continue
        sim_of[key] = s
        adj[a][b] = s
        adj[b][a] = s
    return adj, sim_of


def _disparity_keep(adj: dict[int, dict[int, float]], alpha: float) -> set[tuple[int, int]]:
    keep: set[tuple[int, int]] = set()
    for i, nbrs in adj.items():
        degree = len(nbrs)
        if degree == 1:
            keep.update((min(i, j), max(i, j)) for j in nbrs)
            continue
        if not nbrs:
            continue
        # Scaling all incident weights by their maximum preserves the test and
        # avoids overflow when input weights are large but finite.
        peak = max(nbrs.values())
        strength = math.fsum(w / peak for w in nbrs.values())
        for j, weight in nbrs.items():
            proportion = (weight / peak) / strength
            if (1.0 - proportion) ** (degree - 1) < alpha:
                keep.add((min(i, j), max(i, j)))
    return keep


def _ensure_connected(
    keep: set[tuple[int, int]],
    sim_of: dict[tuple[int, int], float],
    nodes: list[int],
) -> set[tuple[int, int]]:
    """Kruskal on the original edges, with the retained components pre-unioned."""
    components = nx.utils.UnionFind(nodes)
    for a, b in sorted(keep):
        components.union(a, b)
    for (a, b), _ in sorted(sim_of.items(), key=lambda item: (-item[1], item[0])):
        if components[a] != components[b]:
            components.union(a, b)
            keep.add((a, b))
    return keep


def extract_backbone_with_bridges(
    pairs: list[tuple[int, int, float]],
    *,
    target_degree: float | None = None,
    alpha: float = 0.05,
    min_sim: float = 0.0,
    ensure_connected: bool = True,
    baseline: str | float | None = None,
) -> tuple[list[tuple[int, int, float]], set[tuple[int, int]]]:
    """Return retained edges plus explicitly flagged display bridge keys.

    alpha must lie in (0,1). min_sim is an optional prefilter; the default
    includes every positive edge in the null model. target_degree and baseline
    remain accepted for caller compatibility, but are ignored with a warning.
    Returned weights are original scores. A bridge is never a significant edge.
    """
    if not math.isfinite(alpha) or not 0 < alpha < 1:
        raise ValueError("alpha must lie strictly between 0 and 1")
    if target_degree is not None or baseline is not None:
        warnings.warn(
            "target_degree and baseline are ignored by the original disparity filter; "
            "use alpha for significance or mutual_knn for neighborhood construction",
            DeprecationWarning, stacklevel=2,
        )
    adj, sim_of = _build_adj(pairs, min_sim)
    keep = _disparity_keep(adj, alpha)
    bridges: set[tuple[int, int]] = set()
    if ensure_connected:
        connected = _ensure_connected(set(keep), sim_of, sorted(adj))
        bridges = connected - keep
        keep = connected
    return [(a, b, sim_of[(a, b)]) for a, b in sorted(keep)], bridges


def extract_backbone(
    pairs: list[tuple[int, int, float]],
    *,
    target_degree: float | None = None,
    alpha: float = 0.05,
    min_sim: float = 0.0,
    ensure_connected: bool = False,
    baseline: str | float | None = None,
) -> list[tuple[int, int, float]]:
    """Return statistical backbone edges only.

    This legacy list-only API cannot communicate bridge flags, so even callers
    passing ensure_connected=True receive only the retained backbone. Use
    extract_backbone_with_bridges for a connected display.
    """
    if ensure_connected:
        warnings.warn(
            "extract_backbone excludes display bridges; use extract_backbone_with_bridges",
            DeprecationWarning, stacklevel=2,
        )
    edges, _ = extract_backbone_with_bridges(
        pairs, target_degree=target_degree, alpha=alpha, min_sim=min_sim,
        ensure_connected=False, baseline=baseline,
    )
    return edges


def mutual_knn(
    pairs: list[tuple[int, int, float]], *, k: int = 8, min_sim: float = 0.0,
) -> list[tuple[int, int, float]]:
    """Undirected mutual k-nearest-neighbor graph, without forced connectivity.

    This is a standard neighborhood construction (von Luxburg, 2007, section
    2.2), not a significance test. At the kth boundary all exact ties are kept
    to avoid arbitrary paper-ID effects, so degree can exceed k. The caller
    must label these edges separately from disparity significance and display
    bridges. Positive rank-fusion scores are not evidence of semantic truth.
    """
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("k must be a positive integer")
    adj, sim_of = _build_adj(pairs, min_sim)
    neighbors: dict[int, set[int]] = {}
    for node, weights in adj.items():
        ordered = sorted(weights.values(), reverse=True)
        threshold = ordered[min(k, len(ordered)) - 1]
        neighbors[node] = {other for other, weight in weights.items() if weight >= threshold}
    return [
        (a, b, weight) for (a, b), weight in sorted(sim_of.items())
        if b in neighbors[a] and a in neighbors[b]
    ]


def add_display_bridges(
    pairs: list[tuple[int, int, float]],
    retained: list[tuple[int, int, float]],
) -> tuple[list[tuple[int, int, float]], set[tuple[int, int]]]:
    """Connect a separately selected analysis graph using original-edge bridges."""
    adj, sim_of = _build_adj(pairs, 0.0)
    keep = {(min(a, b), max(a, b)) for a, b, _ in retained}
    if not keep.issubset(sim_of):
        raise ValueError("Retained edges must exist in the original graph")
    connected = _ensure_connected(set(keep), sim_of, sorted(adj))
    return [(a, b, sim_of[a, b]) for a, b in sorted(connected)], connected - keep
