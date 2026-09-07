"""
论文关联度算法单测（graphkit.relatedness / lexical / structural）

这些用例全部离线：向量化用注入的假 embed_fn，不碰 DB / ChromaDB / 网络。
"""

import asyncio

import pytest

from backend.services.deep_research.graphkit import (
    DEFAULT_WEIGHTS,
    EVIDENCE,
    SEMANTIC,
    PaperFeatures,
    compute_relations,
    normalize_weights,
)
from backend.services.deep_research.graphkit.lexical import (
    build_idf,
    extract_terms,
    idf_cosine,
    normalize_entity,
)
from backend.services.deep_research.graphkit.structural import (
    build_cites_map,
    compute_bibliographic_coupling_pairs,
    pair_map,
)


# ---------------------------------------------------------------------------
# lexical
# ---------------------------------------------------------------------------
def test_extract_terms_keeps_rare_model_names_and_drops_boilerplate():
    terms = extract_terms("Few-shot anomaly detection with DINOv2 on MVTec-AD", "我们提出一种方法")
    assert "dinov2" in terms
    assert "mvtec-ad" in terms
    assert "few-shot" in terms
    assert "method" not in terms      # 停用词
    assert "the" not in terms


def test_idf_cosine_rewards_rare_shared_terms():
    docs = {
        1: {"detection", "mvtec-ad", "dinov2"},
        2: {"detection", "mvtec-ad", "patchcore"},
        3: {"detection", "segmentation"},
        4: {"detection", "captioning"},
        5: {"detection", "tracking"},
    }
    idf = build_idf(docs)
    rare_pair, shared = idf_cosine(docs[1], docs[2], idf)     # 共享冷门 mvtec-ad
    common_pair, _ = idf_cosine(docs[3], docs[4], idf)        # 只共享人人都有的 detection
    assert rare_pair > common_pair
    assert "mvtec-ad" in shared


def test_normalize_entity_unifies_writing_variants():
    assert normalize_entity("MVTec AD") == normalize_entity("mvtec-ad") == "mvtec-ad"


# ---------------------------------------------------------------------------
# 权重归一
# ---------------------------------------------------------------------------
def test_equal_channel_priors_sum_to_one():
    w = normalize_weights(None)
    assert sum(w.values()) == pytest.approx(1.0)
    assert set(w.values()) == {0.125}
    assert sum(w[c] for c in SEMANTIC) == pytest.approx(sum(DEFAULT_WEIGHTS[c] for c in SEMANTIC))


def test_zeroing_evidence_group_gives_semantic_full_weight():
    w = normalize_weights({c: 0.0 for c in EVIDENCE})
    assert sum(w[c] for c in SEMANTIC) == pytest.approx(1.0)
    assert all(w[c] == 0.0 for c in EVIDENCE)


