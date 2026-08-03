"""共引、文献耦合与 Graph PPR 检索测试。"""

import asyncio
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, select

from backend.models.paper import Paper
from backend.models.research import PaperReference
from backend.services.deep_research.graph_analytics import _split_graphs
from backend.services.deep_research.graph_builder import build_graph
from backend.services.deep_research.graph_retriever import (
    GraphRetrieverConfig,
    SOURCE_GRAPH_PPR,
    build_ppr_graph,
    expand_structural_neighbors,
    personalized_pagerank,
    retrieve_as_candidates,
    retrieve_from_graph,
)
from backend.services.deep_research.reference_store import ReferenceInput, list_by_citing, upsert_references
from backend.services.deep_research.graphkit.structural import compute_bibliographic_coupling_pairs
from backend.services.deep_research.graphkit.structural import compute_co_citation_pairs
from backend.services.paper_identity import PaperWork, apply_identity_to_paper, build_canonical_id


def _make_paper(
    session: Session,
    *,
    title: str,
    doi: str | None = None,
    arxiv_id: str | None = None,
) -> Paper:
    p = Paper(
        filename=f"{title[:40]}.pdf",
        pdf_path="/tmp/fake.pdf",
        file_size=100,
        title=title,
        doi=doi,
        arxiv_id=arxiv_id,
        source="uploaded",
        level=1,
        status="indexed",
        download_status="available",
        canonical_id=build_canonical_id(doi=doi, arxiv_id=arxiv_id, title=title),
        updated_at=datetime.now(timezone.utc),
    )
    apply_identity_to_paper(p, PaperWork(title=title, doi=doi, arxiv_id=arxiv_id))
    session.add(p)
    session.commit()
    session.refresh(p)
    return p




@pytest.fixture(autouse=True)
def _cleanup(session):
    yield
    for ref in session.exec(select(PaperReference)).all():
        session.delete(ref)
    for paper in session.exec(select(Paper)).all():
        session.delete(paper)
    session.commit()


class _EmptyRelations:
    """关联度算法的空替身：这些用例只关心 cites / bibcoupling / cocitation 边。"""

    pairs: list = []
    weights: dict = {}
    coverage: dict = {}

    def explain(self, a, b):
        return {"weight": 0.0, "components": []}


@pytest.fixture(autouse=True)
def _mock_relations(monkeypatch):
    async def _empty(papers, **kwargs):
        return _EmptyRelations()

    monkeypatch.setattr(
        "backend.services.deep_research.graph_builder.compute_paper_relations", _empty
    )


# ---------------------------------------------------------------------------
# 纯函数：单调性
# ---------------------------------------------------------------------------

def test_bibcoupling_weight_monotonic_with_shared_refs():
  cites_map = {
      1: {"r1", "r2", "r3"},
      2: {"r1"},
      3: {"r1", "r2"},
  }
  pairs = {(a, b): w for a, b, w, _ in compute_bibliographic_coupling_pairs(cites_map)}
  w_12 = pairs[(1, 2)]
  w_13 = pairs[(1, 3)]
  assert w_13 > w_12  # 共享 2 条 > 共享 1 条


def test_cocitation_weight_monotonic_with_shared_citers():
  cited_by = {
      10: {1, 2},
      20: {1},
      30: {1, 2, 3},
  }
  paper_ids = {10, 20, 30}
  pairs = {(a, b): w for a, b, w, _ in compute_co_citation_pairs(cited_by, paper_ids)}
  w_10_20 = pairs[(10, 20)]
  w_10_30 = pairs[(10, 30)]
  assert w_10_30 > w_10_20  # 2 个共同引用者 > 1 个


# ---------------------------------------------------------------------------
# graph_builder 结构边
# ---------------------------------------------------------------------------

