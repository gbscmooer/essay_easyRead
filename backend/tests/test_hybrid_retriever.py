"""HybridRetriever 单元测试 — mock 多路召回，零网络。"""

from __future__ import annotations

import pytest

from backend.services.deep_research.hybrid_retriever import (
    CHANNEL_BM25,
    CHANNEL_DENSE,
    CHANNEL_GRAPH,
    HybridRetriever,
    fuse_ranked_pools,
    get_graph_paper_scores,
)
from backend.services.deep_research.lexical_index import BM25Index, aggregate_paper_scores


def _catalog():
    return {
        "1:sec:0": {
            "chunk_id": "1:sec:0",
            "paper_id": 1,
            "paper_title": "Dense Winner",
            "section_name": "Intro",
            "chunk_index": 0,
            "snippet": "transformer attention mechanism neural network",
        },
        "2:sec:0": {
            "chunk_id": "2:sec:0",
            "paper_id": 2,
            "paper_title": "Graph Favorite",
            "section_name": "Intro",
            "chunk_index": 0,
            "snippet": "graph neural network citation retrieval",
        },
        "3:sec:0": {
            "chunk_id": "3:sec:0",
            "paper_id": 3,
            "paper_title": "Filtered Out",
            "section_name": "Intro",
            "chunk_index": 0,
            "snippet": "should not appear when paper_ids filters",
        },
    }


@pytest.mark.asyncio
async def test_any_source_failure_still_returns(monkeypatch):
    catalog = _catalog()

    def boom(*_a, **_k):
        raise RuntimeError("dense down")

    monkeypatch.setattr(
        "backend.services.deep_research.hybrid_retriever.query_workspace",
        boom,
    )
    monkeypatch.setattr(
        "backend.services.deep_research.hybrid_retriever.list_workspace_chunks",
        lambda ws_id, paper_ids=None: [
            {"id": cid, **meta, "text": meta["snippet"]}
            for cid, meta in catalog.items()
        ],
    )
    async def fake_rerank(q, docs, top_n=None):
        return [
            {"index": i, "score": 0.5, "document": d}
            for i, d in enumerate(docs[: (top_n or len(docs))])
        ]

    monkeypatch.setattr(
        "backend.services.deep_research.hybrid_retriever.rerank",
        fake_rerank,
    )

    retriever = HybridRetriever(1)
    retriever._chunk_catalog = dict(catalog)  # noqa: SLF001

    result = await retriever.retrieve(
        "graph neural",
        [0.1, 0.2],
        top_k=2,
        recall_k=5,
        use_rerank=True,
        mode="hybrid",
    )

    assert len(result.hits) >= 1
    assert all(h["paper_id"] in (1, 2, 3) for h in result.hits)


@pytest.mark.asyncio
async def test_bm25_failure_dense_only(monkeypatch):
    catalog = _catalog()

    monkeypatch.setattr(
        "backend.services.deep_research.hybrid_retriever.query_workspace",
        lambda ws_id, vec, top_k=5, paper_ids=None: [
            {**catalog["1:sec:0"], "score": 0.9}
        ],
    )

    class BrokenBM25:
        def build(self, chunks):
            raise RuntimeError("bm25 build failed")

        def query(self, *a, **k):
            return []

    monkeypatch.setattr(
        "backend.services.deep_research.hybrid_retriever.BM25Index",
        BrokenBM25,
    )
    async def fake_rerank(q, docs, top_n=None):
        return [{"index": 0, "score": 0.8, "document": docs[0]}]

    monkeypatch.setattr(
        "backend.services.deep_research.hybrid_retriever.rerank",
        fake_rerank,
    )

    retriever = HybridRetriever(1)
    result = await retriever.retrieve(
        "transformer",
        [0.1],
        top_k=1,
        recall_k=5,
        use_rerank=True,
        mode="hybrid",
    )
    assert len(result.hits) == 1
    assert result.hits[0]["paper_id"] == 1


