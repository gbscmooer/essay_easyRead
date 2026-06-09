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