def test_build_graph_adds_bibcoupling_and_cocitation_edges(session):
    """A、B 共享引用 doi（bibcoupling）；D 同时引用 A、B（cocitation）。"""
    paper_a = _make_paper(session, title="Paper A")
    paper_b = _make_paper(session, title="Paper B")
    paper_c = _make_paper(session, title="Paper C", doi="10.1000/shared.c")
    paper_d = _make_paper(session, title="Paper D")

    upsert_references(session, paper_a.id, [
        ReferenceInput(cited_paper_id=paper_c.id),
        ReferenceInput(cited_doi="10.1000/external.x"),
    ])
    upsert_references(session, paper_b.id, [
        ReferenceInput(cited_paper_id=paper_c.id),
        ReferenceInput(cited_doi="10.1000/external.x"),
    ])
    # D 同时引用 A、B → A 与 B 构成共引对
    upsert_references(session, paper_d.id, [
        ReferenceInput(cited_paper_id=paper_a.id),
        ReferenceInput(cited_paper_id=paper_b.id),
    ])

    refs = [
        *list_by_citing(session, paper_a.id),
        *list_by_citing(session, paper_b.id),
        *list_by_citing(session, paper_d.id),
    ]
    graph = asyncio.run(build_graph(
        1, [paper_a, paper_b, paper_c, paper_d],
        enable_shared_method=False,
        references=refs,
    ))

    cites = [e for e in graph["edges"] if e["kind"] == "cites"]
    assert len(cites) == 4

    bc = [e for e in graph["edges"] if e["kind"] == "bibcoupling"]
    assert bc, "应有 bibcoupling 边（A、B 共享外部 doi）"
    bc_pairs = {(e["source"], e["target"]) for e in bc}
    assert (f"paper:{paper_a.id}", f"paper:{paper_b.id}") in bc_pairs

    cc = [e for e in graph["edges"] if e["kind"] == "cocitation"]
    assert cc, "应有 cocitation 边（D 同时引用 A、B）"
    cc_pairs = {(e["source"], e["target"]) for e in cc}
    assert (f"paper:{paper_a.id}", f"paper:{paper_b.id}") in cc_pairs
    assert graph["stats"]["by_kind"]["bibcoupling"] > 0
    assert graph["stats"]["by_kind"]["cocitation"] > 0


def test_disable_bibcoupling_cocitation_flags(session):
    paper_a = _make_paper(session, title="Flag A")
    paper_b = _make_paper(session, title="Flag B")
    paper_c = _make_paper(session, title="Flag C", doi="10.1000/flag.c")

    upsert_references(session, paper_a.id, [ReferenceInput(cited_paper_id=paper_c.id)])
    upsert_references(session, paper_b.id, [ReferenceInput(cited_paper_id=paper_c.id)])
    refs = [*list_by_citing(session, paper_a.id), *list_by_citing(session, paper_b.id)]

    graph = asyncio.run(build_graph(
        1, [paper_a, paper_b, paper_c],
        enable_shared_method=False,
        enable_bibcoupling=False,
        enable_cocitation=False,
        references=refs,
    ))
    assert not [e for e in graph["edges"] if e["kind"] == "bibcoupling"]
    assert not [e for e in graph["edges"] if e["kind"] == "cocitation"]
    assert len([e for e in graph["edges"] if e["kind"] == "cites"]) == 2


# ---------------------------------------------------------------------------
# PPR
# ---------------------------------------------------------------------------

def _triangle_graph() -> dict:
    """3 篇论文链式 cites：1→2→3，bibcoupling 1-3。"""
    nodes = [
        {"id": f"paper:{i}", "kind": "paper", "label": f"P{i}", "meta": {"paper_id": i}}
        for i in (1, 2, 3)
    ]
    edges = [
        {"source": "paper:1", "target": "paper:2", "kind": "cites", "weight": 1.0},
        {"source": "paper:2", "target": "paper:3", "kind": "cites", "weight": 1.0},
        {"source": "paper:1", "target": "paper:3", "kind": "bibcoupling", "weight": 0.8},
        {"source": "paper:3", "target": "paper:1", "kind": "bibcoupling", "weight": 0.8},
    ]
    return {"nodes": nodes, "edges": edges}


def test_ppr_ranking_changes_with_personalization():
    graph = _triangle_graph()
    g = build_ppr_graph(graph["nodes"], graph["edges"])

    scores_seed1 = personalized_pagerank(g, [1])
    scores_seed3 = personalized_pagerank(g, [3])

    # seed=1 时 2 应比 3 更靠前（直接 cites）；seed=3 时排名应不同
    rank_from_1 = sorted(scores_seed1.items(), key=lambda x: x[1], reverse=True)
    rank_from_3 = sorted(scores_seed3.items(), key=lambda x: x[1], reverse=True)
    assert rank_from_1[0][0] != rank_from_3[0][0] or rank_from_1 != rank_from_3


def test_ppr_disabled_returns_empty_baseline():
    graph = _triangle_graph()
    hits = retrieve_from_graph(
        graph, [1],
        config=GraphRetrieverConfig(enable_ppr=False, enable_cocite_neighbors=False, enable_bibcoupling_neighbors=False),
    )
    assert hits == []


