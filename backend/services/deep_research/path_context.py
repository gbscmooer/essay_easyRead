"""Path-style graph context for workspace RAG.

The implementation keeps the current Chroma/vector retrieval path intact and
adds a lightweight PathRAG-style layer on top of the persisted ResearchGraph.
Graph traversal uses NetworkX instead of a hand-rolled path search.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any

import networkx as nx


@dataclass(slots=True)
class PathContext:
    context: str
    evidence: list[dict[str, Any]]

    @property
    def prompt(self) -> str:
        """Backward-compatible alias for callers that treat this as prompt text."""

        return self.context


def parse_paper_node_id(value: Any) -> int | None:
    """Return the DB paper id from graph node ids like ``paper:123`` or ``123``."""

    if value is None:
        return None
    text = str(value).strip()
    if text.startswith("paper:"):
        text = text.split(":", 1)[1]
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _node_id_for_paper(paper_id: int) -> str:
    return f"paper:{paper_id}"


def _title(node: dict[str, Any] | None, fallback: str = "") -> str:
    if not node:
        return fallback or "Unknown"
    return str(node.get("label") or fallback or "Unknown")


def _edge_cost(weight: Any, kind: str) -> float:
    try:
        w = float(weight)
    except (TypeError, ValueError):
        w = 1.0
    w = max(w, 0.001)
    # Citation links are harder evidence than semantic similarity, so they get
    # a small cost discount while still preserving the provider weight signal.
    prior = 1.25 if kind == "cites" else 1.0
    return 1.0 / (w * prior)


def _build_graph(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> nx.Graph:
    graph = nx.Graph()
    valid_node_ids = {str(n.get("id")) for n in nodes if n.get("id")}
    for node_id in valid_node_ids:
        graph.add_node(node_id)

    for edge in edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if not source or not target or source == target:
            continue
        if valid_node_ids and (source not in valid_node_ids or target not in valid_node_ids):
            continue

        kind = str(edge.get("kind") or "related")
        weight = edge.get("weight", 1.0)
        cost = _edge_cost(weight, kind)
        if graph.has_edge(source, target):
            current = graph[source][target]
            if cost < current.get("cost", float("inf")):
                current.update({"kind": kind, "weight": weight, "cost": cost, "raw": edge})
            else:
                kinds = set(current.get("kinds", [current.get("kind", "related")]))
                kinds.add(kind)
                current["kinds"] = sorted(kinds)
        else:
            graph.add_edge(
                source,
                target,
                kind=kind,
                kinds=[kind],
                weight=weight,
                cost=cost,
                raw=edge,
            )
    return graph


def _edge_label(edge: dict[str, Any], source: str, target: str) -> str:
    kind = edge.get("kind", "related")
    raw = edge.get("raw") or {}
    raw_source = str(raw.get("source") or "")
    raw_target = str(raw.get("target") or "")

    if kind == "cites":
        if raw_source == source and raw_target == target:
            return "引用/扩展"
        if raw_source == target and raw_target == source:
            return "被引用/反向扩展"
        return "引用相关"
    if kind in ("related", "similar"):   # similar = v2 及以前持久化图谱的旧名
        return "关联"
    if kind == "shared-method":
        return "共享方法"
    return str(kind)


def _format_weight(weight: Any) -> str:
    try:
        return f"{float(weight):.2f}"
    except (TypeError, ValueError):
        return "1.00"


def _format_path(
    path: list[str],
    graph: nx.Graph,
    node_by_id: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    parts: list[str] = []
    evidence_edges: list[dict[str, Any]] = []

    for idx, node_id in enumerate(path):
        parts.append(f"《{_title(node_by_id.get(node_id), node_id)}》")
        if idx == len(path) - 1:
            continue
        next_id = path[idx + 1]
        edge = dict(graph[node_id][next_id])
        label = _edge_label(edge, node_id, next_id)
        weight = _format_weight(edge.get("weight", 1.0))
        parts.append(f" --{label}({weight})-- ")
        evidence_edges.append(
            {
                "source": node_id,
                "target": next_id,
                "kind": edge.get("kind", "related"),
                "weight": edge.get("weight", 1.0),
            }
        )

    score = 0.0
    for a, b in zip(path, path[1:]):
        try:
            score += 1.0 / max(float(graph[a][b].get("cost", 1.0)), 0.001)
        except (TypeError, ValueError):
            score += 1.0

    evidence = {
        "type": "path",
        "path": path,
        "node_ids": path,
        "paper_ids": [parse_paper_node_id(n) for n in path],
        "edges": evidence_edges,
        "text": "".join(parts),
        "score": round(score, 4),
    }
    return evidence["text"], evidence


def _cluster_context(
    hits: list[dict[str, Any]],
    node_by_paper: dict[int, dict[str, Any]],
    nodes: list[dict[str, Any]],
    *,
    max_items: int,
) -> list[str]:
    community_top: dict[int, dict[str, Any]] = {}
    for node in nodes:
        meta = node.get("meta") or {}
        cluster = meta.get("cluster", -1)
        if not isinstance(cluster, int) or cluster < 0:
            continue
        importance = float(meta.get("importance", 0.0) or 0.0)
        current = community_top.get(cluster)
        if current is None or importance > float((current.get("meta") or {}).get("importance", 0.0) or 0.0):
            community_top[cluster] = node

    lines: list[str] = []
    seen: set[int] = set()
    for hit in hits:
        paper_id = parse_paper_node_id(hit.get("paper_id"))
        if paper_id is None or paper_id in seen:
            continue
        seen.add(paper_id)
        node = node_by_paper.get(paper_id)
        if not node:
            continue
        meta = node.get("meta") or {}
        cluster = meta.get("cluster", -1)
        label = meta.get("cluster_label") or "未分类"
        importance = float(meta.get("importance", 0.0) or 0.0)
        title = _title(node, str(hit.get("paper_title") or "Unknown"))

        suffix = ""
        if isinstance(cluster, int) and cluster >= 0:
            top = community_top.get(cluster)
            if top and top.get("id") != node.get("id"):
                top_meta = top.get("meta") or {}
                suffix = (
                    f"，代表性文献《{_title(top)}》"
                    f"（影响力 {float(top_meta.get('importance', 0.0) or 0.0):.2f}）"
                )
        lines.append(f"- 《{title}》处于研究子方向：{label}（重要性 {importance:.2f}{suffix}）")
        if len(lines) >= max_items:
            break
    return lines


def _graph_parts(graph_or_nodes: Any, edges: Any = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if graph_or_nodes is None:
        return [], []
    if isinstance(graph_or_nodes, dict):
        return list(graph_or_nodes.get("nodes") or []), list(graph_or_nodes.get("edges") or [])
    if hasattr(graph_or_nodes, "nodes") and hasattr(graph_or_nodes, "edges"):
        return list(getattr(graph_or_nodes, "nodes") or []), list(getattr(graph_or_nodes, "edges") or [])
    if isinstance(graph_or_nodes, list):
        return graph_or_nodes, list(edges or [])
    return [], []


def build_path_context(
    graph_or_nodes: Any,
    hits_or_edges: list[dict[str, Any]] | None = None,
    hits: list[dict[str, Any]] | None = None,
    *,
    max_paths: int = 4,
    max_path_nodes: int = 4,
    max_cluster_items: int = 6,
) -> PathContext:
    """Build compact graph evidence for a RAG prompt.

    ``hits`` is the vector/rerank result list. The function only uses graph
    paths among those retrieved papers, so it cannot introduce unrelated papers
    unless they are a bridge on a shortest graph path.
    """

    if hits is None:
        graph_nodes, graph_edges = _graph_parts(graph_or_nodes)
        rag_hits = list(hits_or_edges or [])
    else:
        graph_nodes, graph_edges = _graph_parts(graph_or_nodes, hits_or_edges)
        rag_hits = list(hits or [])

    if not graph_nodes or not rag_hits:
        return PathContext(context="", evidence=[])

    node_by_id = {str(n.get("id")): n for n in graph_nodes if n.get("id")}
    node_by_paper = {
        pid: n
        for n in graph_nodes
        if (pid := parse_paper_node_id(n.get("id"))) is not None
    }

    hit_paper_ids: list[int] = []
    seen: set[int] = set()
    for hit in rag_hits:
        pid = parse_paper_node_id(hit.get("paper_id"))
        if pid is None or pid in seen:
            continue
        seen.add(pid)
        if pid in node_by_paper:
            hit_paper_ids.append(pid)

    cluster_lines = _cluster_context(rag_hits, node_by_paper, graph_nodes, max_items=max_cluster_items)
    graph = _build_graph(graph_nodes, graph_edges)

    path_items: list[dict[str, Any]] = []
    for a_pid, b_pid in combinations(hit_paper_ids[:6], 2):
        source = _node_id_for_paper(a_pid)
        target = _node_id_for_paper(b_pid)
        if source not in graph or target not in graph:
            continue
        try:
            paths_iter = nx.shortest_simple_paths(graph, source, target, weight="cost")
            path = next(paths_iter)
        except (nx.NetworkXNoPath, nx.NodeNotFound, StopIteration):
            continue
        if len(path) > max_path_nodes:
            continue
        text, evidence = _format_path(path, graph, node_by_id)
        evidence["source_paper_id"] = a_pid
        evidence["target_paper_id"] = b_pid
        evidence["text"] = text
        path_items.append(evidence)

    path_items.sort(key=lambda item: (len(item["node_ids"]), -float(item.get("score", 0.0))))
    path_items = path_items[:max_paths]

    sections: list[str] = []
    if cluster_lines:
        sections.append("【命中论文图谱位置】:\n" + "\n".join(cluster_lines))
    if path_items:
        path_lines = [f"- {item['text']}" for item in path_items]
        sections.append("【PathRAG 图谱路径证据】:\n" + "\n".join(path_lines))

    context = ("\n" + "\n\n".join(sections)) if sections else ""
    return PathContext(context=context, evidence=path_items)
