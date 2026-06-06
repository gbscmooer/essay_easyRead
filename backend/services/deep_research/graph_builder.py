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
from .similarity import get_similarity_provider
from ..ai_config import get_similarity_config

logger = logging.getLogger(__name__)


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


async def build_graph(ws_id: int, papers, *, similarity_threshold: float, enable_shared_method: bool) -> dict:
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

    # 2) similar 边：委托给配置的 SimilarityProvider
    provider = get_similarity_provider(get_similarity_config())
    logger.info(f"graph similar edges via provider={provider.name}")
    pairs = await provider.score_pairs(papers, ws_id=ws_id)
    similar_count = 0
    for a_id, b_id, sim in pairs:
        if sim < similarity_threshold:
            continue
        # 双向 similar 边方便前端 force-graph 渲染
        edges.append({
            "source": f"paper:{a_id}", "target": f"paper:{b_id}",
            "kind": "similar", "weight": sim,
        })
        edges.append({
            "source": f"paper:{b_id}", "target": f"paper:{a_id}",
            "kind": "similar", "weight": sim,
        })
        similar_count += 2

    # 3) shared-method 边：占位
    if enable_shared_method:
        logger.warning("shared-method 边生成暂未实现（method_extractor 模块待开发），跳过")

    stats = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "by_kind": {
            "cites": cites_count,
            "similar": similar_count,
            "shared-method": 0,
        },
    }
    return {"nodes": nodes, "edges": edges, "stats": stats}
