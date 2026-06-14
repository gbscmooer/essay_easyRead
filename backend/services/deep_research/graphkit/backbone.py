"""
backbone — 多尺度骨架提取（纯函数，无 DB）

去"毛球"的核心算法。论文语义相似图是一张**稠密加权图**（几乎每对论文都有一条余弦
边），直接展示就是一团毛球：高相似度边和大量中等相似度边混在一起，看不出研究方向的
骨架。本模块用 **Disparity Filter** 抽取其骨架——只保留相对**每个节点局部强度分布**
而言统计显著的边。

    Serrano M.Á., Boguñá M., Vespignani A. (2009)
    "Extracting the multiscale backbone of complex weighted networks", PNAS 106(16).

为什么比"全局 k 近邻"更适合去毛球
--------------------------------------------------
- 全局 k（互近邻）对所有节点用同一个 k：稠密子领域里 k 偏小 → 局部仍是毛球；稀疏区
  k 偏大 → 把弱关系也强连进来。k 只是"条数"，分不清"8 条真·强边"还是"8 条平庸边"。
- Disparity Filter 对**每个节点用它自己的强度分布**做零模型检验，稠密 / 稀疏区各自
  归一，是真正的**多尺度**：枢纽论文按需保留多条，专一论文只留它最相关的一两条。
- 单一可解释参数 **α**（显著性水平）：越小骨架越稀、越严格。

零模型与判据
--------------------------------------------------
把节点 i 的 k_i 条边的归一化权重 p_ij = w_ij / s_i（s_i 为 i 的强度=邻边权之和）看作
"把单位区间 [0,1] 随机切成 k_i 段"的一种结果。某条边在 i 看来显著，当且仅当它的归一
化权重显著大于这种随机切分的期望：

    α_ij = (1 - p_ij) ** (k_i - 1) < α          # i 端 p 值

无向图采用文献标准的 **OR 合并**：边 (i,j) 只要从 i 或 j **任一端**显著就保留。这恰好
留住"叶子论文 → 它最相似的枢纽"这类边——枢纽自视每条边都不显著（它边太多），但叶子
自视这条边是它的主导关系，于是被保留。这正是多尺度特性的来源。

度为 1 的节点：它只有一条边，无从做显著性检验（k_i-1=0），该边直接保留（否则会把它
变成孤点）。

连通性兜底
--------------------------------------------------
骨架可能裂成多个连通块，但"研究地图"要连通才有导航意义。用**原图最强跨块边**做最大权
生成森林把各块桥接起来（在补集上跑 Kruskal）：只用真实存在的、跨两块里**最强**的那条
边，绝不凭空造边，也不像旧的 connect_isolated 那样给孤点补一条任意弱边。最多增加
(连通块数 - 1) 条边。
"""

from __future__ import annotations

from collections import defaultdict


def _build_adj(
    pairs: list[tuple[int, int, float]], min_sim: float
) -> tuple[dict[int, dict[int, float]], dict[tuple[int, int], float]]:
    """对称邻接表 {node: {other: sim}} + 边查找表 {(min,max): sim}。

    低于 min_sim 的对、自环、重复对（取较大值）在此统一清洗。
    """
    adj: dict[int, dict[int, float]] = defaultdict(dict)
    sim_of: dict[tuple[int, int], float] = {}
    for a, b, s in pairs:
        if a == b or s < min_sim:
            continue
        key = (a, b) if a < b else (b, a)
        if s <= sim_of.get(key, -1.0):
            continue
        sim_of[key] = s
        adj[a][b] = s
        adj[b][a] = s
    return adj, sim_of


def _disparity_keep(adj: dict[int, dict[int, float]], alpha: float) -> set[tuple[int, int]]:
    """Disparity Filter 显著边集合（OR 合并）。键为 (min, max)。"""
    keep: set[tuple[int, int]] = set()
    for i, nbrs in adj.items():
        k_i = len(nbrs)
        if k_i <= 1:
            # 度为 1：唯一邻边无从检验，必留（避免制造孤点）
            for j in nbrs:
                keep.add((i, j) if i < j else (j, i))
            continue
        s_i = sum(nbrs.values()) or 1e-12
        exponent = k_i - 1
        for j, w in nbrs.items():
            p = w / s_i
            # α_ij = (1 - p)^(k_i - 1)；从 i 端看显著则保留（OR 合并自然由双端各自迭代实现）
            if (1.0 - p) ** exponent < alpha:
                keep.add((i, j) if i < j else (j, i))
    return keep


def _ensure_connected(
    keep: set[tuple[int, int]],
    sim_of: dict[tuple[int, int], float],
    nodes: list[int],
) -> set[tuple[int, int]]:
    """最大权生成森林桥接各连通块：在原图补集上按相似度降序加最强跨块边。"""
    parent = {n: n for n in nodes}

    def find(x: int) -> int:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:          # 路径压缩
            parent[x], x = root, parent[x]
        return root

    def union(a: int, b: int) -> bool:
        ra, rb = find(a), find(b)
        if ra == rb:
            return False
        parent[ra] = rb
        return True

    for a, b in keep:
        union(a, b)

    # 候选桥：所有原图边按相似度降序，跳过已在骨架里的，挑跨块最强
    for (a, b), _s in sorted(sim_of.items(), key=lambda kv: kv[1], reverse=True):
        if (a, b) in keep:
            continue
        if union(a, b):
            keep.add((a, b))
    return keep


def extract_backbone(
    pairs: list[tuple[int, int, float]],
    *,
    alpha: float = 0.25,
    min_sim: float = 0.05,
    ensure_connected: bool = True,
) -> list[tuple[int, int, float]]:
    """从稠密加权相似图抽取多尺度骨架。

    Args:
        pairs: 对称相似边 (a, b, sim)，sim 越大越相似（通常是余弦 ∈ [0,1]）。
        alpha: 显著性水平 α ∈ (0,1)。越小骨架越稀越严格；典型 0.1（很稀）~ 0.5（较密）。
        min_sim: 相似度地板，低于此的对直接当噪声丢弃（不参与强度统计，避免拉高分母）。
        ensure_connected: True 时用最强跨块边把骨架补成连通图（研究地图需连通）。

    Returns:
        骨架边 [(a, b, sim)]，a<b，去重，sim 为原始相似度（供下游 PageRank/渲染用）。
    """
    adj, sim_of = _build_adj(pairs, min_sim)
    nodes = list(adj.keys())
    if len(nodes) <= 1:
        return []
    # 节点太少时显著性检验退化（度 1~2，p 值无意义），保留全部上地板边，
    # 不要把一张 2~3 篇的小地图拆散。
    if len(nodes) <= 3:
        return [(a, b, s) for (a, b), s in sim_of.items()]

    keep = _disparity_keep(adj, alpha)
    if ensure_connected:
        keep = _ensure_connected(keep, sim_of, nodes)
    return [(a, b, sim_of[(a, b)]) for (a, b) in keep if (a, b) in sim_of]
