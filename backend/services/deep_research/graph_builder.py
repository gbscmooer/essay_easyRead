"""
Graph Builder — 知识图谱构建

节点类型：paper
边类型：
- cites:         由 PaperReference 表构建的真实引用关系（citing → cited）
- bibcoupling:   文献耦合（共享参考文献，无向）
- cocitation:    共引（被相同论文共同引用，无向）
- similar:       论文级相似度（双向，由 SimilarityProvider 计算）
- shared-method: 共享方法（占位，未实现）

注意：parent_paper_id 仅表示发现/下载谱系，不再用于 cites 边。
"""

import logging

from sqlmodel import Session

from .similarity import get_similarity_provider
from .graph_analytics import annotate_graph
from .graphkit import extract_backbone
from ..ai_config import get_similarity_config

logger = logging.getLogger(__name__)

# 相似边的最小分数地板：低于此值的对直接当噪声丢弃，不参与骨架的强度统计
# （避免一堆近乎正交的弱边拉高节点强度分母）。骨架提取是主控，这是兜底。
MIN_SIMILARITY = 0.05

# Disparity Filter 默认显著性水平 α：越小骨架越稀越严格。可被运行参数覆盖。
DEFAULT_BACKBONE_ALPHA = 0.25


def _backbone_with_bridges(
    pairs: list[tuple[int, int, float]], alpha: float, floor: float,
) -> tuple[list[tuple[int, int, float]], set[tuple[int, int]]]:
    """返回骨架边及连通性兜底桥接边键集合 (min_id, max_id)。"""
    sparse = extract_backbone(pairs, alpha=alpha, min_sim=floor, ensure_connected=False)
    full = extract_backbone(pairs, alpha=alpha, min_sim=floor, ensure_connected=True)
    sparse_keys = {(min(a, b), max(a, b)) for a, b, _ in sparse}
    bridge_keys = {(min(a, b), max(a, b)) for a, b, _ in full} - sparse_keys
    return full, bridge_keys


def _build_structural_edges(
    pairs: list[tuple[int, int, float, int]],
    *,
    kind: str,
) -> list[dict]:
    """从无向结构对构建双向边（bibcoupling / cocitation）。"""
    edges: list[dict] = []
    for a, b, weight, shared in pairs:
        detail = {"shared_count": shared, "salton": weight}
        edges.append({
            "source": f"paper:{a}",
            "target": f"paper:{b}",
            "kind": kind,
            "weight": weight,
            "detail": detail,
        })
        edges.append({
            "source": f"paper:{b}",
            "target": f"paper:{a}",
            "kind": kind,
            "weight": weight,
            "detail": detail,
        })
    return edges


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
            "read_level": "full" if p.status == "done" else ("digest" if digest else "none"),
            "summary": digest.get("summary") if digest else None,
        },
    }


def _contexts_for_edge(contexts: list[dict]) -> list[dict]:
    return [
        {
            "section": c.get("section", ""),
            "context": c.get("context", ""),
            "intent": c.get("intent", "background"),
        }
        for c in contexts
    ]


def _load_paper_references(session: Session, paper_ids: set[int]):
    from .reference_store import list_for_papers
    return list_for_papers(session, paper_ids)


def _build_cites_edges(papers, references, by_id: dict[int, object]) -> tuple[list[dict], int]:
    """从 PaperReference 构建 cites 边；两端节点均须在工作区 papers 中。"""
    edges: list[dict] = []
    cites_count = 0

    for ref in references:
        citing_id = ref.citing_paper_id
        cited_id = ref.cited_paper_id
        if citing_id not in by_id or not cited_id or cited_id not in by_id:
            continue

        edge: dict = {
            "source": f"paper:{citing_id}",
            "target": f"paper:{cited_id}",
            "kind": "cites",
            "weight": min(1.0 + max(ref.frequency - 1, 0) * 0.15, 2.0),
        }
        if ref.contexts:
            edge["contexts"] = _contexts_for_edge(ref.contexts)
            if ref.intents:
                edge["intents"] = list(ref.intents)
        edges.append(edge)
        cites_count += 1

    return edges, cites_count


