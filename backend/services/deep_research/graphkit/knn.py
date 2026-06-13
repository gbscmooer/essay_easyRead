"""
knn — 相似图去噪（纯函数，无 DB）

替换旧的"全局均值中心化 + 绝对阈值地板"。那套有两个硬伤：
- 中心化在小样本（N≈3）下把共有词汇整体减掉，语义维度被压成 0（实测）。
- 绝对阈值对 BGE 失效（余弦普遍 0.78~0.95，阈值要么全留要么全杀）。

本模块改用两个**尺度无关**、与具体 embedding 无关的经典图构造技巧：

1. **互近邻（mutual k-NN）**：边 (i,j) 成立当且仅当 j 在 i 的 top-k 且 i 在 j 的 top-k。
   这是去"毛球"的核心——枢纽节点被所有人塞进 top-k，但若不被对方回选则不连边，
   单向的弱关系被自然剔除。比单纯 union-kNN 干净得多。

2. **连通性兜底**：互近邻可能让某些节点变孤点；对度为 0 的节点保留其最强的一条边，
   保证语义图不产生孤立点（研究地图要连通才有意义）。

另提供 `local_scaling_affinity`（Zelnik-Manor & Perona 自调尺度）做可选的权重校准：
每个节点用"到第 k 近邻的距离"作局部带宽 σ_i，affinity=exp(-d²/(σ_iσ_j))，
让稠密/稀疏区域各自归一。默认不改变展示用的边权（仍是原始余弦），仅供分析/评测。
"""

from __future__ import annotations

import math
from collections import defaultdict


def _neighbor_lists(
    pairs: list[tuple[int, int, float]], min_sim: float
) -> tuple[dict[int, list[tuple[int, float]]], dict[tuple[int, int], float]]:
    """把对称的 pairs 摊成每个节点的邻居列表（按相似度降序）+ 边查找表。"""
    nbr: dict[int, list[tuple[int, float]]] = defaultdict(list)
    sim_of: dict[tuple[int, int], float] = {}
    for a, b, s in pairs:
        if s < min_sim or a == b:
            continue
        sim_of[(min(a, b), max(a, b))] = s
        nbr[a].append((b, s))
        nbr[b].append((a, s))
    for node in nbr:
        nbr[node].sort(key=lambda x: x[1], reverse=True)
    return nbr, sim_of


def denoise_knn(
    pairs: list[tuple[int, int, float]],
    *,
    k: int = 8,
    mutual: bool = True,
    min_sim: float = 0.05,
    connect_isolated: bool = True,
) -> list[tuple[int, int, float]]:
    """去噪后的相似边 (a, b, sim)，a<b，去重。

    Args:
        k: 每个节点的近邻数。
        mutual: True=互近邻（推荐，去毛球）；False=union-kNN（旧行为，任一端入选即留）。
        min_sim: 原始余弦地板，低于此即便进 top-k 也不连（兜底剔除明显无关对）。
        connect_isolated: 互近邻后对度为 0 的节点补其最强边，避免语义孤点。
    """
    if k <= 0:
        return [(a, b, s) for (a, b), s in _neighbor_lists(pairs, min_sim)[1].items()]

    nbr, sim_of = _neighbor_lists(pairs, min_sim)
    topk = {node: {o for o, _ in lst[:k]} for node, lst in nbr.items()}

    keep: set[tuple[int, int]] = set()
    for node, lst in nbr.items():
        for other, _ in lst[:k]:
            if mutual and node not in topk.get(other, ()):
                continue  # 非互选的弱单向边丢弃
            keep.add((min(node, other), max(node, other)))

    if connect_isolated:
        deg: dict[int, int] = defaultdict(int)
        for a, b in keep:
            deg[a] += 1
            deg[b] += 1
        for node, lst in nbr.items():
            if deg[node] == 0 and lst:
                other = lst[0][0]
                keep.add((min(node, other), max(node, other)))
                deg[node] += 1
                deg[other] += 1

    return [(a, b, sim_of[(a, b)]) for (a, b) in keep if (a, b) in sim_of]


def local_scaling_affinity(
    pairs: list[tuple[int, int, float]], *, k: int = 8, min_sim: float = 0.0
) -> dict[tuple[int, int], float]:
    """自调尺度 affinity（可选权重校准）。返回 {(min,max): affinity in (0,1]}。

    σ_i = 1 - sim(i, i 的第 k 近邻)；affinity = exp(-d_ij² / (σ_i σ_j))，d = 1 - sim。
    稠密区 σ 小 → 同样余弦给更低 affinity（区分更细）；稀疏区反之。
    """
    nbr, sim_of = _neighbor_lists(pairs, min_sim)
    sigma: dict[int, float] = {}
    for node, lst in nbr.items():
        kth_sim = lst[min(k, len(lst)) - 1][1] if lst else 0.0
        sigma[node] = max(1e-6, 1.0 - kth_sim)
    out: dict[tuple[int, int], float] = {}
    for (a, b), s in sim_of.items():
        d = 1.0 - s
        out[(a, b)] = round(math.exp(-(d * d) / (sigma[a] * sigma[b])), 4)
    return out
