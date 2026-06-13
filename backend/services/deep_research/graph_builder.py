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
import re
from .similarity import get_similarity_provider
from .graph_analytics import annotate_graph
from .graphkit import denoise_knn
from ..ai_config import get_similarity_config

logger = logging.getLogger(__name__)

# 相似边的最小分数地板：低于此值即便进了某节点的 top-k 也不连边，
# 避免论文很少时把几乎不相关的对也连上。kNN 是主控，这是兜底。
MIN_SIMILARITY = 0.05


def _normalize_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def _match_contexts_to_paper(contexts: list[dict], target_paper) -> list[dict]:
    ext_id = getattr(target_paper, "external_id", None) or ""
    target_arxiv = ""
    target_doi = ""
    if ext_id.startswith("arxiv:"):
        target_arxiv = ext_id[6:]
    elif ext_id.startswith("doi:"):
        target_doi = ext_id[4:]

    target_title_norm = _normalize_title(getattr(target_paper, "title", "") or "")

    matched: list[dict] = []
    for ctx in contexts:
        ctx_arxiv = ctx.get("arxiv_id") or ""
        ctx_doi = ctx.get("doi") or ""
        if target_arxiv and ctx_arxiv and target_arxiv == ctx_arxiv:
            matched.append(ctx)
        elif target_doi and ctx_doi and target_doi == ctx_doi:
            matched.append(ctx)
        elif target_title_norm and ctx.get("title"):
            if _normalize_title(ctx["title"]) == target_title_norm:
                matched.append(ctx)
    return matched[:20]


def _sparsify_knn(pairs: list[tuple[int, int, float]], knn_k: int, floor: float, *, mutual: bool = True):
    """kNN 稀疏化：委托 graphkit.denoise_knn。

    默认 mutual=True（互近邻）——边只在双方都把对方选进 top-k 时成立，这是去"毛球"
    的核心，比旧的 union-kNN 干净得多，并对度为 0 的节点补一条最强边避免孤立点。
    mutual=False 可复现旧 union-kNN 行为（与旧 provider 对比时用）。
    """
    return denoise_knn(pairs, k=knn_k, mutual=mutual, min_sim=floor, connect_isolated=mutual)


def _paper_node(p) -> dict:
    digest = getattr(p, "graph_digest", None) or None
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
            # 解读层级：full=全量精读 / digest=图谱速读 / none=未解读
            "read_level": "full" if p.status == "done" else ("digest" if digest else "none"),
            # 速读总结（供节点详情展示，未速读为 None）
            "summary": digest.get("summary") if digest else None,
        },
    }


async def build_graph(ws_id: int, papers, *, similarity_threshold: float, enable_shared_method: bool,
                      knn_k: int = 8, citation_contexts: dict[int, list[dict]] | None = None) -> dict:
    nodes = [_paper_node(p) for p in papers]
    by_id = {p.id: p for p in papers}
    edges: list[dict] = []
    ctx_map = citation_contexts or {}

    # 1) cites 边：基于 parent_paper_id 的"父 → 子"引用
    cites_count = 0
    for p in papers:
        if p.parent_paper_id and p.parent_paper_id in by_id:
            edge: dict = {
                "source": f"paper:{p.parent_paper_id}",
                "target": f"paper:{p.id}",
                "kind": "cites",
                "weight": 1.0,
            }
            seed_contexts = ctx_map.get(p.parent_paper_id)
            if seed_contexts:
                matched = _match_contexts_to_paper(seed_contexts, p)
                if matched:
                    edge["contexts"] = [
                        {
                            "section": c.get("section", ""),
                            "context": c.get("context", ""),
                            "intent": c.get("intent", "background"),
                        }
                        for c in matched
                    ]
                    edge["weight"] = min(1.0 + len(matched) * 0.15, 2.0)
            edges.append(edge)
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
        "modularity": analytics.get("modularity", 0.0),
    }
    return {"nodes": nodes, "edges": edges, "stats": stats}