def test_bridge_edges_excluded_from_ppr_boost():
    nodes = [
        {"id": "paper:1", "kind": "paper", "label": "P1", "meta": {}},
        {"id": "paper:2", "kind": "paper", "label": "P2", "meta": {}},
        {"id": "paper:3", "kind": "paper", "label": "P3", "meta": {}},
    ]
    # 1-2 正常边，2-3 弱桥接（仅连通兜底）
    edges = [
        {"source": "paper:1", "target": "paper:2", "kind": "similar", "weight": 0.9},
        {"source": "paper:2", "target": "paper:1", "kind": "similar", "weight": 0.9},
        {"source": "paper:2", "target": "paper:3", "kind": "similar", "weight": 0.5, "bridge": True},
        {"source": "paper:3", "target": "paper:2", "kind": "similar", "weight": 0.5, "bridge": True},
    ]
    g_no_bridge = build_ppr_graph(nodes, edges, bridge_weight_factor=0.0)
    g_with_bridge = build_ppr_graph(nodes, edges, bridge_weight_factor=1.0)

    scores_no = personalized_pagerank(g_no_bridge, [1])
    scores_with = personalized_pagerank(g_with_bridge, [1])

    # 无桥接 boost 时 3 的分数应更低或不存在
    assert scores_no.get(3, 0.0) == 0.0
    assert scores_with.get(3, 0.0) >= scores_no.get(3, 0.0)


def test_bridge_edges_excluded_from_semantic_centrality_graph():
    nodes = [{"id": f"paper:{i}"} for i in (1, 2, 3)]
    edges = [
        {"source": "paper:1", "target": "paper:2", "kind": "similar", "weight": 0.9},
        {
            "source": "paper:2",
            "target": "paper:3",
            "kind": "similar",
            "weight": 0.5,
            "bridge": True,
        },
    ]
    _cite, semantic = _split_graphs(nodes, edges)
    assert semantic.has_edge("paper:1", "paper:2")
    assert not semantic.has_edge("paper:2", "paper:3")


def test_expand_cocite_and_bibcoupling_neighbors():
    graph = _triangle_graph()
    hits = expand_structural_neighbors(
        [1], graph["edges"],
        enable_cocite=False,
        enable_bibcoupling=True,
    )
    sources = {h.source for h in hits}
    assert "graph_bibcoupling" in sources
    assert any(h.paper_id == 3 for h in hits)


def test_retrieve_from_graph_ppr_source(session):
    graph = _triangle_graph()
    hits = retrieve_from_graph(graph, [1], config=GraphRetrieverConfig(top_k=5))
    ppr_hits = [h for h in hits if h.source == SOURCE_GRAPH_PPR]
    assert ppr_hits
    assert ppr_hits[0].raw_score > 0
    assert "source:graph_ppr" in ppr_hits[0].provenance


def test_graph_candidates_can_enter_discovery_rrf(session):
    papers = {
        1: _make_paper(session, title="Seed", doi="10.1000/seed"),
        2: _make_paper(session, title="Graph Candidate", doi="10.1000/graph"),
        3: _make_paper(session, title="Other Candidate", doi="10.1000/other"),
    }
    candidates = retrieve_as_candidates(
        _triangle_graph(),
        [1],
        papers,
        config=GraphRetrieverConfig(
            top_k=5,
            enable_cocite_neighbors=False,
            enable_bibcoupling_neighbors=False,
        ),
    )
    assert candidates
    assert all(candidate.canonical_key for candidate in candidates)
    assert any("source:graph_ppr" in candidate.provenance for candidate in candidates)


def test_score_papers_ppr_adapter_for_hybrid():
    """P7 HybridRetriever 期望的 score_papers_ppr 适配层。"""
    from backend.services.deep_research.graph_retriever import score_papers_ppr

    graph = _triangle_graph()
    scores = score_papers_ppr(graph=graph, question="unused", seed_ids=[1])
    assert scores
    assert all(isinstance(pid, int) and sc > 0 for pid, sc in scores.items())
    filtered = score_papers_ppr(
        graph=graph, question="unused", seed_ids=[1], paper_ids=[2],
    )
    assert set(filtered.keys()) <= {2}


def test_cocitation_approximation_from_cites_edges():
    """数据不足时可用 cites 边近似共引：同一 citing 引用多篇 cited。"""
    from backend.services.deep_research.graphkit.structural import co_citation_from_cites_edges

    edges = [
        {"source": "paper:9", "target": "paper:1", "kind": "cites", "weight": 1.0},
        {"source": "paper:9", "target": "paper:2", "kind": "cites", "weight": 1.0},
    ]
    pairs = co_citation_from_cites_edges(edges, {1, 2, 9})
    assert pairs
    assert pairs[0][0] == 1 and pairs[0][1] == 2