def test_all_zero_weights_falls_back_to_defaults():
    w = normalize_weights({c: 0.0 for c in SEMANTIC + EVIDENCE})
    assert sum(w.values()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# compute_relations
# ---------------------------------------------------------------------------
def _fake_embed(mapping: dict[str, list[float]], dim: int = 4):
    async def embed(texts: list[str]) -> list[list[float]]:
        return [mapping.get(t, [0.0] * (dim - 1) + [1.0]) for t in texts]
    return embed


def _run(coro):
    return asyncio.run(coro)


def test_shared_datasets_and_bibcoupling_lift_the_score():
    """同样的文本，多一份共享数据集 / 文献耦合证据，分数必须更高。"""
    base_vec = [1.0, 0.0, 0.0, 0.0]
    papers = [
        PaperFeatures(id=1, title="Anomaly A", is_analyzed=True, purpose_text="anomaly detection",
                      method_text="patch memory", datasets=["MVTec-AD"], doc_vector=base_vec),
        PaperFeatures(id=2, title="Anomaly B", is_analyzed=True, purpose_text="anomaly detection",
                      method_text="patch memory", datasets=["MVTec-AD"], doc_vector=base_vec),
        PaperFeatures(id=3, title="Anomaly C", is_analyzed=True, purpose_text="anomaly detection",
                      method_text="patch memory", datasets=["VisA"], doc_vector=base_vec),
        PaperFeatures(id=4, title="Caption D", is_analyzed=True, purpose_text="image captioning",
                      method_text="autoregressive decoding", datasets=["COCO"],
                      doc_vector=[0.0, 1.0, 0.0, 0.0]),
    ]
    embed = _fake_embed({
        "anomaly detection": [1.0, 0.0, 0.0, 0.0],
        "image captioning": [0.0, 1.0, 0.0, 0.0],
        "patch memory": [1.0, 0.0, 0.0, 0.0],
        "autoregressive decoding": [0.0, 1.0, 0.0, 0.0],
    })
    bib = pair_map([(1, 2, 0.6, 6)])

    rel = _run(compute_relations(papers, embed_fn=embed, bibcoupling=bib))
    scores = {(a, b): s for a, b, s in rel.pairs}

    # 1&2：文本相同 + 共享数据集 + 文献耦合 > 1&3：文本相同但只差数据集
    assert scores[(1, 2)] > scores[(1, 3)]
    # 同方向的任意一对都应高于跨方向的 1&4（分数为 0 的对不进 pairs，视作 0）
    assert scores[(1, 3)] > scores.get((1, 4), 0.0)


def test_explain_components_sum_to_score():
    papers = [
        PaperFeatures(id=1, title="A", is_analyzed=True, purpose_text="p1", method_text="m1",
                      datasets=["D1"], baselines=["B1"], doc_vector=[1.0, 0.0]),
        PaperFeatures(id=2, title="B", is_analyzed=True, purpose_text="p1", method_text="m2",
                      datasets=["D1"], baselines=["B2"], doc_vector=[0.9, 0.1]),
        PaperFeatures(id=3, title="C", is_analyzed=True, purpose_text="p3", method_text="m3",
                      datasets=["D3"], baselines=["B3"], doc_vector=[0.0, 1.0]),
    ]
    embed = _fake_embed({"p1": [1.0, 0.0], "p3": [0.0, 1.0],
                         "m1": [1.0, 0.0], "m2": [0.8, 0.2], "m3": [0.0, 1.0]}, dim=2)
    rel = _run(compute_relations(papers, embed_fn=embed))

    detail = rel.explain(1, 2)
    assert detail["components"], "必须给出维度分解"
    total = sum(c["contribution"] for c in detail["components"])
    assert total == pytest.approx(detail["weight"], abs=1e-3)
    assert {c["facet"] for c in detail["components"]} == set(SEMANTIC + EVIDENCE)
    # 共享数据集要能在解释里看到具体名字
    dataset_comp = next(c for c in detail["components"] if c["facet"] == "dataset")
    assert dataset_comp["shared"] == ["D1"]


def test_leaf_papers_without_analysis_still_get_scored():
    """Missing channels cast no vote, while available text still supplies ranks."""
    papers = [
        PaperFeatures(id=1, title="Diffusion models for image synthesis", is_analyzed=True,
                      purpose_text="generative image synthesis", method_text="denoising",
                      doc_vector=[1.0, 0.0]),
        PaperFeatures(id=2, title="Diffusion models for video synthesis", is_analyzed=False,
                      abstract="video synthesis with diffusion", doc_vector=[0.95, 0.05]),
        PaperFeatures(id=3, title="Graph neural networks for molecules", is_analyzed=False,
                      abstract="molecular property prediction", doc_vector=[0.0, 1.0]),
    ]
    embed = _fake_embed({"generative image synthesis": [1.0, 0.0], "denoising": [1.0, 0.0]}, dim=2)
    rel = _run(compute_relations(papers, embed_fn=embed))
    scores = {(a, b): s for a, b, s in rel.pairs}

    assert scores[(1, 2)] > 0, "叶子论文必须能连边"
    assert scores[(1, 2)] > scores.get((1, 3), 0.0)
    # The omitted vote and its prior remain visible in the explanation.
    purpose = next(c for c in rel.explain(2, 3)["components"] if c["facet"] == "purpose")
    assert purpose["weight"] == 0.0
    assert "缺少可比文本" in purpose["reason"]


def test_embedding_failure_degrades_to_remaining_channels():
    async def broken_embed(texts):
        raise RuntimeError("embedding service down")

    papers = [
        PaperFeatures(id=1, title="anomaly detection on MVTec-AD", is_analyzed=True,
                      purpose_text="anomaly", datasets=["MVTec-AD"]),
        PaperFeatures(id=2, title="anomaly detection on MVTec-AD variants", is_analyzed=True,
                      purpose_text="anomaly", datasets=["MVTec-AD"]),
        PaperFeatures(id=3, title="machine translation with transformers", is_analyzed=True,
                      purpose_text="translation", datasets=["WMT14"]),
    ]
    rel = _run(compute_relations(papers, embed_fn=broken_embed))
    scores = {(a, b): s for a, b, s in rel.pairs}
    assert scores[(1, 2)] > scores.get((1, 3), 0.0), "embedding 挂了也要靠术语/实体通道给出关系"


def test_structural_channel_matches_reference_overlap():
    class Ref:
        def __init__(self, citing, doi):
            self.citing_paper_id = citing
            self.cited_paper_id = None
            self.cited_doi = doi
            self.cited_arxiv_id = None
            self.cited_title = None

    refs = [Ref(1, "10.1/a"), Ref(1, "10.1/b"), Ref(2, "10.1/a"), Ref(2, "10.1/b"), Ref(3, "10.1/z")]
    pairs = compute_bibliographic_coupling_pairs(build_cites_map(refs, {1, 2, 3}))
    mapped = pair_map(pairs)
    assert mapped[(1, 2)][1] == 2          # 共享 2 篇参考文献
    assert (1, 3) not in mapped

def test_custom_weights_preserve_requested_ratios_across_groups():
    weights = {c: 0.0 for c in SEMANTIC + EVIDENCE}
    weights.update(topic=1.0, dataset=3.0)
    normalized = normalize_weights(weights)
    assert normalized["topic"] == .25
    assert normalized["dataset"] == .75
    assert normalize_weights({c: 1e308 for c in weights}) == DEFAULT_WEIGHTS
    with pytest.raises(ValueError, match="Non-finite"):
        normalize_weights({"topic": float("nan")})


def _structural_only_relations(values, ids=(1, 2, 3, 4), **kwargs):
    async def no_embed(texts):
        return [[] for _ in texts]
    weights = dict.fromkeys(SEMANTIC + EVIDENCE, 0.0)
    weights["bibcoupling"] = 1.0
    return _run(compute_relations(
        [PaperFeatures(id=i, title="") for i in ids], embed_fn=no_embed,
        weights=weights, bibcoupling={pair: (value, 1) for pair, value in values.items()}, **kwargs,
    ))


def test_rrf_matches_endpoint_reciprocal_ranks_with_average_ties():
    rel = _structural_only_relations({(1, 2): 9, (1, 3): 9, (2, 3): 1, (2, 4): 2})
    # From node 1, node 2 ties for ranks 1 and 2. From node 2 it ranks first.
    expected = (61 / 61.5 + 61 / 61) / 2
    assert rel.score(1, 2) == pytest.approx(expected)
    assert rel.score(2, 1) == rel.score(1, 2)
    detail = rel.explain(1, 2)
    component = next(c for c in detail["components"] if c["facet"] == "bibcoupling")
    assert component["rank_source"] == 1.5
    assert component["rank_target"] == 1
    assert detail["method"] == "weighted_rrf_v1"
    assert detail["symmetrization"] == "mean_endpoint_reciprocal_ranks"


def test_rrf_is_invariant_to_positive_monotone_rescaling_and_input_order():
    values = {(1, 2): .8, (1, 3): .2, (1, 4): .1, (2, 3): .3, (3, 4): .9}
    before = _structural_only_relations(values)
    after = _structural_only_relations({p: v ** 3 for p, v in reversed(list(values.items()))}, ids=(4, 2, 1, 3))
    assert after.pairs == before.pairs


def test_zeros_missing_invalid_external_and_self_pairs_never_vote():
    rel = _structural_only_relations({
        (1, 2): 1.0, (2, 3): 0, (1, 3): -1, (3, 4): float("nan"),
        (2, 4): float("inf"), (1, 99): 100, (1, 1): 100,
    })
    assert rel.pairs == [(1, 2, 1.0)]
    assert rel.score(1, 1) == rel.score(1, 99) == 0


def test_missing_channels_do_not_inflate_a_single_evidence_vote():
    async def empty_embed(texts):
        return [[] for _ in texts]
    rel = _run(compute_relations(
        [PaperFeatures(id=1, title=""), PaperFeatures(id=2, title="")],
        embed_fn=empty_embed, bibcoupling={(1, 2): (.1, 1)},
    ))
    assert rel.score(1, 2) == .125
    assert sum(c["contribution"] for c in rel.explain(1, 2)["components"]) == .125


def test_calibrate_parameter_cannot_refit_custom_weights():
    values = {(1, 2): .9, (1, 3): .1, (2, 3): .2}
    before = _structural_only_relations(values, calibrate=False)
    after = _structural_only_relations(values, calibrate=True)
    assert after.pairs == before.pairs
    assert after.weights == before.weights
    assert not after.calibration.applied


def test_dense_rank_ties_and_nonpositive_scores_do_not_create_votes():
    import numpy as np
    from backend.services.deep_research.graphkit.relatedness import _DenseChannel
    channel = _DenseChannel(
        "topic", {1: 0, 2: 1, 3: 2},
        np.array([[1, .8, .8], [.8, 1, 0], [.8, 0, 1]]),
        np.array([True, True, True]),
    )
    channel.prepare_ranks()
    assert channel.rank_pair(1, 2) == (1.5, 1)
    assert channel.norm(1, 2) == pytest.approx((61 / 61.5 + 1) / 2)
    assert channel.norm(2, 3) == channel.norm(1, 1) == 0
