"""
Graph analytics with mandatory Leiden and weighted PageRank.

Leiden: Traag, Waltman & van Eck (2019), doi:10.1038/s41598-019-41695-z.
Community detection optimizes the weighted RB configuration objective at
resolution 1, with seed 42. No heuristic small-community merging is applied.

importance and centrality use relation-graph PageRank; foundational separately
reports citation-graph PageRank. Raw stationary mass is also retained. Scores
displayed in [0,1] are min-max-scaled PageRank, not relevance probabilities.
Display bridges never affect community membership or any centrality.
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
def _split_graphs(
    nodes: list[dict], edges: list[dict]
) -> tuple[nx.DiGraph, nx.Graph, list[tuple[str, str, float]]]:
    """拆成"有向引用图"（奠基性）与"无向关联图"（子方向 + 枢纽性），另返回桥接边。

    桥接边仅用于展示，不进入社区划分、中心性或孤立节点的归属。
    """
    cite = nx.DiGraph()
    rel = nx.Graph()
    for n in nodes:
        cite.add_node(n["id"])
        rel.add_node(n["id"])

    bridges: list[tuple[str, str, float]] = []
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if not s or not t or s == t or s not in rel or t not in rel:
            continue
        kind = e.get("kind")
        raw_weight = e.get("weight", 1.0)
        w = float(raw_weight) if raw_weight is not None else 0.0
        if not math.isfinite(w) or w <= 0:
            continue
        if e.get("bridge"):
            bridges.append((s, t, w))
            continue
        if kind == "cites":
            cite.add_edge(s, t, weight=w)            # 方向：citing → cited
        elif kind in RELATION_EDGE_KINDS:
            if rel.has_edge(s, t):
                rel[s][t]["weight"] = max(rel[s][t]["weight"], w)   # 同对双向边取一次
            else:
                rel.add_edge(s, t, weight=w)

    return cite, rel, bridges


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
# Community detection: mandatory Leiden, without post-hoc merges
# ---------------------------------------------------------------------------
def _detect_communities(rel: nx.Graph) -> list[set]:
    if rel.number_of_edges() == 0:
        return [{n} for n in sorted(rel.nodes())]
    try:
        import igraph as ig
        import leidenalg
    except ImportError as ex:
        raise RuntimeError(
            "Leiden community detection requires igraph and leidenalg; "
            "install the backend requirements."
        ) from ex
    names = sorted(rel.nodes())
    idx = {node: i for i, node in enumerate(names)}
    edges = sorted((min(idx[u], idx[v]), max(idx[u], idx[v]), d.get("weight", 1.0))
                   for u, v, d in rel.edges(data=True))
    g_ig = ig.Graph(n=len(names), edges=[(u, v) for u, v, _ in edges])
    g_ig.es["weight"] = [w for _, _, w in edges]
    part = leidenalg.find_partition(
        g_ig, leidenalg.RBConfigurationVertexPartition, weights="weight",
        resolution_parameter=1.0, seed=42, n_iterations=-1,
    )
    communities = [set(names[i] for i in comm) for comm in part]
    return sorted(communities, key=lambda comm: min(comm))


def _merge_small(rel: nx.Graph, communities: list[set], *, min_size: int = 2) -> list[set]:
    """Compatibility shim for older callers; preserve the Leiden partition."""
    return [set(community) for community in communities]


# ---------------------------------------------------------------------------
# 簇标签：TF-ICF 关键词抽取（本簇高频 + 跨簇稀有 = 独特主题词）
# ---------------------------------------------------------------------------
def _tokenize(text: str) -> list[str]:
    toks = [w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9\-]+", str(text).lower()) if len(w) > 2 and w not in _STOP]
    cjk = re.findall(r"[一-鿿]", str(text))
    toks += [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]  # 中文 bigram
    return toks


def _same_stem(a: str, b: str) -> bool:
    """粗略同源判断：graph / graphs、embedding / embeddings 只保留一个，别浪费标签位。"""
    x, y = (a, b) if len(a) <= len(b) else (b, a)
    return y.startswith(x) and len(y) - len(x) <= 2


def _label_clusters(node_text: dict[str, str], communities: list[set]) -> dict[int, str]:
    """TF-ICF 关键词：本簇高频 + 跨簇稀有。文本用节点标题 + 速读总结（比只用标题更稳）。"""
    cluster_tokens: list[list[str]] = []
    df: dict[str, int] = defaultdict(int)          # 含某 token 的簇数（cluster frequency）
    for comm in communities:
        toks: list[str] = []
        for nid in comm:
            toks += _tokenize(node_text.get(nid, ""))
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
        picked: list[str] = []
        for tk in sorted(scored, key=scored.get, reverse=True):
            if any(_same_stem(tk, p) for p in picked):
                continue
            picked.append(tk)
            if len(picked) == 2:
                break
        labels[ci] = " · ".join(picked)
    return labels


# ---------------------------------------------------------------------------
# 主入口（签名保持向后兼容）
# ---------------------------------------------------------------------------
def annotate_graph(nodes: list[dict], edges: list[dict]) -> dict:
    """就地为 nodes 注入 importance/foundational/centrality/cluster/cluster_label，返回统计。"""
    if not nodes:
        return {"clusters": 0, "top_papers": [], "modularity": 0.0}

    cite, rel, _bridges = _split_graphs(nodes, edges)

    foundational_raw = _safe_pagerank(cite)
    centrality_raw = _safe_pagerank(rel)
    foundational = _minmax(foundational_raw)
    centrality = _minmax(centrality_raw)
    importance = centrality

    communities = _detect_communities(rel)
    isolated = {n["id"] for n in nodes if rel.degree(n["id"]) == 0}

    node2cluster: dict[str, int] = {}
    for cid, members in enumerate(communities):
        for nid in members:
            node2cluster[nid] = -1 if (len(members) == 1 and nid in isolated) else cid

    for n in nodes:
        nid = n["id"]
        meta = n.setdefault("meta", {})
        meta["importance"] = round(float(importance.get(nid, 0.0)), 4)
        meta["foundational"] = round(float(foundational.get(nid, 0.0)), 4)
        meta["centrality"] = round(float(centrality.get(nid, 0.0)), 4)
        meta["cluster"] = node2cluster.get(nid, -1)
        meta["cluster_label"] = ""
        meta["pagerank"] = centrality_raw.get(nid, 0.0)
        meta["citation_pagerank"] = foundational_raw.get(nid, 0.0)
        meta["importance_method"] = "relation_pagerank"

    # 簇标签只用标题：CV/ML 标题本身就是领域术语（"anomaly detection" / "few-shot"），
    # 而速读总结是中文长文本，滑窗 bigram 会切出"棒性""器翻"这类碎片并盖过标题词。
    node_text = {n["id"]: n.get("label", "") for n in nodes}
    by_id = {n["id"]: n for n in nodes}
    labels = _label_clusters(node_text, communities)
    for cid, members in enumerate(communities):
        lbl = labels.get(cid) or _fallback_label(members, nodes)
        for nid in members:
            if node2cluster.get(nid, -1) >= 0 and nid in by_id:
                by_id[nid].setdefault("meta", {})["cluster_label"] = lbl

    # modularity（社区质量，写进 stats 供评测）。注意必须传**完整划分**：
    # 只挑 len>1 的社区会让 networkx 抛 NotAPartition，旧实现在 except 里静默记 0，
    # 于是"有孤立点的图 modularity 恒为 0"——这正是之前评测里 modularity 归零的根因。
    try:
        modularity = (
            nx.community.modularity(rel, communities, weight="weight")
            if communities and rel.number_of_edges() else 0.0
        )
    except Exception as ex:  # pragma: no cover - 数值兜底
        logger.warning("modularity failed: %s", ex)
        modularity = 0.0

    top = sorted(nodes, key=lambda x: x["meta"]["importance"], reverse=True)[:5]
    top_papers = [
        {"id": n["id"], "label": n["label"], "importance": n["meta"]["importance"]}
        for n in top
    ]

    by_kind: dict[str, int] = defaultdict(int)
    for e in edges:
        by_kind[str(e.get("kind", "unknown"))] += 1

    n_real = sum(1 for c in communities if any(n not in isolated for n in c))
    logger.info(
        f"graph analytics Leiden: {n_real} 子方向(+{len(communities)-n_real} 孤立), "
        f"modularity={modularity:.3f}, top={top_papers[0]['importance'] if top_papers else 0}"
    )
    return {
        "clusters": n_real,
        "top_papers": top_papers,
        "modularity": round(float(modularity), 4),
        "edge_counts_by_kind": dict(by_kind),
        "community_method": "leiden_rb_configuration",
        "community_resolution": 1.0,
        "community_seed": 42,
        "importance_method": "relation_pagerank",
        "display_bridge_count": len(_bridges),
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
