"""Disparity significance, display-only bridges, and mutual kNN contracts."""

import itertools

import networkx as nx
import pytest

from backend.services.deep_research.graphkit.backbone import (
    extract_backbone,
    extract_backbone_with_bridges,
    mutual_knn,
)


def _uniform(n=5, weight=1.0):
    return [(a, b, weight) for a, b in itertools.combinations(range(n), 2)]


def test_original_disparity_or_rule_matches_hand_calculation():
    # Node 0: degree=3, strength=10, p(0,1)=.8, alpha_01=.04.
    # Node 1: degree=3, strength=28, alpha_10=(20/28)^2 > .05.
    pairs = [(0, 1, 8.0), (0, 2, 1.0), (0, 3, 1.0),
             (1, 2, 10.0), (1, 3, 10.0), (2, 3, 10.0)]
    assert extract_backbone(pairs, alpha=0.05) == [(0, 1, 8.0)]
    assert extract_backbone(pairs, alpha=0.039) == []


def test_uniform_graph_is_not_made_significant():
    assert extract_backbone(_uniform()) == []
    assert extract_backbone(_uniform(3)) == []


def test_connectivity_edges_are_flagged_and_never_returned_by_analysis_api():
    pairs = _uniform()
    full, bridges = extract_backbone_with_bridges(pairs)
    assert len(full) == len(bridges) == 4
    assert bridges == {(a, b) for a, b, _ in full}
    assert nx.is_connected(nx.Graph([(a, b) for a, b, _ in full]))
    with pytest.warns(DeprecationWarning, match="excludes display bridges"):
        assert extract_backbone(pairs, ensure_connected=True) == []


def test_bridges_are_exactly_the_connectivity_only_edges():
    pairs = [(a, b, 100.0 if (a, b) in {(0, 1), (2, 3)} else 1.0)
             for a, b in itertools.combinations(range(5), 2)]
    full, bridges = extract_backbone_with_bridges(pairs)
    significant = {(a, b) for a, b, _ in extract_backbone(pairs)}
    assert significant
    assert bridges == {(a, b) for a, b, _ in full} - significant
    assert bridges.isdisjoint(significant)
    original = {(a, b): w for a, b, w in pairs}
    assert all(w == original[a, b] for a, b, w in full)


def test_legacy_density_and_baseline_are_ignored():
    pairs = _uniform()
    for target in (1, 8, 100):
        with pytest.warns(DeprecationWarning, match="ignored"):
            assert extract_backbone(pairs, target_degree=target, baseline="auto") == []


def test_positive_scale_invariance():
    pairs = [(0, 1, 8), (0, 2, 1), (0, 3, 1),
             (1, 2, 10), (1, 3, 10), (2, 3, 10)]
    expected = {(a, b) for a, b, _ in extract_backbone(pairs)}
    for scale in (1e-100, 1, 1e300):
        assert {(a, b) for a, b, _ in extract_backbone([(a, b, w * scale) for a, b, w in pairs])} == expected


def test_display_forest_is_deterministic_and_does_not_invent_edges():
    pairs = _uniform(4) + [(10, 11, 2)]
    full, bridges = extract_backbone_with_bridges(pairs)
    assert extract_backbone_with_bridges(list(reversed(pairs))) == (full, bridges)
    assert all((a, b, w) in pairs for a, b, w in full)
    assert nx.number_connected_components(nx.Graph([(a, b) for a, b, _ in full])) == 2


def test_leaf_convention_and_input_cleanup():
    assert extract_backbone([]) == []
    assert extract_backbone([(1, 1, 1)]) == []
    assert extract_backbone([(1, 2, 0), (2, 3, -1), (2, 4, float("nan")), (1, 4, float("inf"))]) == []
    assert extract_backbone([(2, 1, 0.4), (1, 2, 0.9)]) == [(1, 2, 0.9)]
    assert extract_backbone([(1, 2, 0.01)], min_sim=0.05) == []


@pytest.mark.parametrize("alpha", [0, 1, -0.1, float("nan"), float("inf")])
def test_invalid_significance_is_rejected(alpha):
    with pytest.raises(ValueError, match="alpha"):
        extract_backbone([], alpha=alpha)


def test_mutual_knn_requires_both_endpoints():
    pairs = [(0, 1, .9), (0, 2, .8), (1, 2, .7), (2, 3, .95)]
    assert mutual_knn(pairs, k=1) == [(0, 1, .9), (2, 3, .95)]


def test_mutual_knn_includes_boundary_ties_and_is_permutation_invariant():
    pairs = _uniform(4)
    assert mutual_knn(pairs, k=1) == pairs
    assert mutual_knn(list(reversed(pairs)), k=1) == pairs


def test_mutual_knn_preserves_original_weights_and_never_forces_connectivity():
    pairs = [(0, 1, 10), (2, 3, 9), (0, 3, 1)]
    assert mutual_knn(pairs, k=1) == pairs[:2]


@pytest.mark.parametrize("k", [0, -1, 1.5, True])
def test_invalid_neighbor_count_is_rejected(k):
    with pytest.raises(ValueError, match="positive integer"):
        mutual_knn([], k=k)
