"""Leiden partitions and bridge-independent PageRank contracts."""
import builtins
from copy import deepcopy

import networkx as nx
import pytest

from backend.services.deep_research import graph_analytics as ga


def _nodes(count):
    return [{"id": str(i), "label": f"paper {i}", "meta": {}} for i in range(count)]


def _edge(a, b, weight=1.0, **extra):
    return {"source": str(a), "target": str(b), "kind": "related", "weight": weight, **extra}


def test_bridge_only_graph_remains_unclustered():
    nodes = _nodes(4)
    for node in nodes:
        node["meta"]["cluster_label"] = "stale"
    stats = ga.annotate_graph(nodes, [_edge(i, i + 1, bridge=True) for i in range(3)])
    assert stats["clusters"] == 0
    assert stats["modularity"] == 0
    assert all(n["meta"]["cluster"] == -1 for n in nodes)
    assert all(n["meta"]["importance"] == 0 for n in nodes)
    assert all(n["meta"]["cluster_label"] == "" for n in nodes)


def test_bridges_cannot_attach_nodes_or_change_any_analysis():
    nodes = _nodes(5)
    edges = [_edge(0, 1, 3), _edge(1, 2, 1), _edge(2, 0, 2)]
    expected = deepcopy(nodes)
    ga.annotate_graph(expected, edges)
    actual = deepcopy(nodes)
    ga.annotate_graph(actual, edges + [_edge(2, 3, 10000, bridge=True), _edge(3, 4, 10000, bridge=True)])
    assert actual == expected
    assert actual[3]["meta"]["cluster"] == -1


def test_importance_is_single_relation_pagerank_and_citations_separate():
    nodes = _nodes(4)
    edges = [_edge(0, 1, 4), _edge(1, 2, 1), _edge(2, 3, 1)]
    ga.annotate_graph(nodes, edges + [_edge(i, 3, kind="cites") for i in range(3)])
    rel = nx.Graph()
    rel.add_weighted_edges_from([("0", "1", 4), ("1", "2", 1), ("2", "3", 1)])
    raw = nx.pagerank(rel, weight="weight")
    scaled = ga._minmax(raw)
    assert sum(n["meta"]["pagerank"] for n in nodes) == pytest.approx(1)
    for node in nodes:
        assert node["meta"]["pagerank"] == pytest.approx(raw[node["id"]])
        assert node["meta"]["importance"] == round(scaled[node["id"]], 4)
        assert node["meta"]["importance"] == node["meta"]["centrality"]
    assert nodes[3]["meta"]["foundational"] == 1


def test_leiden_recovers_disconnected_cliques_deterministically():
    graph = nx.disjoint_union(nx.complete_graph(5), nx.complete_graph(4))
    nx.set_edge_attributes(graph, 1.0, "weight")
    communities = ga._detect_communities(graph)
    assert {frozenset(c) for c in communities} == {frozenset(range(5)), frozenset(range(5, 9))}
    assert ga._detect_communities(graph) == communities
    reversed_graph = nx.Graph()
    reversed_graph.add_nodes_from(reversed(list(graph.nodes)))
    reversed_graph.add_edges_from(reversed(list(graph.edges(data=True))))
    assert ga._detect_communities(reversed_graph) == communities


def test_missing_leiden_raises_actionable_error_without_louvain(monkeypatch):
    original_import = builtins.__import__

    def without_leiden(name, *args, **kwargs):
        if name == "leidenalg":
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_leiden)
    with pytest.raises(RuntimeError, match="requires igraph and leidenalg"):
        ga._detect_communities(nx.path_graph(3))


def test_leiden_runtime_errors_propagate(monkeypatch):
    import leidenalg

    def broken(*args, **kwargs):
        raise RuntimeError("partition failure")

    monkeypatch.setattr(leidenalg, "find_partition", broken)
    with pytest.raises(RuntimeError, match="partition failure"):
        ga._detect_communities(nx.path_graph(3))


def test_small_community_shim_preserves_partition():
    graph = nx.path_graph(4)
    communities = [{0}, {1, 2, 3}]
    result = ga._merge_small(graph, communities, min_size=10)
    assert result == communities
    assert result[0] is not communities[0]


def test_production_does_not_merge_leiden_singletons(monkeypatch):
    monkeypatch.setattr(ga, "_detect_communities", lambda graph: [{"0"}, {"1", "2"}])
    nodes = _nodes(3)
    stats = ga.annotate_graph(nodes, [_edge(0, 1), _edge(1, 2)])
    assert nodes[0]["meta"]["cluster"] != nodes[1]["meta"]["cluster"]
    assert stats["clusters"] == 2


def test_invalid_edges_do_not_create_phantom_nodes_or_promote_zero_weights():
    cite, rel, bridges = ga._split_graphs(
        _nodes(3),
        [_edge(0, 1, 0), _edge(1, 2, float("nan")), _edge(1, 2, float("inf")),
         _edge(0, 99), _edge(1, 1), _edge(0, 2, -1)],
    )
    assert len(cite) == len(rel) == 3
    assert rel.number_of_edges() == cite.number_of_edges() == 0
    assert bridges == []
