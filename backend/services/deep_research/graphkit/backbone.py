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

"环境关联度地板"扣除与密度控制（v3.1 关键修正）
--------------------------------------------------
Disparity Filter 的零模型隐含假设权重从 ~0 起跳。真实语料不是这样：同一个领域里**任
何两篇论文都有点像**，分数分布是一条不从 0 起跳的带（BGE 余弦实测 min≈0.76、
median≈0.85；v3 融合分实测 median≈0.27）。此时每个节点的归一化权重近乎均匀(≈1/k_i)，
p 值 `(1-1/k_i)^(k_i-1)≈1/e≈0.376` 对几乎所有边都一样 —— 骨架在 α 附近**双峰退化**：
稍小全删（只剩连通兜底的生成树）、稍大全留（又是毛球）。

修正是把显著性检验做在"**超出环境地板的关联度** w' = w − base"上（返回的边权仍是原始
分数，供渲染 / PageRank 用）。剩下的问题是 base 取多少：

- v3.0 取 **图内最小边权**。这在"窄高带余弦"上凑效（min 就是语料地板），但 v3 把边权
  换成多通道融合分之后彻底失灵 —— 融合分的最小值被 `min_sim` 地板钉死在 0.05 附近，
  base≈0 等于没扣。ws1 实测：α=0.05→201 边(平均度 2.2)、α=0.25→3740 边(平均度 41.8)、
  α=0.4→10241 边(平均度 114)，**α 在"树"和"毛球"之间没有可用区间**，用户拖滑块只能
  在两种坏结果里选。
- v3.1 改为**按目标密度反解 base**：用户给"每篇论文平均连几条边"（`target_degree`），
  二分求满足该边数预算的分位数地板。α 退回它本来的角色（局部显著性），密度由一个
  **与分数尺度无关**的参数控制 —— 以后再改打分口径也不会重蹈覆辙。

ws1 实测（179 篇，573 对工作区内真实引用；同一边预算下对比）：

    稀疏化方法            边数  平均度  引用对召回  单边命中提升  modularity  连通块  孤点
    全局 top-N             716    8.0     49.6%       11.0x        0.615      21    18
    互近邻 mutual-kNN      187    2.1     17.1%       14.6x        0.881      38    24
    本模块(target_degree=8) 734   8.2     50.6%       11.0x        0.629       1     0

同样的边预算下，本模块与全局阈值精度相当，但**多尺度 + 连通兜底**让地图只有一个连通
块、零孤立点（全局阈值留下 18 个谁也连不上的节点，正是"看不出重点"的另一种形态）。

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


def _quantile(sorted_vals: list[float], q: float) -> float:
    """已排序序列的分位数（线性插值，等价 numpy 默认口径，但不引依赖）。"""
    if not sorted_vals:
        return 0.0
    if q <= 0:
        return sorted_vals[0]
    if q >= 1:
        return sorted_vals[-1]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _shift(adj: dict[int, dict[int, float]], base: float) -> dict[int, dict[int, float]]:
    if base <= 0.0:
        return adj
    return {i: {j: max(0.0, w - base) for j, w in nb.items()} for i, nb in adj.items()}


def _solve_baseline_for_degree(
    adj: dict[int, dict[int, float]],
    sim_of: dict[tuple[int, int], float],
    *,
    alpha: float,
    target_degree: float,
    max_iter: int = 18,
) -> tuple[set[tuple[int, int]], float]:
    """二分环境地板分位数，使显著边数 ≈ target_degree × n / 2。

    边数随地板单调不增（地板越高，能"跑赢自己邻居"的边越少），二分稳定；
    为防个别非单调点，同时记录离预算最近的一次结果。
    """
    n = len(adj)
    budget = max(1, int(round(target_degree * n / 2)))
    vals = sorted(sim_of.values())
    tol = max(1, int(budget * 0.05))

    lo_q, hi_q = 0.0, 0.999
    best: tuple[set[tuple[int, int]], float] | None = None
    best_gap = None
    for _ in range(max_iter):
        mid = (lo_q + hi_q) / 2
        base = _quantile(vals, mid)
        keep = _disparity_keep(_shift(adj, base), alpha)
        gap = abs(len(keep) - budget)
        if best_gap is None or gap < best_gap:
            best, best_gap = (keep, base), gap
        if gap <= tol:
            break
        if len(keep) > budget:
            lo_q = mid
        else:
            hi_q = mid
    return best if best is not None else (set(), 0.0)