@pytest.mark.asyncio
async def test_graph_mode_changes_ordering_with_fake_graph_scores(monkeypatch):
    catalog = _catalog()

    monkeypatch.setattr(
        "backend.services.deep_research.hybrid_retriever.query_workspace",
        lambda ws_id, vec, top_k=5, paper_ids=None: [
            {**catalog["1:sec:0"], "score": 0.95},
        ],
    )
    monkeypatch.setattr(
        "backend.services.deep_research.hybrid_retriever.list_workspace_chunks",
        lambda ws_id, paper_ids=None: [
            {"id": cid, **meta, "text": meta["snippet"]}
            for cid, meta in catalog.items()
        ],
    )

    def fake_graph_scores(*, graph=None, question="", paper_ids=None):
        return {2: 9.9}

    retriever = HybridRetriever(1)

    vector_result = await retriever.retrieve(
        "graph citation retrieval",
        [0.1],
        top_k=2,
        recall_k=5,
        use_rerank=False,
        mode="vector",
    )
    graph_result = await retriever.retrieve(
        "graph citation retrieval",
        [0.1],
        top_k=2,
        recall_k=5,
        use_rerank=False,
        mode="graph",
        graph={"nodes": [], "edges": []},
        graph_score_fn=fake_graph_scores,
    )

    assert vector_result.hits[0]["paper_id"] == 1
    assert graph_result.hits[0]["paper_id"] == 2
    assert graph_result.debug[0].get("graph_score") == pytest.approx(9.9)


@pytest.mark.asyncio
async def test_graph_mode_uses_bm25_seeds_when_dense_fails(monkeypatch):
    catalog = _catalog()
    captured: dict[str, list[int] | None] = {}

    def dense_boom(*_args, **_kwargs):
        raise RuntimeError("dense unavailable")

    monkeypatch.setattr(
        "backend.services.deep_research.hybrid_retriever.query_workspace",
        dense_boom,
    )
    monkeypatch.setattr(
        "backend.services.deep_research.hybrid_retriever.list_workspace_chunks",
        lambda ws_id, paper_ids=None: [
            {"id": cid, **meta, "text": meta["snippet"]}
            for cid, meta in catalog.items()
        ],
    )

    def fake_graph_scores(**kwargs):
        captured["seed_ids"] = kwargs.get("seed_ids")
        return {2: 1.0}

    result = await HybridRetriever(1).retrieve(
        "graph citation retrieval",
        [0.1],
        top_k=2,
        recall_k=5,
        use_rerank=False,
        mode="graph",
        graph={"nodes": [], "edges": []},
        graph_score_fn=fake_graph_scores,
    )

    assert captured["seed_ids"]
    assert captured["seed_ids"][0] == 2
    assert result.hits


@pytest.mark.asyncio
async def test_paper_ids_filter_applies_to_all_sources(monkeypatch):
    catalog = _catalog()
    seen_filters: list[list[int] | None] = []

    def dense(ws_id, vec, top_k=5, paper_ids=None):
        seen_filters.append(paper_ids)
        if paper_ids == [2]:
            return [{**catalog["2:sec:0"], "score": 0.8}]
        return [{**catalog["1:sec:0"], "score": 0.9}]

    def chunks(ws_id, paper_ids=None):
        seen_filters.append(paper_ids)
        allowed = set(paper_ids or [])
        return [
            {"id": cid, **meta, "text": meta["snippet"]}
            for cid, meta in catalog.items()
            if not paper_ids or meta["paper_id"] in allowed
        ]

    monkeypatch.setattr(
        "backend.services.deep_research.hybrid_retriever.query_workspace",
        dense,
    )
    monkeypatch.setattr(
        "backend.services.deep_research.hybrid_retriever.list_workspace_chunks",
        chunks,
    )
    async def fake_rerank(q, docs, top_n=None):
        return [
            {"index": i, "score": 0.5, "document": d}
            for i, d in enumerate(docs)
        ]

    monkeypatch.setattr(
        "backend.services.deep_research.hybrid_retriever.rerank",
        fake_rerank,
    )

    def fake_graph_scores(*, graph=None, question="", paper_ids=None):
        seen_filters.append(paper_ids)
        return {2: 5.0}

    retriever = HybridRetriever(1)
    result = await retriever.retrieve(
        "graph",
        [0.1],
        top_k=3,
        recall_k=5,
        paper_ids=[2],
        mode="graph",
        graph={"nodes": [], "edges": []},
        graph_score_fn=fake_graph_scores,
        use_rerank=True,
    )

    assert all(h["paper_id"] == 2 for h in result.hits)
    assert all(f == [2] for f in seen_filters if f is not None)


def test_fuse_ranked_pools_rrf_prefers_multi_channel():
    pools = {
        CHANNEL_DENSE: ["a", "b", "c"],
        CHANNEL_BM25: ["b", "a"],
        CHANNEL_GRAPH: ["c", "b"],
    }
    fused = fuse_ranked_pools(pools)
    keys = [cid for cid, _, _ in fused]
    assert keys[0] == "b"


