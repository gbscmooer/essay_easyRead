"""
Graph Analytics — 图谱分析：关键论文识别 + 研究子方向发现

在 build_graph 产出 nodes/edges 之后调用，就地为每个节点的 meta 注入：
- importance:    PageRank 归一化到 [0, 1]，标识枢纽/奠基论文（前端按此放大节点）
- cluster:       Louvain 社区编号（-1 表示孤立点），标识研究子方向
- cluster_label: 子方向的简短标题（默认取簇内 importance 最高论文的标题）

依赖 networkx（已在 requirements，>=3.2 内置 Louvain，无需 python-louvain）。
所有重边（同一对节点同时存在 cites + similar）在分析时折叠为一条，取最大权重。
"""

import logging
import networkx as nx

logger = logging.getLogger(__name__)


def _build_nx_graph(nodes: list[dict], edges: list[dict]) -> nx.Graph:
    """把 list[edge] 折叠成无向带权图（PageRank / Louvain 都用它）。"""
    g = nx.Graph()
    for n in nodes:
        g.add_node(n["id"])
    for e in edges:
        s, t, w = e["source"], e["target"], float(e.get("weight", 1.0) or 1.0)
        if s == t:
            continue
        if g.has_edge(s, t):
            g[s][t]["weight"] = max(g[s][t]["weight"], w)
        else:
            g.add_edge(s, t, weight=w)
    return g


def annotate_graph(nodes: list[dict], edges: list[dict]) -> dict:
    """就地为 nodes 注入 importance / cluster / cluster_label，返回分析统计。"""
    if not nodes:
        return {"clusters": 0, "top_papers": []}

    g = _build_nx_graph(nodes, edges)
    has_edges = g.number_of_edges() > 0

    # --- PageRank：枢纽 / 奠基论文 ---
    try:
        pr = nx.pagerank(g, weight="weight") if has_edges else {}
    except Exception as ex:  # pragma: no cover - 数值兜底
        logger.warning(f"pagerank failed, fallback to uniform: {ex}")
        pr = {}
    if pr:
        lo, hi = min(pr.values()), max(pr.values())
        span = (hi - lo) or 1.0
    else:
        lo, span = 0.0, 1.0

    # --- Louvain：研究子方向 ---
    try:
        communities = (
            nx.community.louvain_communities(g, weight="weight", seed=42)
            if has_edges else []
        )
    except Exception as ex:  # pragma: no cover - 算法兜底
        logger.warning(f"louvain failed, no clustering: {ex}")
        communities = []
    node2cluster: dict[str, int] = {}
    for cid, members in enumerate(communities):
        for nid in members:
            node2cluster[nid] = cid

    # --- 注入 meta ---
    for n in nodes:
        nid = n["id"]
        importance = (pr.get(nid, lo) - lo) / span if pr else 0.0
        meta = n.setdefault("meta", {})
        meta["importance"] = round(float(importance), 4)
        meta["cluster"] = node2cluster.get(nid, -1)

    # --- 每个簇用 importance 最高的论文标题作默认标签 ---
    cluster_best: dict[int, tuple[str, float]] = {}
    for n in nodes:
        c = n["meta"]["cluster"]
        if c < 0:
            continue
        imp = n["meta"]["importance"]
        if c not in cluster_best or imp > cluster_best[c][1]:
            cluster_best[c] = (n["label"], imp)
    for n in nodes:
        c = n["meta"]["cluster"]
        if c in cluster_best:
            n["meta"]["cluster_label"] = cluster_best[c][0]

    # --- top 影响力论文（写进 stats，供前端"关键论文"列表用） ---
    top = sorted(nodes, key=lambda x: x["meta"]["importance"], reverse=True)[:5]
    top_papers = [
        {"id": n["id"], "label": n["label"], "importance": n["meta"]["importance"]}
        for n in top
    ]

    logger.info(
        f"graph analytics: {len(communities)} clusters, "
        f"top paper importance={top_papers[0]['importance'] if top_papers else 0}"
    )
    return {"clusters": len(communities), "top_papers": top_papers}
