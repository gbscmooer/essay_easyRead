"""
Graph Analytics — 图谱分析：关键论文识别 + 研究子方向发现（v2 强化版）

在 build_graph 产出 nodes/edges 之后调用，就地为每个节点的 meta 注入：
- importance:    综合影响力 [0,1]（前端按此放大节点），= 奠基性 ⊕ 主题枢纽性 的融合
- foundational:  有向引用 PageRank [0,1]——被很多论文引用 = 领域奠基（"先读这几篇"）
- centrality:    关联图加权 PageRank [0,1]——某子方向里的连接枢纽
- cluster:       研究子方向编号（-1 表示孤立点）
- cluster_label: 子方向标题（从簇内标题做 TF-ICF 关键词抽取，比"取最大论文标题"更准）

相比 v1 的三处关键改进（直接回应"图谱毛球、看不出重点"）：
1. **区分两种重要性**：奠基性用**有向** cites 图（谁被引得多），不再被无向折叠抹平；
   主题枢纽性用关联图。旧版把 cites + similar 折叠成一条无向边取 max 权，方向丢失、
   两种结构性质混淆。
2. **社区只在关联图上做**，不掺引用树——否则社区退化成"每个 seed 的引用子树"这种
   平凡结构。算完再把**过小/单点社区并入最强邻居**。
3. **可解释簇标签**：TF-ICF 选出本簇相对全局更"独特"的关键词，而非簇内最大论文标题。

v3 的关键修正：关联图**只收 related 边**。旧版把 bibcoupling / cocitation 也放进同
一张图并按 max 折叠权重——related 边权恒在 0.8 上下，Salton 常在 0.05~0.3，取 max
等于把结构证据整个抹掉。现在结构信号已经在 relatedness 里作为通道进入 related 边权，
这里再收一次既会重复计数，又会因尺度不一致污染社区划分。

依赖 networkx（requirements 已有，>=3.2 内置 Louvain）。若环境装了 igraph+leidenalg，
社区检测会自动用更稳的 Leiden；否则回退 Louvain。无新增硬依赖。
"""

import logging
import math
import re
from collections import defaultdict

import networkx as nx

logger = logging.getLogger(__name__)

# 进入"关联图"（社区 + 枢纽中心性）的边类型。"similar" 是 v2 及以前持久化图谱里的
# 旧名，读旧图时等价于 related。bibcoupling / cocitation 只是可视化图层，其分值已计入
# related 边权，不再重复进图。
RELATION_EDGE_KINDS = frozenset({"related", "similar"})

# 结构边类型（用于统计/可解释性）
STRUCTURAL_EDGE_KINDS = frozenset({"bibcoupling", "cocitation"})

_STOP = set(
    "the a an of to in for on with and or is are this that we our by from at as be using "
    "via towards toward into over under via use uses used based approach method methods model "
    "models network networks deep learning neural via paper study novel new framework system "
    "via efficient robust general generation detection via via via".split()
)


# ---------------------------------------------------------------------------
# 图构造：把 list[edge] 拆成"有向引用图" + "无向关联图"
# ---------------------------------------------------------------------------
def _split_graphs(nodes: list[dict], edges: list[dict]) -> tuple[nx.DiGraph, nx.Graph]:
    """拆成"有向引用图"（奠基性）与"无向关联图"（子方向 + 枢纽性）。"""
    cite = nx.DiGraph()
    rel = nx.Graph()
    for n in nodes:
        cite.add_node(n["id"])
        rel.add_node(n["id"])

    bridges: list[tuple[str, str, float]] = []
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if not s or not t or s == t:
            continue
        kind = e.get("kind")
        w = float(e.get("weight", 1.0) or 1.0)
        if kind == "cites":
            cite.add_edge(s, t, weight=w)            # 方向：citing → cited
        elif kind in RELATION_EDGE_KINDS:
            # bridge 仅用于视觉连通，正常情况下不进中心性 / 社区 / PPR seed boost。
            if e.get("bridge"):
                bridges.append((s, t, w))
                continue
            if rel.has_edge(s, t):
                rel[s][t]["weight"] = max(rel[s][t]["weight"], w)   # 同对双向边取一次
            else:
                rel.add_edge(s, t, weight=w)

    # 退化兜底：骨架显著性检验一条边都没留下时（权重高度均匀的小工作区会这样），
    # 全部关系边都被标成桥接边。此时若仍然排除桥接边，关联图会是空图 —— 社区、
    # 中心性、簇标签全部失效。宁可用这些边算，也不要产出"全是孤立点"的图谱。
    if rel.number_of_edges() == 0 and bridges:
        for s, t, w in bridges:
            if rel.has_edge(s, t):
                rel[s][t]["weight"] = max(rel[s][t]["weight"], w)
            else:
                rel.add_edge(s, t, weight=w)
        logger.info("graph analytics: 骨架为空，回退用 %d 条桥接边计算社区/中心性", len(bridges))
    return cite, rel


