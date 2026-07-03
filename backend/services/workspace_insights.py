"""工作区研究洞察服务。"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone

import networkx as nx
from sqlmodel import Session, select

from ..models.paper import Paper
from ..models.research import ResearchGraph
from ..models.schemas import (
    WorkspaceClusterSummary,
    WorkspaceInsightPaper,
    WorkspaceInsightResponse,
)
from ..models.workspace import Workspace, WorkspacePaper
from .database import get_engine

logger = logging.getLogger(__name__)

_READ_LEVEL_ORDER = {"none": 0, "digest": 1, "full": 2}
_TOP_LIMIT = 5


def _read_level(paper: Paper) -> str:
    if paper.status == "done":
        return "full"
    if getattr(paper, "graph_digest", None):
        return "digest"
    return "none"


def _priority(level: str) -> int:
    return _READ_LEVEL_ORDER.get(level, 0)


def _paper_id_from_node(node_id: str) -> int | None:
    if not isinstance(node_id, str) or not node_id.startswith("paper:"):
        return None
    try:
        return int(node_id.split(":", 1)[1])
    except (TypeError, ValueError):
        return None


def _cluster_id(meta: dict) -> int:
    value = meta.get("cluster", -1)
    if value is None:
        return -1
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _paper_label(paper: Paper) -> str:
    return (paper.title or paper.filename or f"Paper {paper.id}").strip()


def _paper_reason(
    *,
    read_level: str,
    importance: float,
    bridge_score: float,
    cluster_label: str | None,
    source: str,
    mode: str,
) -> str:
    parts: list[str] = []
    if mode == "bridge":
        if bridge_score > 0:
            parts.append("跨子方向连接")
        if cluster_label:
            parts.append(cluster_label)
    elif mode == "top":
        if importance > 0:
            parts.append("影响力靠前")
        if cluster_label:
            parts.append(cluster_label)
    else:
        if read_level == "none":
            parts.append("未解读")
        elif read_level == "digest":
            parts.append("已速读")
        if importance > 0.5:
            parts.append("高影响力")
        if bridge_score > 0.15:
            parts.append("桥梁论文")
        if cluster_label:
            parts.append(cluster_label)
    if source == "expanded":
        parts.append("联网扩展")
    if not parts:
        parts.append("建议优先查看")
    unique = list(dict.fromkeys(parts))
    return " · ".join(unique[:3])


def _node_to_paper(
    paper: Paper,
    *,
    importance: float = 0.0,
    bridge_score: float = 0.0,
    cluster: int = -1,
    cluster_label: str | None = None,
    mode: str = "next",
) -> WorkspaceInsightPaper:
    read_level = _read_level(paper)
    return WorkspaceInsightPaper(
        paper_id=paper.id,
        title=_paper_label(paper),
        venue=paper.venue,
        year=paper.year,
        source=paper.source or "uploaded",
        read_level=read_level,
        importance=round(float(importance), 4),
        bridge_score=round(float(bridge_score), 4),
        cluster=cluster,
        cluster_label=cluster_label,
        reason=_paper_reason(
            read_level=read_level,
            importance=importance,
            bridge_score=bridge_score,
            cluster_label=cluster_label,
            source=paper.source or "uploaded",
            mode=mode,
        ),
    )


def _semantic_graph(nodes: list[dict], edges: list[dict]) -> nx.Graph:
    graph = nx.Graph()
    paper_ids = {node["id"] for node in nodes if node.get("kind") == "paper"}
    for nid in paper_ids:
        graph.add_node(nid)

    for edge in edges:
        if edge.get("kind") == "cites":
            continue
        src = edge.get("source")
        tgt = edge.get("target")
        if src not in paper_ids or tgt not in paper_ids or src == tgt:
            continue
        weight = float(edge.get("weight", 1.0) or 1.0)
        distance = 1.0 / max(weight, 1e-6)
        if graph.has_edge(src, tgt):
            graph[src][tgt]["weight"] = max(graph[src][tgt]["weight"], weight)
            graph[src][tgt]["distance"] = min(graph[src][tgt]["distance"], distance)
        else:
            graph.add_edge(src, tgt, weight=weight, distance=distance)
    return graph


def _cluster_label(cluster_nodes: list[dict], cluster_id: int) -> str:
    labels = [n.get("meta", {}).get("cluster_label") for n in cluster_nodes if n.get("meta", {}).get("cluster_label")]
    if labels:
        return str(labels[0])
    if cluster_id < 0:
        return "孤立点"
    return f"子方向 {cluster_id + 1}"


def build_workspace_insights(workspace_id: int) -> WorkspaceInsightResponse:
    engine = get_engine()
    with Session(engine) as session:
        ws = session.get(Workspace, workspace_id)
        if not ws:
            raise ValueError("workspace not found")

        rows = session.exec(
            select(Paper, WorkspacePaper.added_at)
            .join(WorkspacePaper, WorkspacePaper.paper_id == Paper.id)
            .where(WorkspacePaper.workspace_id == workspace_id)
            .order_by(WorkspacePaper.added_at.desc())
        ).all()
        graph = session.exec(
            select(ResearchGraph).where(ResearchGraph.workspace_id == workspace_id)
        ).first()

    papers = [paper for paper, _ in rows]
    paper_count = len(papers)
    read_counts = {"full": 0, "digest": 0, "none": 0}
    source_counts: dict[str, int] = defaultdict(int)
    paper_map = {p.id: p for p in papers}

    for paper in papers:
        read_counts[_read_level(paper)] += 1
        source_counts[paper.source or "uploaded"] += 1

    graph_available = bool(graph and graph.nodes)
    node_map = {node["id"]: node for node in (graph.nodes if graph else []) if node.get("kind") == "paper"}
    importance_map = {nid: float(node.get("meta", {}).get("importance", 0.0) or 0.0) for nid, node in node_map.items()}
    cluster_map = {nid: _cluster_id(node.get("meta", {})) for nid, node in node_map.items()}
    cluster_label_map = {
        nid: node.get("meta", {}).get("cluster_label")
        for nid, node in node_map.items()
        if node.get("meta", {}).get("cluster_label")
    }

    bridge_map: dict[str, float] = {}
    if graph_available:
        semantic_graph = _semantic_graph(graph.nodes, graph.edges)
        if semantic_graph.number_of_edges() > 0:
            try:
                bridge_map = nx.betweenness_centrality(semantic_graph, weight="distance", normalized=True)
            except Exception as exc:  # pragma: no cover - 数值兜底
                logger.warning(f"workspace insights betweenness failed: {exc}")
                bridge_map = {}

    paper_records: list[WorkspaceInsightPaper] = []
    for paper in papers:
        nid = f"paper:{paper.id}"
        paper_records.append(
            _node_to_paper(
                paper,
                importance=importance_map.get(nid, 0.0),
                bridge_score=bridge_map.get(nid, 0.0),
                cluster=cluster_map.get(nid, -1),
                cluster_label=cluster_label_map.get(nid),
                mode="next",
            )
        )

    if graph_available:
        top_papers = sorted(
            paper_records,
            key=lambda p: (p.importance, p.bridge_score, 1 if p.read_level == "full" else 0),
            reverse=True,
        )[:_TOP_LIMIT]
        bridge_papers = sorted(
            [p for p in paper_records if p.bridge_score > 0],
            key=lambda p: (p.bridge_score, p.importance),
            reverse=True,
        )[:_TOP_LIMIT]
    else:
        top_papers = sorted(
            paper_records,
            key=lambda p: (_priority(p.read_level), p.year or 0, p.paper_id),
            reverse=True,
        )[:_TOP_LIMIT]
        bridge_papers = []

    next_reads = sorted(
        [p for p in paper_records if p.read_level != "full"],
        key=lambda p: (
            _priority(p.read_level),
            -p.importance,
            -p.bridge_score,
            p.paper_id,
        ),
    )[:_TOP_LIMIT]
    if not next_reads:
        next_reads = sorted(
            paper_records,
            key=lambda p: (-p.importance, -p.bridge_score, p.paper_id),
        )[:_TOP_LIMIT]

    cluster_summaries: list[WorkspaceClusterSummary] = []
    modularity = None
    cluster_count = 0
    if graph_available:
        clusters: dict[int, list[dict]] = defaultdict(list)
        for node in graph.nodes:
            if node.get("kind") != "paper":
                continue
            cluster = _cluster_id(node.get("meta", {}))
            clusters[cluster].append(node)

        for cluster_id, members in sorted(
            clusters.items(),
            key=lambda item: (
                item[0] == -1,
                -len(item[1]),
                item[0],
            ),
        ):
            cluster_papers = []
            for node in members:
                pid = _paper_id_from_node(node["id"])
                if pid is None or pid not in paper_map:
                    continue
                paper = paper_map[pid]
                cluster_papers.append(
                    _node_to_paper(
                        paper,
                        importance=float(node.get("meta", {}).get("importance", 0.0) or 0.0),
                        bridge_score=bridge_map.get(node["id"], 0.0),
                        cluster=cluster_id,
                        cluster_label=node.get("meta", {}).get("cluster_label"),
                        mode="top",
                    )
                )
            if not cluster_papers:
                continue
            unread_count = sum(1 for p in cluster_papers if p.read_level == "none")
            digest_count = sum(1 for p in cluster_papers if p.read_level == "digest")
            full_count = sum(1 for p in cluster_papers if p.read_level == "full")
            top_member = max(cluster_papers, key=lambda p: (p.importance, p.bridge_score, -p.paper_id))
            avg_importance = round(sum(p.importance for p in cluster_papers) / len(cluster_papers), 4)
            cluster_summaries.append(
                WorkspaceClusterSummary(
                    cluster=cluster_id,
                    label=_cluster_label(members, cluster_id),
                    paper_count=len(cluster_papers),
                    unread_count=unread_count,
                    digest_count=digest_count,
                    full_count=full_count,
                    top_paper_id=top_member.paper_id,
                    top_paper_title=top_member.title,
                    average_importance=avg_importance,
                )
            )

        cluster_count = sum(1 for c in cluster_summaries if c.cluster >= 0)
        if graph.stats:
            modularity = graph.stats.get("modularity")
            if isinstance(modularity, (int, float)):
                modularity = round(float(modularity), 4)
            else:
                modularity = None

    updated_at = graph.updated_at if graph else ws.updated_at
    return WorkspaceInsightResponse(
        workspace_id=workspace_id,
        graph_available=graph_available,
        paper_count=paper_count,
        read_counts=read_counts,
        source_counts=dict(source_counts),
        modularity=modularity,
        cluster_count=cluster_count,
        clusters=cluster_summaries,
        top_papers=top_papers,
        bridge_papers=bridge_papers,
        next_reads=next_reads,
        updated_at=updated_at,
    )
