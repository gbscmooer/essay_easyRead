"""End-to-end graph selection policy and persisted edge provenance."""

import asyncio
from types import SimpleNamespace

import pytest

from backend.services.deep_research import graph_builder as gb
from backend.services.deep_research.graphkit import PaperFeatures, compute_relations
from backend.tests.test_relation_quality import _corpus, _fake_embed, _bibcoupling


def _paper(pid):
    return SimpleNamespace(
        id=pid, title=f"paper {pid}", filename=f"{pid}.pdf", year=2024, venue="test",
        source="uploaded", status="indexed", external_id=None, graph_digest=None,
    )


def test_build_graph_rrf_knn_is_explicit_and_yields_analysis_clusters(monkeypatch):
    features, vecs, labels = _corpus()
    relations = asyncio.run(compute_relations(
        features, embed_fn=_fake_embed(vecs), bibcoupling=_bibcoupling(labels),
    ))

    async def fake_relations(*args, **kwargs):
        return relations
    monkeypatch.setattr(gb, "compute_paper_relations", fake_relations)
    graph = asyncio.run(gb.build_graph(1, [_paper(p.id) for p in features], references=[]))
    stats = graph["stats"]
    assert stats["backbone"]["method"] == "mutual_knn"
    assert stats["backbone"]["alpha"] == .05
    assert stats["backbone"]["knn_k"] == 10
    assert stats["backbone"]["disparity_role"] == "annotation_only"
    assert stats["backbone"]["disparity_retained_pairs"] == 0
    assert stats["backbone"]["analysis_mean_degree"] > 0
    assert "map_density" not in stats["backbone"]
    assert stats["relation_channels"]["method"] == "weighted_rrf_v1"
    assert stats["clusters"] == 4
    assert all(n["meta"]["cluster"] >= 0 for n in graph["nodes"])
    assert all(
        e["selection_method"] == ("display_bridge" if e.get("bridge") else "mutual_knn")
        and not e["detail"]["disparity_retained"]
        for e in graph["edges"] if e["kind"] == "related"
    )
    by_pair = {(e["source"], e["target"]): e for e in graph["edges"] if e["kind"] == "related"}
    for (source, target), edge in by_pair.items():
        reverse = by_pair[target, source]
        for forward_component, reverse_component in zip(edge["detail"]["components"], reverse["detail"]["components"]):
            assert forward_component["rank_source"] == reverse_component["rank_target"]
            assert forward_component["rank_target"] == reverse_component["rank_source"]
    with pytest.warns(DeprecationWarning, match="map_density"):
        changed_density = asyncio.run(gb.build_graph(
            1, [_paper(p.id) for p in features], references=[], map_density=100,
        ))
    assert changed_density == graph


def test_sparse_disparity_cannot_isolate_the_analysis_graph():
    pairs = [(0, 1, 8), (0, 2, 1), (0, 3, 1), (1, 2, 10), (1, 3, 10), (2, 3, 10)]
    edges, bridges, retained, method = gb._select_relation_edges(pairs, alpha=.05, knn_k=10)
    assert method == "mutual_knn"
    assert retained == {(0, 1)}
    assert {(a, b) for a, b, _ in edges} - bridges == {(a, b) for a, b, _ in pairs}
    strict_edges, strict_bridges, strict_retained, _ = gb._select_relation_edges(
        pairs, alpha=.001, knn_k=10,
    )
    assert strict_retained == set()
    assert (strict_edges, strict_bridges) == (edges, bridges)


def test_no_absolute_threshold_drops_small_positive_rank_scores():
    edges, bridges, retained, method = gb._select_relation_edges(
        [(0, 1, .000001)], alpha=.05, knn_k=10,
    )
    assert edges == [(0, 1, .000001)]
    assert bridges == set()
    assert retained == {(0, 1)}
    assert method == "mutual_knn"


def test_disconnected_knn_gets_display_bridges_without_analysis_membership():
    pairs = [(0, 1, .9), (0, 2, .2), (0, 3, .2),
             (1, 2, .2), (1, 3, .2), (2, 3, .9)]
    edges, bridges, retained, method = gb._select_relation_edges(pairs, alpha=.05, knn_k=1)
    assert retained == set()
    assert method == "mutual_knn"
    assert {(a, b) for a, b, _ in edges} - bridges == {(0, 1), (2, 3)}
    assert len(bridges) == 1


def test_empty_relations_remain_empty():
    assert gb._select_relation_edges([], alpha=.05, knn_k=10) == ([], set(), set(), "mutual_knn")
