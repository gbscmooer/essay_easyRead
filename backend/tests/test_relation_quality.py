"""
Offline synthetic graph regression, not an estimate of real-world accuracy.

The corpus has broad topic vectors and narrower lexical/entity evidence. Tests
compare the production disparity-or-mutual-kNN policy on fixed synthetic data.
No labels are used by the ranking or community algorithms.
"""
import asyncio
from collections import Counter
from dataclasses import replace

import numpy as np
import pytest

from backend.services.deep_research.graph_analytics import annotate_graph
from backend.services.deep_research.graph_builder import _select_relation_edges
from backend.services.deep_research.graphkit import (
    PaperFeatures,
    compute_relations,
)

DIM = 64
N_AREA = 2          # 大方向数
N_SUB = 2           # 每个大方向下的细分方向数
N_PER = 8           # 每个细分方向的论文数
SUB_TERMS = {       # 每个细分方向特有的稀有专名（真实论文标题里就长这样）
    0: ("mvtec-ad", "patchcore"),
    1: ("visa", "winclip"),
    2: ("coco-caption", "blip2"),
    3: ("nocaps", "flamingo"),
}
SUB_DATASET = {0: "MVTec-AD", 1: "VisA", 2: "COCO", 3: "NoCaps"}


def _corpus(seed: int = 0):
    """返回 (papers, purpose/method 向量表, 真实细分方向标签)。"""
    rng = np.random.default_rng(seed)

    def unit(*orth):
        v = rng.normal(size=DIM)
        for o in orth:
            v -= (v @ o) * o
        return v / np.linalg.norm(v)

    common = unit()
    areas = [unit(common) for _ in range(N_AREA)]
    subs = {(a, s): unit(common, areas[a]) for a in range(N_AREA) for s in range(N_SUB)}

    papers: list[PaperFeatures] = []
    vec_table: dict[str, list[float]] = {}
    labels: list[int] = []
    pid = 0
    for ai in range(N_AREA):
        for si in range(N_SUB):
            sub_id = ai * N_SUB + si
            for k in range(N_PER):
                pid += 1
                personal = unit()
                # 全文质心：领域公共 + 大方向 占主导，细分信号被平均稀释
                doc = 1.0 * common + 0.40 * areas[ai] + 0.18 * subs[(ai, si)] + 0.22 * personal
                # 短文本向量：细分信号强，噪声大
                short = 0.45 * common + 0.30 * areas[ai] + 0.42 * subs[(ai, si)] + 0.45 * personal
                purpose = f"purpose-{sub_id}-{k}"
                method = f"method-{sub_id}-{k}"
                vec_table[purpose] = (short / np.linalg.norm(short)).tolist()
                m = short + 0.15 * unit()
                vec_table[method] = (m / np.linalg.norm(m)).tolist()
                papers.append(PaperFeatures(
                    id=pid,
                    title=f"paper {pid} on {SUB_TERMS[sub_id][0]} with {SUB_TERMS[sub_id][1]}",
                    is_analyzed=True,
                    purpose_text=purpose,
                    method_text=method,
                    datasets=[SUB_DATASET[sub_id]],
                    baselines=[SUB_TERMS[sub_id][1]],
                    doc_vector=(doc / np.linalg.norm(doc)).tolist(),
                ))
                labels.append(sub_id)
    return papers, vec_table, labels


def _bibcoupling(labels: list[int], seed: int = 7):
    """同细分方向的论文大概率共享参考文献，跨方向偶发弱共享。"""
    rng = np.random.default_rng(seed)
    out: dict[tuple[int, int], tuple[float, int]] = {}
    n = len(labels)
    for i in range(n):
        for j in range(i + 1, n):
            same = labels[i] == labels[j]
            if rng.random() < (0.55 if same else 0.06):
                salton = float(rng.uniform(0.15, 0.45) if same else rng.uniform(0.02, 0.12))
                out[(i + 1, j + 1)] = (salton, int(round(salton * 20)))
    return out


def _fake_embed(vec_table: dict[str, list[float]]):
    async def embed(texts: list[str]) -> list[list[float]]:
        return [vec_table.get(t, [0.0] * (DIM - 1) + [1.0]) for t in texts]
    return embed


def _adjusted_rand(true_labels, pred_labels) -> float:
    n = len(true_labels)
    comb = lambda x: x * (x - 1) / 2  # noqa: E731
    sij = sum(comb(v) for v in Counter(zip(true_labels, pred_labels)).values())
    sa = sum(comb(v) for v in Counter(true_labels).values())
    sb = sum(comb(v) for v in Counter(pred_labels).values())
    expected = sa * sb / comb(n)
    maximum = (sa + sb) / 2
    return (sij - expected) / (maximum - expected) if maximum != expected else 0.0


