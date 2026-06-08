"""
Graph Builder — 知识图谱构建

节点类型：paper
边类型：
- cites:         由 parent_paper_id 推断的引用关系（单向，父 → 子）
- similar:       论文级相似度（双向，由 SimilarityProvider 计算）
- shared-method: 共享方法（占位，未实现）

注意：早期版本用 networkx.DiGraph，但 DiGraph 同一对 (s, t) 只能存一条边，
导致 cites 边被随后的 similar 边覆盖。当前实现改为直接维护 list[edge]，
允许同一对节点之间存在多种 kind 的边。
"""

import logging
from collections import defaultdict
from .similarity import get_similarity_provider
from .graph_analytics import annotate_graph
from ..ai_config import get_similarity_config

logger = logging.getLogger(__name__)

# 相似边的最小分数地板：低于此值即便进了某节点的 top-k 也不连边，
# 避免论文很少时把几乎不相关的对也连上。kNN 是主控，这是兜底。
MIN_SIMILARITY = 0.05


def _sparsify_knn(pairs: list[tuple[int, int, float]], knn_k: int, floor: float):
    """kNN 稀疏化：每个节点只保留分数最高的 knn_k 条相似边（取并集）。

    这是解决"绝对阈值失效→图谱变全连接毛球"的核心：不再用一刀切的阈值，
    而是让每篇论文只连它最像的 K 篇。返回去重后的 (a, b, score)，a < b。
    """
    if knn_k <= 0:
        return [(a, b, s) for a, b, s in pairs if s >= floor]
    nbr: dict[int, list[tuple[int, float]]] = defaultdict(list)
    score_of: dict[tuple[int, int], float] = {}
    for a, b, s in pairs:
        if s < floor:
            continue
        key = (min(a, b), max(a, b))
        score_of[key] = s
        nbr[a].append((b, s))
        nbr[b].append((a, s))
    keep: set[tuple[int, int]] = set()
    for node, lst in nbr.items():
        lst.sort(key=lambda x: x[1], reverse=True)
        for other, _ in lst[:knn_k]:
            keep.add((min(node, other), max(node, other)))
    return [(a, b, score_of[(a, b)]) for (a, b) in keep]


def _paper_node(p) -> dict:
    return {
        "id": f"paper:{p.id}",
        "kind": "paper",
        "label": (p.title or p.filename)[:80],
        "meta": {
            "paper_id": p.id,
            "year": p.year,
            "venue": p.venue,
            "source": p.source,
            "status": p.status,
            "external_id": p.external_id,
        },
    }


async def build_graph(ws_id: int, papers, *, similarity_threshold: float, enable_shared_method: bool,
                      knn_k: int = 8) -> dict:
    nodes = [_paper_node(p) for p in papers]
    by_id = {p.id: p for p in papers}
    edges: list[dict] = []

    # 1) cites 边：基于 parent_paper_id 的"父 → 子"引用
    cites_count = 0
    for p in papers:
        if p.parent_paper_id and p.parent_paper_id in by_id:
            edges.append({
                "source": f"paper:{p.parent_paper_id}",
                "target": f"paper:{p.id}",
                "kind": "cites",
                "weight": 1.0,
            })
            cites_count += 1

    # 2) similar 边：委托给配置的 SimilarityProvider，再用 kNN 稀疏化
    provider = get_similarity_provider(get_similarity_config())
    logger.info(f"graph similar edges via provider={provider.name}, knn_k={knn_k}")
    pairs = await provider.score_pairs(papers, ws_id=ws_id)
    # provider 可选地通过 .details 暴露每对论文的相似度分解（如 facet_weighted），
    # 键为 (min_id, max_id)，graph_builder 把它原样挂到 edge.detail 上供前端展示。
    details: dict = getattr(provider, "details", {}) or {}
    kept = _sparsify_knn(pairs, knn_k=knn_k, floor=max(MIN_SIMILARITY, 0.0))
    similar_count = 0
    for a_id, b_id, sim in kept:
        detail = details.get((min(a_id, b_id), max(a_id, b_id)))
        # 双向 similar 边方便前端 force-graph 渲染，两条边共享同一份 detail
        e_ab = {"source": f"paper:{a_id}", "target": f"paper:{b_id}", "kind": "similar", "weight": sim}
        e_ba = {"source": f"paper:{b_id}", "target": f"paper:{a_id}", "kind": "similar", "weight": sim}
        if detail:
            e_ab["detail"] = detail
            e_ba["detail"] = detail
        edges.append(e_ab)
        edges.append(e_ba)
        similar_count += 2

    # 3) shared-method 边：占位
    if enable_shared_method:
        logger.warning("shared-method 边生成暂未实现（method_extractor 模块待开发），跳过")

    # 3.5) 图谱分析：PageRank 关键论文 + Louvain 子方向（就地写入 node.meta）
    analytics = annotate_graph(nodes, edges)

    stats = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "by_kind": {
            "cites": cites_count,
            "similar": similar_count,
            "shared-method": 0,
        },
        "clusters": analytics["clusters"],
        "top_papers": analytics["top_papers"],
    }
    return {"nodes": nodes, "edges": edges, "stats": stats}
