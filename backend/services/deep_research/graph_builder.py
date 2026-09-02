"""
Graph Builder — 知识图谱构建

节点类型：paper
边类型：
- cites:         由 PaperReference 表构建的真实引用关系（citing → cited，有向）
- related:       论文关联度边（无向，双向存储）。权重来自唯一的关联度算法
                 （graphkit.relatedness），已融合主题向量 / 研究目的 / 方法 / 术语 /
                 共享数据集 / 共享 baseline / 文献耦合 / 共引八个通道，
                 edge.detail 里带完整分解。
- bibcoupling / cocitation: 仅作为**可视化图层**保留（前端可单独筛选查看结构关系）。
                 它们的分值已经计入 related 边，社区检测与中心性只看 related，
                 不再出现"同一对论文多种边按 max 折叠"的尺度打架。

注意：parent_paper_id 仅表示发现/下载谱系，不用于 cites 边。
"""

import logging

from sqlmodel import Session

from .graph_analytics import annotate_graph
from .graphkit import extract_backbone_with_bridges
from .paper_relatedness import compute_paper_relations, structural_channels
from ..ai_config import get_relations_config
from ..paper_identity import clean_display_title

logger = logging.getLogger(__name__)

# 关联度地板：低于此值的对当噪声丢弃，不参与骨架的强度统计
# （避免一堆近乎无关的弱边拉高节点强度分母）。骨架提取是主控，这是兜底。
MIN_RELATEDNESS = 0.05

# 目标平均度：每篇论文平均连几条关系边。这是地图密度的主控参数（与打分尺度无关），
# 实测 ws1 上 8 是"可读且不丢结构"的平衡点。可被运行参数覆盖。
DEFAULT_MAP_DENSITY = 8.0

# Disparity Filter 显著性水平 α：控制"局部显著"的判据（密度由 map_density 控制）。
DEFAULT_BACKBONE_ALPHA = 0.25


def _build_structural_edges(
    pairs: list[tuple[int, int, float, int]],
    *,
    kind: str,
) -> list[dict]:
    """从无向结构对构建双向边（bibcoupling / cocitation 可视化图层）。"""
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
    # 展示标题清洗：PDF 页眉噪声（"Published as a conference paper at ..."）不进节点标签
    label = clean_display_title(p.title) or (p.title or p.filename)
    return {
        "id": f"paper:{p.id}",
        "kind": "paper",
        "label": label[:80],
        "meta": {
            "paper_id": p.id,
            "year": p.year,
            "venue": p.venue,
            "source": p.source,
            "status": p.status,
            "external_id": p.external_id,
            "read_level": "full" if p.status == "done" else ("digest" if digest else "none"),
            "summary": digest.get("summary") if digest else None,
            "citation_count": getattr(p, "citation_count", None),
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
    enable_shared_method: bool = False,
    map_density: float = DEFAULT_MAP_DENSITY,
    backbone_alpha: float = DEFAULT_BACKBONE_ALPHA,
    enable_bibcoupling: bool = True,
    enable_cocitation: bool = True,
    citation_contexts: dict[int, list[dict]] | None = None,
    session: Session | None = None,
    references: list | None = None,
) -> dict:
    # 防御性去重：同一 paper_id 只建一个节点（旧数据/上游 join 异常曾产生重复节点，
    # 重复条目会污染前端渲染并使离线分析的度数虚高）。
    seen_ids: set[int] = set()
    uniq_papers = []
    for p in papers:
        if p.id in seen_ids:
            continue
        seen_ids.add(p.id)
        uniq_papers.append(p)
    if len(uniq_papers) != len(papers):
        logger.warning(
            "graph_builder: dropped %d duplicate paper rows", len(papers) - len(uniq_papers)
        )
    papers = uniq_papers

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

    # 2) related 边：唯一的关联度算法。结构信号（文献耦合/共引）作为通道进入分数，
    #    再由 Disparity Filter 决定留哪些边——结构证据因此参与"选边"，而不只是配色。
    relations = await compute_paper_relations(
        papers, ws_id=ws_id, references=references or [],
    )
    kept, bridge_keys = extract_backbone_with_bridges(
        relations.pairs,
        target_degree=map_density,
        alpha=backbone_alpha,
        min_sim=MIN_RELATEDNESS,
    )
    related_count = 0
    for a_id, b_id, score in kept:
        pair_key = (min(a_id, b_id), max(a_id, b_id))
        detail = relations.explain(a_id, b_id)
        is_bridge = pair_key in bridge_keys
        for s, t in ((a_id, b_id), (b_id, a_id)):
            e = {
                "source": f"paper:{s}", "target": f"paper:{t}",
                "kind": "related", "weight": score, "detail": detail,
            }
            if is_bridge:
                e["bridge"] = True
            edges.append(e)
            related_count += 1

    # 3) 结构可视化图层：分值已计入 related，此处只为前端能单独筛选查看
    bibcoupling_count = 0
    cocitation_count = 0
    if references and (enable_bibcoupling or enable_cocitation):
        from .graphkit import (
            build_cited_by_map,
            build_cites_map,
            compute_bibliographic_coupling_pairs,
            compute_co_citation_pairs,
        )
        if enable_bibcoupling:
            bc_pairs = compute_bibliographic_coupling_pairs(build_cites_map(references, paper_ids))
            bc_edges = _build_structural_edges(bc_pairs, kind="bibcoupling")
            edges.extend(bc_edges)
            bibcoupling_count = len(bc_edges)
        if enable_cocitation:
            cc_pairs = compute_co_citation_pairs(
                build_cited_by_map(references, paper_ids), paper_ids
            )
            cc_edges = _build_structural_edges(cc_pairs, kind="cocitation")
            edges.extend(cc_edges)
            cocitation_count = len(cc_edges)

    if enable_shared_method:
        logger.warning("shared-method 边生成暂未实现（method_extractor 模块待开发），跳过")

    # 4) 图谱分析：社区 / 中心性只看 related 边（见 graph_analytics）
    analytics = annotate_graph(nodes, edges)

    logger.info(
        "graph: %d 节点 / %d 边（cites=%d, related=%d[桥 %d], bib=%d, cocite=%d），"
        "平均度=%.1f, %d 子方向, modularity=%.3f, 通道权重=%s",
        len(nodes), len(edges), cites_count, related_count, len(bridge_keys),
        bibcoupling_count, cocitation_count,
        related_count / max(1, len(nodes)),
        analytics["clusters"], analytics.get("modularity", 0.0),
        {k: round(v, 3) for k, v in relations.weights.items()},
    )

    stats = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "by_kind": {
            "cites": cites_count,
            "bibcoupling": bibcoupling_count,
            "cocitation": cocitation_count,
            "related": related_count,
            "shared-method": 0,
        },
        "clusters": analytics["clusters"],
        "top_papers": analytics["top_papers"],
        "modularity": analytics.get("modularity", 0.0),
        "backbone": {
            "map_density": map_density,
            "alpha": backbone_alpha,
            "mean_degree": round(related_count / max(1, len(nodes)), 2),
            "bridge_pairs": len(bridge_keys),
            "candidate_pairs": len(relations.pairs),
        },
        "relation_channels": {
            "weights": {k: round(v, 4) for k, v in relations.weights.items()},
            "coverage": relations.coverage,
            "calibration": relations.calibration.to_dict() if relations.calibration else None,
        },
    }
    return {"nodes": nodes, "edges": edges, "stats": stats}