def _evaluate(pairs, labels, **backbone_kw) -> dict:
    """跑生产的骨架提取 + 社区检测，返回细分方向 ARI / 骨架边纯度 / 簇数。"""
    kw = {"alpha": 0.05, "knn_k": 10, **backbone_kw}
    kept, bridges, disparity, method = _select_relation_edges(pairs, **kw)
    nodes = [{"id": f"paper:{i + 1}", "label": f"p{i + 1}", "meta": {}} for i in range(len(labels))]
    edges = []
    for a, b, w in kept:
        for s, t in ((a, b), (b, a)):
            edges.append({"source": f"paper:{s}", "target": f"paper:{t}", "kind": "related", "weight": w,
                          "bridge": (min(a, b), max(a, b)) in bridges})
    stats = annotate_graph(nodes, edges)
    pred = [n["meta"]["cluster"] for n in nodes]
    analysis_edges = [(a, b, w) for a, b, w in kept if (a, b) not in bridges]
    purity = sum(1 for a, b, _ in analysis_edges if labels[a - 1] == labels[b - 1]) / max(1, len(analysis_edges))
    top1_correct = 0
    best: dict[int, tuple[int, float]] = {}
    for a, b, w in pairs:
        if w > best.get(a, (0, -1.0))[1]:
            best[a] = (b, w)
        if w > best.get(b, (0, -1.0))[1]:
            best[b] = (a, w)
    for a, (b, _) in best.items():
        top1_correct += int(labels[a - 1] == labels[b - 1])
    return {
        "method": method,
        "disparity_pairs": len(disparity),
        "analysis_pairs": len(analysis_edges),
        "ari": _adjusted_rand(labels, pred),
        "purity": purity,
        "clusters": stats["clusters"],
        "top1": top1_correct / max(1, len(best)),
    }


def _score(weights, papers, vec_table, labels, bib, **backbone_kw):
    rel = asyncio.run(compute_relations(
        papers, embed_fn=_fake_embed(vec_table), weights=weights,
        bibcoupling=bib, cocitation=None,
    ))
    return _evaluate(rel.pairs, labels, **backbone_kw)


@pytest.fixture(scope="module")
def corpus():
    papers, vec_table, labels = _corpus()
    return papers, vec_table, labels, _bibcoupling(labels)


def test_fused_relations_recover_fine_grained_subfields(corpus):
    papers, vec_table, labels, bib = corpus
    fused = _score(None, papers, vec_table, labels, bib)

    assert fused["clusters"] == N_AREA * N_SUB, (
        f"应还原 {N_AREA * N_SUB} 个细分方向，实际 {fused['clusters']}：{fused}"
    )
    assert fused["ari"] >= 0.80, f"细分方向 ARI 过低: {fused}"
    assert fused["purity"] >= 0.70, f"骨架里跨细分方向的边太多: {fused}"
    assert fused["top1"] >= 0.90, f"最相似邻居常常不是同细分方向: {fused}"


TOPIC_ONLY = {"topic": 1.0, "purpose": 0.0, "method": 0.0, "term": 0.0,
              "dataset": 0.0, "baseline": 0.0, "bibcoupling": 0.0, "cocitation": 0.0}


def test_fused_beats_topic_only_baseline(corpus):
    """Additional channels resolve subfields when topic vectors are indistinguishable."""
    papers, vec_table, labels, bib = corpus
    papers = [replace(p, doc_vector=[1.0, 0.0]) for p in papers]
    baseline = _score(TOPIC_ONLY, papers, vec_table, labels, bib)
    fused = _score(None, papers, vec_table, labels, bib)

    assert fused["purity"] > baseline["purity"] + 0.03, (
        f"骨架边纯度未超过只用主题向量的口径: {fused} vs {baseline}"
    )
    assert fused["ari"] >= baseline["ari"], f"细分方向 ARI 不应低于基线: {fused} vs {baseline}"


def test_empty_disparity_uses_explicit_mutual_knn_policy(corpus):
    papers, vec_table, labels, bib = corpus
    result = _score(None, papers, vec_table, labels, bib)
    assert result["disparity_pairs"] == 0
    assert result["method"] == "mutual_knn"
    assert result["analysis_pairs"] > 0
    assert result["clusters"] == N_AREA * N_SUB
    assert result["ari"] >= 0.80


def test_legacy_calibration_flag_cannot_change_graph_or_weights(corpus):
    papers, vec_table, labels, bib = corpus
    enabled = asyncio.run(compute_relations(
        papers, embed_fn=_fake_embed(vec_table), bibcoupling=bib, calibrate=True))
    disabled = asyncio.run(compute_relations(
        papers, embed_fn=_fake_embed(vec_table), bibcoupling=bib, calibrate=False))
    assert not enabled.calibration.applied
    assert enabled.weights == disabled.weights
    assert enabled.pairs == disabled.pairs
    assert _evaluate(enabled.pairs, labels) == _evaluate(disabled.pairs, labels)


def test_evidence_channels_contribute_on_top_of_semantics(corpus):
    """Entity and citation evidence resolve an otherwise tied semantic graph."""
    papers, vec_table, labels, bib = corpus
    papers = [replace(p, title="common", purpose_text="", method_text="",
                      doc_vector=[1.0, 0.0]) for p in papers]
    semantic_only = {"dataset": 0.0, "baseline": 0.0, "bibcoupling": 0.0, "cocitation": 0.0}
    without_evidence = _score(semantic_only, papers, vec_table, labels, bib)
    fused = _score(None, papers, vec_table, labels, bib)
    assert fused["purity"] >= without_evidence["purity"], (
        f"证据通道未能提升骨架纯度: {fused} vs {without_evidence}"
    )


def test_analytics_never_uses_display_bridges_when_backbone_is_empty():
    """The builder must choose an explicit analysis policy; analytics cannot invent one."""
    nodes = [{"id": f"paper:{i}", "label": f"p{i}", "meta": {}} for i in (1, 2, 3, 4)]
    edges = []
    for a, b in ((1, 2), (2, 3), (1, 3)):
        for s, t in ((a, b), (b, a)):
            edges.append({"source": f"paper:{s}", "target": f"paper:{t}",
                          "kind": "related", "weight": 0.8, "bridge": True})
    stats = annotate_graph(nodes, edges)
    assert stats["clusters"] == 0
    assert all(n["meta"]["cluster"] == -1 for n in nodes)
    assert all(n["meta"]["importance"] == 0 for n in nodes)
