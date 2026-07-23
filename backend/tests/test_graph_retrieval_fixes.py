"""图检索有效性修复的单元测试（v2.5.0）。

覆盖：
- build_ppr_graph 的 cites 反向低权边（dangling 导流）
- score_papers_ppr 的 exclude_seeds
- _graph_chunk_pool 的 per-paper 限量与 dense 分数排序
- clean_display_title / is_junk_title 页眉噪声清洗
"""

import pytest

from backend.services.deep_research.graph_retriever import (
    GraphRetrieverConfig,
    build_ppr_graph,
    score_papers_ppr,
)
from backend.services.deep_research.hybrid_retriever import _graph_chunk_pool
from backend.services.paper_identity import clean_display_title, is_junk_title


def _hub_graph() -> dict:
    """1 与 2 都引用 3；3 是无出边的 dangling 枢纽（典型扩展引用结构）。"""
    nodes = [{"id": f"paper:{i}"} for i in (1, 2, 3)]
    edges = [
        {"source": "paper:1", "target": "paper:3", "kind": "cites", "weight": 1.0},
        {"source": "paper:2", "target": "paper:3", "kind": "cites", "weight": 1.0},
    ]
    return {"nodes": nodes, "edges": edges}


# ── 反向边 ────────────────────────────────────────────────

def test_reverse_edges_added_with_factor():
    g = _hub_graph()
    g0 = build_ppr_graph(g["nodes"], g["edges"])
    assert not g0.has_edge("paper:3", "paper:1")

    g1 = build_ppr_graph(g["nodes"], g["edges"], cites_reverse_factor=0.25)
    assert g1.has_edge("paper:3", "paper:1")
    assert g1["paper:3"]["paper:1"]["weight"] == pytest.approx(0.25)
    assert g1["paper:1"]["paper:3"]["weight"] == pytest.approx(1.0)  # 正向不受影响


def test_reverse_edge_does_not_overwrite_stronger_real_edge():
    nodes = [{"id": "paper:1"}, {"id": "paper:2"}]
    edges = [
        {"source": "paper:1", "target": "paper:2", "kind": "cites", "weight": 1.0},
        # 语义边双向存储（graph_builder 行为）
        {"source": "paper:1", "target": "paper:2", "kind": "similar", "weight": 0.9},
        {"source": "paper:2", "target": "paper:1", "kind": "similar", "weight": 0.9},
    ]
    g = build_ppr_graph(nodes, edges, cites_reverse_factor=0.25)
    # 反向 cites (0.25) 与已有 similar (0.9) 取 max，不得削弱真实边
    assert g["paper:2"]["paper:1"]["weight"] == pytest.approx(0.9)


def test_dangling_hub_conducts_mass_with_reverse():
    """无反向边时 seed=1 到不了 2；有反向边时质量经枢纽 3 流到共同引用者 2。"""
    g = _hub_graph()
    old = score_papers_ppr(
        graph=g, seed_ids=[1],
        config=GraphRetrieverConfig(cites_reverse_factor=0.0, exclude_seeds=True),
    )
    new = score_papers_ppr(graph=g, seed_ids=[1], config=GraphRetrieverConfig())
    assert old.get(2, 0.0) == 0.0
    assert new.get(2, 0.0) > 0.0
    # 直接引用的 3 仍应比间接可达的 2 分高
    assert new[3] > new[2]


# ── exclude_seeds ─────────────────────────────────────────

def test_exclude_seeds_default_on():
    g = _hub_graph()
    scores = score_papers_ppr(graph=g, seed_ids=[1])
    assert 1 not in scores
    assert scores  # 非 seed 论文仍有分


def test_exclude_seeds_off_keeps_seed():
    g = _hub_graph()
    scores = score_papers_ppr(
        graph=g, seed_ids=[1], config=GraphRetrieverConfig(exclude_seeds=False)
    )
    assert 1 in scores


# ── graph 通道 chunk 池 ───────────────────────────────────

def test_graph_chunk_pool_cap_and_dense_ordering():
    paper_scores = {1: 0.9, 2: 0.5}
    chunks = {1: ["a", "b", "c", "d"], 2: ["e", "f"]}
    pool = _graph_chunk_pool(
        paper_scores, chunks, per_paper_cap=2, chunk_scores={"c": 0.9, "a": 0.5}
    )
    # 论文 1 只取 2 个代表 chunk，且按 dense 分数排序（c > a）；论文 2 不足限量全取
    assert pool == ["c", "a", "e", "f"]


def test_graph_chunk_pool_uncapped_reproduces_legacy():
    paper_scores = {1: 0.9, 2: 0.5}
    chunks = {1: ["a", "b", "c", "d"], 2: ["e", "f"]}
    pool = _graph_chunk_pool(paper_scores, chunks, per_paper_cap=0)
    assert pool == ["a", "b", "c", "d", "e", "f"]


def test_graph_chunk_pool_no_ref_scores_keeps_catalog_order():
    pool = _graph_chunk_pool({1: 1.0}, {1: ["a", "b", "c"]}, per_paper_cap=2)
    assert pool == ["a", "b"]


# ── 标题清洗 ──────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("Published as a conference paper at ICLR 2023 Attention Is All You Need",
     "Attention Is All You Need"),
    ("Under review as a conference paper at NeurIPS 2022 Deep Ensembles Revisited",
     "Deep Ensembles Revisited"),
    ("arXiv:2302.13971v1 [cs.CL] 27 Feb 2023 LLaMA: Open and Efficient Foundation Language Models",
     "LLaMA: Open and Efficient Foundation Language Models"),
    ("Attention Is All You Need", "Attention Is All You Need"),
    ("GAN（Generative Adversarial Nets）", "GAN（Generative Adversarial Nets）"),
])
def test_clean_display_title_strips_banners(raw, expected):
    assert clean_display_title(raw) == expected


@pytest.mark.parametrize("raw", [
    "Published as a conference paper at ICLR 2024",
    "Published as a conference paper at I",
    "",
    None,
])
def test_junk_or_empty_titles(raw):
    assert clean_display_title(raw) is None
    if raw:
        assert is_junk_title(raw)


def test_normal_title_not_junk():
    assert not is_junk_title("Understanding Deep Learning Requires Rethinking Generalization")
