"""
细分方向可分性回归测试

这是 v3 重写要守住的核心指标。旧算法只用"全文质心余弦"定拓扑，在"一个大领域里
有多个细分方向"的语料上稳定退化成只能还原**大方向**（合成对照：真值 4 个细分方向，
聚出 2 个大簇，骨架里只有 ~46% 的边连接同一细分方向）。

合成语料的生成过程贴近真实：
- doc 质心（全文平均）= 领域公共成分强 + 大方向 + 细分方向弱 + 噪声小
  → 单看它就是"大方向表示"，这正是旧实现的输入；
- purpose / method 向量（短文本）= 公共成分弱 + 细分方向强 + 噪声大；
- 标题里带细分方向特有的稀有专名（term 通道）、共享数据集、文献耦合。

断言的是**相对关系**（融合口径显著优于只用 topic 通道）与绝对下限，不依赖具体
embedding 模型，因此可长期当回归门禁用。
"""

import asyncio
from collections import Counter

import numpy as np
import pytest

from backend.services.deep_research.graph_analytics import annotate_graph
from backend.services.deep_research.graph_builder import DEFAULT_MAP_DENSITY
from backend.services.deep_research.graphkit import (
    PaperFeatures,
    compute_relations,
    extract_backbone,
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
    kw = {"target_degree": DEFAULT_MAP_DENSITY, "alpha": 0.25,
          "min_sim": 0.05, "ensure_connected": True, **backbone_kw}
    kept = extract_backbone(pairs, **kw)
    nodes = [{"id": f"paper:{i + 1}", "label": f"p{i + 1}", "meta": {}} for i in range(len(labels))]
    edges = []
    for a, b, w in kept:
        for s, t in ((a, b), (b, a)):
            edges.append({"source": f"paper:{s}", "target": f"paper:{t}", "kind": "related", "weight": w})
    stats = annotate_graph(nodes, edges)
    pred = [n["meta"]["cluster"] for n in nodes]
    purity = sum(1 for a, b, _ in kept if labels[a - 1] == labels[b - 1]) / max(1, len(kept))
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
    """同样的地图密度下，多通道融合的骨架边比只用全文质心更贴细分方向。"""
    papers, vec_table, labels, bib = corpus
    baseline = _score(TOPIC_ONLY, papers, vec_table, labels, bib)
    fused = _score(None, papers, vec_table, labels, bib)

    assert fused["purity"] > baseline["purity"] + 0.03, (
        f"骨架边纯度未超过只用主题向量的口径: {fused} vs {baseline}"
    )
    assert fused["ari"] >= baseline["ari"], f"细分方向 ARI 不应低于基线: {fused} vs {baseline}"


def test_density_control_prevents_subfield_collapse(corpus):
    """密度控制本身就是质量手段：毛球密度下细分方向会塌成大方向。

    旧口径（α + "最小边权"当地板，v3.0 行为）在这份语料上留下 231 条边、只剩 2 个大簇；
    按目标平均度反解地板后同一份分数能还原 4 个细分方向。真实工作区上同样的退化更严重
    （ws1：平均度 41.8 的毛球）。
    """
    papers, vec_table, labels, bib = corpus
    hairball = _score(TOPIC_ONLY, papers, vec_table, labels, bib,
                      target_degree=None, baseline="auto")
    controlled = _score(TOPIC_ONLY, papers, vec_table, labels, bib)

    assert hairball["clusters"] < N_AREA * N_SUB, "旧口径本应塌成大方向"
    assert controlled["clusters"] == N_AREA * N_SUB
    assert controlled["purity"] > hairball["purity"] + 0.2, f"{controlled} vs {hairball}"


def test_calibration_does_not_hurt_a_corpus_where_prior_is_right(corpus):
    """自监督校准是自适应机制，不能在"先验本来就对"的语料上帮倒忙。"""
    papers, vec_table, labels, bib = corpus
    rel_cal = asyncio.run(compute_relations(
        papers, embed_fn=_fake_embed(vec_table), bibcoupling=bib, calibrate=True))
    rel_raw = asyncio.run(compute_relations(
        papers, embed_fn=_fake_embed(vec_table), bibcoupling=bib, calibrate=False))
    cal = _evaluate(rel_cal.pairs, labels)
    raw = _evaluate(rel_raw.pairs, labels)

    assert rel_cal.calibration is not None and rel_cal.calibration.applied
    assert cal["ari"] >= raw["ari"] - 0.02, f"校准后 ARI 明显变差: {cal} vs {raw}"
    assert cal["purity"] >= raw["purity"] - 0.02, f"校准后骨架纯度明显变差: {cal} vs {raw}"


def test_evidence_channels_contribute_on_top_of_semantics(corpus):
    """关掉证据通道（数据集/baseline/文献耦合/共引）纯度应下降——证明结构证据真的参与了选边。"""
    papers, vec_table, labels, bib = corpus
    semantic_only = {"dataset": 0.0, "baseline": 0.0, "bibcoupling": 0.0, "cocitation": 0.0}
    without_evidence = _score(semantic_only, papers, vec_table, labels, bib)
    fused = _score(None, papers, vec_table, labels, bib)
    assert fused["purity"] >= without_evidence["purity"], (
        f"证据通道未能提升骨架纯度: {fused} vs {without_evidence}"
    )


def test_analytics_falls_back_when_backbone_is_empty():
    """权重高度均匀的小工作区里骨架可能一条显著边都留不下，全部关系边被标成桥接边。
    此时社区检测必须回退用桥接边，而不是产出"全是孤立点"的空图谱。"""
    nodes = [{"id": f"paper:{i}", "label": f"p{i}", "meta": {}} for i in (1, 2, 3, 4)]
    edges = []
    for a, b in ((1, 2), (2, 3), (1, 3)):
        for s, t in ((a, b), (b, a)):
            edges.append({"source": f"paper:{s}", "target": f"paper:{t}",
                          "kind": "related", "weight": 0.8, "bridge": True})
    stats = annotate_graph(nodes, edges)
    assert stats["clusters"] >= 1
    assert any(n["meta"]["cluster"] >= 0 for n in nodes)