def test_bm25_index_basic():
    index = BM25Index()
    index.build([
        {"id": "1:body:0", "text": "transformer attention is powerful", "paper_id": 1},
        {"id": "2:body:0", "text": "graph neural networks for retrieval", "paper_id": 2},
    ])
    hits = index.query("transformer attention", top_k=2)
    assert hits
    assert hits[0]["chunk_id"] == "1:body:0"
    assert hits[0]["score"] > 0


def test_aggregate_paper_scores_top3_sum():
    scores = {"a": 1.0, "b": 3.0, "c": 2.0, "d": 0.5}
    paper_map = {"a": 1, "b": 1, "c": 1, "d": 2}
    agg = aggregate_paper_scores(scores, paper_map, method="top3_sum")
    assert agg[1] == pytest.approx(6.0)
    assert agg[2] == pytest.approx(0.5)


def test_get_graph_paper_scores_import_failure_returns_empty():
    scores = get_graph_paper_scores({"nodes": []}, "q", score_fn=None)
    assert scores == {}


@pytest.mark.asyncio
async def test_vector_mode_keeps_dense_only_compatibility(monkeypatch):
    catalog = _catalog()
    monkeypatch.setattr(
        "backend.services.deep_research.hybrid_retriever.query_workspace",
        lambda ws_id, vec, top_k=5, paper_ids=None: [
            {**catalog["1:sec:0"], "score": 0.9}
        ],
    )

    def unexpected_bm25(*_args, **_kwargs):
        raise AssertionError("vector mode must not build BM25")

    monkeypatch.setattr(
        "backend.services.deep_research.hybrid_retriever.list_workspace_chunks",
        unexpected_bm25,
    )
    result = await HybridRetriever(1).retrieve(
        "transformer",
        [0.1],
        top_k=1,
        recall_k=5,
        mode="vector",
        use_rerank=False,
    )
    assert [hit["paper_id"] for hit in result.hits] == [1]
    assert result.debug[0]["source_hits"].keys() == {CHANNEL_DENSE}


@pytest.mark.asyncio
async def test_graph_scoring_failure_still_returns_dense_and_bm25(monkeypatch):
    catalog = _catalog()
    monkeypatch.setattr(
        "backend.services.deep_research.hybrid_retriever.query_workspace",
        lambda ws_id, vec, top_k=5, paper_ids=None: [
            {**catalog["1:sec:0"], "score": 0.9}
        ],
    )
    monkeypatch.setattr(
        "backend.services.deep_research.hybrid_retriever.list_workspace_chunks",
        lambda ws_id, paper_ids=None: [
            {"id": cid, **meta, "text": meta["snippet"]}
            for cid, meta in catalog.items()
        ],
    )

    def broken_graph(**_kwargs):
        raise RuntimeError("graph unavailable")

    result = await HybridRetriever(1).retrieve(
        "graph retrieval",
        [0.1],
        top_k=2,
        recall_k=5,
        mode="graph",
        graph={"nodes": [{"id": "paper:1"}], "edges": []},
        graph_score_fn=broken_graph,
        use_rerank=False,
    )
    assert result.hits
    assert all(debug["graph_score"] is None for debug in result.debug)


@pytest.mark.asyncio
async def test_debug_fields_complete_after_rerank(monkeypatch):
    catalog = _catalog()
    monkeypatch.setattr(
        "backend.services.deep_research.hybrid_retriever.query_workspace",
        lambda ws_id, vec, top_k=5, paper_ids=None: [
            {**catalog["1:sec:0"], "score": 0.9}
        ],
    )
    monkeypatch.setattr(
        "backend.services.deep_research.hybrid_retriever.list_workspace_chunks",
        lambda ws_id, paper_ids=None: [],
    )

    async def fake_rerank(_query, documents, top_n=None):
        return [{"index": 0, "score": 0.75, "document": documents[0]}]

    monkeypatch.setattr(
        "backend.services.deep_research.hybrid_retriever.rerank",
        fake_rerank,
    )
    result = await HybridRetriever(1).retrieve(
        "transformer",
        [0.1],
        top_k=1,
        recall_k=5,
        mode="vector",
        use_rerank=True,
    )
    debug = result.debug[0]
    assert set(debug) == {
        "chunk_id",
        "source_hits",
        "fused_score",
        "channel_ranks",
        "graph_score",
        "rerank_score",
    }
    assert debug["rerank_score"] == pytest.approx(0.75)
