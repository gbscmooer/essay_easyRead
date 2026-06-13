"""
graph — 由 PaperFeatures + facet pairs 组装图谱 nodes/edges（纯函数，无 DB）

节点 id 约定与生产一致："paper:{id}"。
analytics（PageRank / Louvain）直接复用 deep_research/graph_analytics.py（那个文件本就
只依赖 networkx、操作 nodes/edges dict，已是解耦的），故本模块不重复实现。

边的两种形态：
- similar_edges：当前生产形态——把 facet 加权分压成单条 "similar" 边（带 detail 分解）。
- faceted_edges：多关系形态——按每个 facet 维度拆成独立 typed 边（same-purpose 等），
  这是重设计目标（docs/DEEP_RESEARCH_REDESIGN.md），供 test_g 试验。
"""

from __future__ import annotations

from .features import PaperFeatures
from .vectors import build_topic_vectors, cosine_pairs
from .knn import denoise_knn
from .engine import FacetEngine
from .facets.base import EmbedFn


def build_paper_nodes(papers: list[PaperFeatures]) -> list[dict]:
    return [
        {
            "id": f"paper:{p.id}",
            "label": p.title or f"paper {p.id}",
            "kind": "paper",
            "meta": {"year": p.year, "analyzed": p.is_analyzed},
        }
        for p in papers
    ]


def cites_edges(papers: list[PaperFeatures]) -> list[dict]:
    """由 pf.cites 生成 cites 边（只保留两端都在集合内的）。"""
    ids = {p.id for p in papers}
    out = []
    for p in papers:
        for tgt in p.cites:
            if tgt in ids and tgt != p.id:
                out.append({"source": f"paper:{p.id}", "target": f"paper:{tgt}", "kind": "cites", "weight": 1.0})
    return out


def sparsify_knn(
    pairs: list[tuple[int, int, float]], *, knn_k: int = 8, floor: float = 0.0
) -> list[tuple[int, int, float]]:
    """每篇论文只保留最相似的 K 条边（无向：任一端入选即保留）。替代绝对阈值。"""
    nbr: dict[int, list[tuple[int, float]]] = {}
    for a, b, s in pairs:
        if s < floor:
            continue
        nbr.setdefault(a, []).append((b, s))
        nbr.setdefault(b, []).append((a, s))
    keep: set[tuple[int, int]] = set()
    for node, lst in nbr.items():
        for other, s in sorted(lst, key=lambda x: x[1], reverse=True)[:knn_k]:
            keep.add((min(node, other), max(node, other)))
    score = {(min(a, b), max(a, b)): s for a, b, s in pairs}
    return [(a, b, score[(a, b)]) for (a, b) in keep if (a, b) in score]


def similar_edges(
    pairs: list[tuple[int, int, float]],
    details: dict[tuple[int, int], dict],
    *,
    knn_k: int = 8,
    floor: float = 0.0,
) -> list[dict]:
    """当前生产形态：单条 similar 边（双向），带 facet 分解 detail。"""
    kept = sparsify_knn(pairs, knn_k=knn_k, floor=floor)
    out = []
    for a, b, sim in kept:
        detail = details.get((min(a, b), max(a, b)))
        for s, t in ((a, b), (b, a)):
            e = {"source": f"paper:{s}", "target": f"paper:{t}", "kind": "similar", "weight": sim}
            if detail:
                e["detail"] = detail
            out.append(e)
    return out


def faceted_edges(
    details: dict[tuple[int, int], dict],
    *,
    knn_k: int = 8,
    min_sim: float = 0.3,
) -> list[dict]:
    """多关系形态：按每个 facet 维度拆成独立 typed 边（same-{facet}）。

    每个维度各自做 kNN 稀疏化，避免某一维度过密。min_sim 过滤弱关系。
    这是 docs/DEEP_RESEARCH_REDESIGN.md 阶段 2 的目标边形态——先在 test_g 里试。
    """
    # 收集每个 facet 维度的 pairs
    by_facet: dict[str, list[tuple[int, int, float]]] = {}
    meta: dict[str, dict] = {}
    for (a, b), d in details.items():
        for c in d.get("components", []):
            f = c["facet"]
            by_facet.setdefault(f, []).append((a, b, c["sim"]))
            meta[f] = {"label": c["label"]}
    out = []
    for facet, pairs in by_facet.items():
        kept = sparsify_knn(pairs, knn_k=knn_k, floor=min_sim)
        for a, b, sim in kept:
            comp = next((c for c in details[(min(a, b), max(a, b))]["components"] if c["facet"] == facet), None)
            for s, t in ((a, b), (b, a)):
                out.append({
                    "source": f"paper:{s}", "target": f"paper:{t}",
                    "kind": f"same-{facet}", "weight": sim,
                    "label": meta[facet]["label"],
                    "reason": comp.get("reason") if comp else "",
                    "shared": comp.get("shared", []) if comp else [],
                })
    return out


async def build_unified_graph(
    papers: list[PaperFeatures],
    *,
    embed_fn: EmbedFn,
    knn_k: int = 8,
    mutual: bool = True,
    min_sim: float = 0.05,
    facet_weights: dict[str, float] | None = None,
) -> dict:
    """统一建图（重设计目标的纯净实现，沙盒与生产共用同一套算法）。

    与旧形态的本质区别：**所有论文**（含未解读叶子）都拿到 topic 向量并参与 similar
    边——把 3 星引用树升级成真正的研究地图。流程：

    1. 每篇论文取最佳主题向量（vectors.build_topic_vectors：doc_vector 优先，否则
       embedding(topic_text)）。
    2. 全量两两余弦 → 互近邻去噪（knn.denoise_knn）得到 similar 边。
    3. 已解读论文对额外挂 facet 分解 detail（FacetEngine，解释"为什么相似"）。
    4. cites 边照常由引用结构生成。

    analytics（PageRank/社区）交给 graph_analytics.annotate_graph，本函数不重复。
    """
    nodes = build_paper_nodes(papers)
    edges: list[dict] = cites_edges(papers)
    cites_n = len(edges)

    vectors = await build_topic_vectors(papers, embed_fn=embed_fn)
    pairs = cosine_pairs(vectors)
    kept = denoise_knn(pairs, k=knn_k, mutual=mutual, min_sim=min_sim)

    # 已解读论文对的可解释 facet 分解（仅用于挂 detail，不改 similar 边的 headline 权重）
    engine = FacetEngine(weights=facet_weights)
    _, details = await engine.score_pairs(papers, embed_fn=embed_fn)

    similar_n = 0
    for a, b, sim in kept:
        detail = details.get((min(a, b), max(a, b)))
        for s, t in ((a, b), (b, a)):
            e = {"source": f"paper:{s}", "target": f"paper:{t}", "kind": "similar", "weight": sim}
            if detail:
                e["detail"] = detail
            edges.append(e)
            similar_n += 1

    stats = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "by_kind": {"cites": cites_n, "similar": similar_n, "shared-method": 0},
        "topic_vectors": len(vectors),
    }
    return {"nodes": nodes, "edges": edges, "stats": stats}