def extract_backbone_with_bridges(
    pairs: list[tuple[int, int, float]],
    *,
    target_degree: float | None = 8.0,
    alpha: float = 0.25,
    min_sim: float = 0.05,
    ensure_connected: bool = True,
    baseline: str | float | None = None,
) -> tuple[list[tuple[int, int, float]], set[tuple[int, int]]]:
    """抽取骨架，并区分"统计显著的边"与"仅为连通性补的桥接边"。

    Args:
        pairs: 对称关联边 (a, b, score)，score 越大越相关。
        target_degree: 目标平均度（每个节点平均连几条边）。这是**推荐的密度控制方式**，
            与打分尺度无关；内部二分求解环境地板。None 表示不控制密度、由
            ``baseline`` + ``alpha`` 决定。
        alpha: Disparity Filter 显著性水平 ∈ (0,1)，控制"局部显著"的判据。
        min_sim: 关联度地板，低于此的对直接当噪声丢弃（不参与强度统计，避免拉高分母）。
        ensure_connected: True 时用最强跨块边把骨架补成连通图（研究地图需连通）。
        baseline: 显式指定环境地板。给了就**优先于** target_degree。
            "auto"/"min"=图内最小边权（v3.0 旧口径，仅为兼容保留）；数值=固定地板；
            None=交给 target_degree。**只影响显著性判定**，返回边权始终是原始分数。

    Returns:
        (骨架边 [(a, b, score)]，桥接边键集合 {(min,max)})。桥接边同样在骨架边列表里，
        键集合用于让调用方把它们标注成"仅为连通性保留"（前端弱化、PPR 降权）。
    """
    adj, sim_of = _build_adj(pairs, min_sim)
    nodes = list(adj.keys())
    if len(nodes) <= 1:
        return [], set()
    # 节点太少时显著性检验退化（度 1~2，p 值无意义），保留全部上地板边，
    # 不要把一张 2~3 篇的小地图拆散。
    if len(nodes) <= 3:
        return [(a, b, s) for (a, b), s in sim_of.items()], set()

    if baseline is not None:
        vals = sorted(sim_of.values())
        if baseline in ("auto", "min"):
            base = vals[0] if (vals[-1] - vals[0]) > 1e-9 else 0.0
        else:
            base = float(baseline or 0.0)
        keep = _disparity_keep(_shift(adj, base), alpha)
    elif target_degree and target_degree > 0:
        keep, _base = _solve_baseline_for_degree(
            adj, sim_of, alpha=alpha, target_degree=target_degree,
        )
    else:
        keep = _disparity_keep(adj, alpha)

    bridges: set[tuple[int, int]] = set()
    if ensure_connected:
        connected = _ensure_connected(set(keep), sim_of, nodes)  # 桥按原始分数排序（单调，等价）
        bridges = connected - keep
        keep = connected
    return [(a, b, sim_of[(a, b)]) for (a, b) in keep if (a, b) in sim_of], bridges


def extract_backbone(
    pairs: list[tuple[int, int, float]],
    *,
    target_degree: float | None = 8.0,
    alpha: float = 0.25,
    min_sim: float = 0.05,
    ensure_connected: bool = True,
    baseline: str | float | None = None,
) -> list[tuple[int, int, float]]:
    """从稠密加权关联图抽取多尺度骨架（参数见 extract_backbone_with_bridges）。"""
    edges, _bridges = extract_backbone_with_bridges(
        pairs, target_degree=target_degree, alpha=alpha, min_sim=min_sim,
        ensure_connected=ensure_connected, baseline=baseline,
    )
    return edges