def _minmax(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    lo, hi = min(scores.values()), max(scores.values())
    span = (hi - lo) or 1.0
    return {k: (v - lo) / span for k, v in scores.items()}


def _safe_pagerank(g) -> dict[str, float]:
    """加权 PageRank，空图/数值异常时回退空 dict。

    对有向引用图：奠基论文 = 入边多 → 标准 PageRank 沿引用流向累积即可；
    对无向关联图：度量主题枢纽性。两者算法相同（nx.pagerank 同时支持 Graph/DiGraph）。
    """
    if g.number_of_edges() == 0:
        return {}
    try:
        return nx.pagerank(g, weight="weight")
    except Exception as ex:  # pragma: no cover - 数值兜底
        logger.warning(f"pagerank failed: {ex}")
        return {}


# ---------------------------------------------------------------------------
# 社区检测：Leiden（可选）→ Louvain（默认）；再合并过小社区
# ---------------------------------------------------------------------------
def _detect_communities(rel: nx.Graph) -> list[set]:
    if rel.number_of_edges() == 0:
        return [{n} for n in rel.nodes()]
    try:  # 可选更优：Leiden（需 igraph + leidenalg），未安装则静默回退
        import igraph as ig
        import leidenalg
        idx = {n: i for i, n in enumerate(rel.nodes())}
        rev = {i: n for n, i in idx.items()}
        g_ig = ig.Graph(n=len(idx))
        g_ig.add_edges([(idx[u], idx[v]) for u, v in rel.edges()])
        g_ig.es["weight"] = [rel[u][v]["weight"] for u, v in rel.edges()]
        part = leidenalg.find_partition(
            g_ig, leidenalg.RBConfigurationVertexPartition, weights="weight", seed=42
        )
        return [set(rev[i] for i in comm) for comm in part]
    except Exception:
        return [set(c) for c in nx.community.louvain_communities(rel, weight="weight", seed=42)]


def _merge_small(rel: nx.Graph, communities: list[set], *, min_size: int = 2) -> list[set]:
    """把过小（< min_size）社区并入"边权之和最大的邻居社区"，消除碎屑/单点社区。

    没有任何跨社区邻居的真·孤立点保留为独立社区（它们本就该是 cluster=-1 的候选）。
    """
    comms = [set(c) for c in communities]
    node2c = {n: i for i, c in enumerate(comms) for n in c}
    for i in sorted(range(len(comms)), key=lambda x: len(comms[x])):
        if not comms[i] or len(comms[i]) >= min_size:
            continue
        weight_to: dict[int, float] = defaultdict(float)
        for node in comms[i]:
            for nb in rel.neighbors(node):
                cj = node2c.get(nb)
                if cj is not None and cj != i and comms[cj]:
                    weight_to[cj] += rel[node][nb].get("weight", 1.0)
        if weight_to:
            best = max(weight_to, key=weight_to.get)
            comms[best] |= comms[i]
            for node in comms[i]:
                node2c[node] = best
            comms[i] = set()
    return [c for c in comms if c]


# ---------------------------------------------------------------------------
# 簇标签：TF-ICF 关键词抽取（本簇高频 + 跨簇稀有 = 独特主题词）
# ---------------------------------------------------------------------------
def _tokenize(text: str) -> list[str]:
    toks = [w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9\-]+", str(text).lower()) if len(w) > 2 and w not in _STOP]
    cjk = re.findall(r"[一-鿿]", str(text))
    toks += [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]  # 中文 bigram
    return toks


def _label_clusters(id2title: dict[str, str], communities: list[set]) -> dict[int, str]:
    cluster_tokens: list[list[str]] = []
    df: dict[str, int] = defaultdict(int)          # 含某 token 的簇数（cluster frequency）
    for comm in communities:
        toks: list[str] = []
        for nid in comm:
            toks += _tokenize(id2title.get(nid, ""))
        cluster_tokens.append(toks)
        for tk in set(toks):
            df[tk] += 1
    n_clusters = max(1, len(communities))
    labels: dict[int, str] = {}
    for ci, toks in enumerate(cluster_tokens):
        if not toks:
            labels[ci] = ""
            continue
        tf: dict[str, int] = defaultdict(int)
        for tk in toks:
            tf[tk] += 1
        scored = {tk: c * math.log(1 + n_clusters / df[tk]) for tk, c in tf.items() if c >= 1}
        top = sorted(scored, key=scored.get, reverse=True)[:2]
        labels[ci] = " · ".join(top) if top else ""
    return labels


# ---------------------------------------------------------------------------
# 主入口（签名保持向后兼容）
# ---------------------------------------------------------------------------
def annotate_graph(nodes: list[dict], edges: list[dict]) -> dict:
    """就地为 nodes 注入 importance/foundational/centrality/cluster/cluster_label，返回统计。"""
    if not nodes:
        return {"clusters": 0, "top_papers": [], "modularity": 0.0}

    cite, rel = _split_graphs(nodes, edges)

    foundational = _minmax(_safe_pagerank(cite))   # 有向引用图：奠基性
    centrality = _minmax(_safe_pagerank(rel))      # 无向关联图：子方向内的枢纽性

    # 综合影响力：奠基性优先（被引是更硬的信号），主题枢纽性次之；缺一不互相拉低。
    importance: dict[str, float] = {}
    for n in nodes:
        nid = n["id"]
        importance[nid] = 0.6 * foundational.get(nid, 0.0) + 0.4 * centrality.get(nid, 0.0)
    importance = _minmax(importance) if importance else {}

    communities = _merge_small(rel, _detect_communities(rel), min_size=2)
    node2cluster: dict[str, int] = {}
    for cid, members in enumerate(communities):
        for nid in members:
            node2cluster[nid] = cid
    # 单点（社区只剩自己且无语义边）记为 -1，前端按孤立点处理
    singletons = {nid for c in communities if len(c) == 1 for nid in c}
    for nid in singletons:
        if rel.degree(nid) == 0:
            node2cluster[nid] = -1

    for n in nodes:
        nid = n["id"]
        meta = n.setdefault("meta", {})
        meta["importance"] = round(float(importance.get(nid, 0.0)), 4)
        meta["foundational"] = round(float(foundational.get(nid, 0.0)), 4)
        meta["centrality"] = round(float(centrality.get(nid, 0.0)), 4)
        meta["cluster"] = node2cluster.get(nid, -1)

    id2title = {n["id"]: n.get("label", "") for n in nodes}
    labels = _label_clusters(id2title, communities)
    for cid, members in enumerate(communities):
        lbl = labels.get(cid) or _fallback_label(members, nodes)
        for nid in members:
            if node2cluster.get(nid, -1) >= 0:
                next(n for n in nodes if n["id"] == nid)["meta"]["cluster_label"] = lbl

    # modularity（社区质量，写进 stats 供 Phase C 评测）
    try:
        real = [c for c in communities if len(c) > 1]
        modularity = (
            nx.community.modularity(rel, real, weight="weight")
            if real and rel.number_of_edges() else 0.0
        )
    except Exception:
        modularity = 0.0

    top = sorted(nodes, key=lambda x: x["meta"]["importance"], reverse=True)[:5]
    top_papers = [
        {"id": n["id"], "label": n["label"], "importance": n["meta"]["importance"]}
        for n in top
    ]

    by_kind: dict[str, int] = defaultdict(int)
    for e in edges:
        by_kind[str(e.get("kind", "unknown"))] += 1

    n_real = sum(1 for c in communities if len(c) > 1)
    logger.info(
        f"graph analytics v2: {n_real} 子方向(+{len(communities)-n_real} 孤立), "
        f"modularity={modularity:.3f}, top={top_papers[0]['importance'] if top_papers else 0}"
    )
    return {
        "clusters": n_real,
        "top_papers": top_papers,
        "modularity": round(float(modularity), 4),
        "edge_counts_by_kind": dict(by_kind),
    }


def _fallback_label(members: set, nodes: list[dict]) -> str:
    """关键词抽取为空时，退回簇内 importance 最高论文的标题。"""
    best, best_imp = "", -1.0
    for n in nodes:
        if n["id"] in members:
            imp = n.get("meta", {}).get("importance", 0.0)
            if imp > best_imp:
                best_imp, best = imp, n.get("label", "")
    return best