async def build_graph(
    ws_id: int,
    papers,
    *,
    similarity_threshold: float,
    enable_shared_method: bool,
    backbone_alpha: float = DEFAULT_BACKBONE_ALPHA,
    enable_bibcoupling: bool = True,
    enable_cocitation: bool = True,
    citation_contexts: dict[int, list[dict]] | None = None,
    session: Session | None = None,
    references: list | None = None,
) -> dict:
    nodes = [_paper_node(p) for p in papers]
    by_id = {p.id: p for p in papers}
    edges: list[dict] = []

    # 1) cites 边：PaperReference 真实引用（非 parent_paper_id）
    paper_ids = set(by_id.keys())
    if references is None and session is not None:
        references = _load_paper_references(session, paper_ids)
    elif references is None:
        try:
            from ..database import get_engine
            from sqlmodel import Session as SqlSession
            with SqlSession(get_engine()) as s:
                references = _load_paper_references(s, paper_ids)
        except Exception as e:
            logger.warning("graph_builder: failed to load paper references: %s", e)
            references = []

    cite_edges, cites_count = _build_cites_edges(papers, references or [], by_id)
    edges.extend(cite_edges)

    # 1.5) bibcoupling / cocitation：由 PaperReference 计算结构相似
    bibcoupling_count = 0
    cocitation_count = 0
    if references:
        from .similarity.bibcoupling import build_cites_map, compute_bibliographic_coupling_pairs
        from .similarity.cocitation import build_cited_by_map, compute_co_citation_pairs

        cites_map = build_cites_map(references, paper_ids)
        cited_by_map = build_cited_by_map(references, paper_ids)

        if enable_bibcoupling:
            bc_pairs = compute_bibliographic_coupling_pairs(cites_map)
            bc_edges = _build_structural_edges(bc_pairs, kind="bibcoupling")
            edges.extend(bc_edges)
            bibcoupling_count = len(bc_edges)

        if enable_cocitation:
            cc_pairs = compute_co_citation_pairs(cited_by_map, paper_ids)
            cc_edges = _build_structural_edges(cc_pairs, kind="cocitation")
            edges.extend(cc_edges)
            cocitation_count = len(cc_edges)

    # 2) similar 边：委托给配置的 SimilarityProvider，再用 kNN 稀疏化
    provider = get_similarity_provider(get_similarity_config())
    logger.info(f"graph similar edges via provider={provider.name}, backbone_alpha={backbone_alpha}")
    pairs = await provider.score_pairs(papers, ws_id=ws_id)
    details: dict = getattr(provider, "details", {}) or {}
    kept, bridge_keys = _backbone_with_bridges(
        pairs, alpha=backbone_alpha, floor=max(MIN_SIMILARITY, 0.0),
    )
    similar_count = 0
    for a_id, b_id, sim in kept:
        pair_key = (min(a_id, b_id), max(a_id, b_id))
        detail = details.get(pair_key)
        is_bridge = pair_key in bridge_keys
        e_ab = {"source": f"paper:{a_id}", "target": f"paper:{b_id}", "kind": "similar", "weight": sim}
        e_ba = {"source": f"paper:{b_id}", "target": f"paper:{a_id}", "kind": "similar", "weight": sim}
        if is_bridge:
            e_ab["bridge"] = True
            e_ba["bridge"] = True
        if detail:
            e_ab["detail"] = detail
            e_ba["detail"] = detail
        edges.append(e_ab)
        edges.append(e_ba)
        similar_count += 2

    # 3) shared-method 边：占位
    if enable_shared_method:
        logger.warning("shared-method 边生成暂未实现（method_extractor 模块待开发），跳过")

    # 3.5) 图谱分析
    analytics = annotate_graph(nodes, edges)

    stats = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "by_kind": {
            "cites": cites_count,
            "bibcoupling": bibcoupling_count,
            "cocitation": cocitation_count,
            "similar": similar_count,
            "shared-method": 0,
        },
        "clusters": analytics["clusters"],
        "top_papers": analytics["top_papers"],
        "modularity": analytics.get("modularity", 0.0),
    }
    return {"nodes": nodes, "edges": edges, "stats": stats}
