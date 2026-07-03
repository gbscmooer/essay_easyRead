"""Agent-facing paper tools.

This module is the decoupled core used by both HTTP routes and the MCP stdio
server.  It only reads existing paper analysis, RAG indexes, and research graph
snapshots; it does not mutate papers or change the graph-building algorithm.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from ..models.analysis import AnalysisResult
from ..models.paper import Paper
from ..models.research import ResearchGraph
from ..models.workspace import Workspace, WorkspacePaper
from .database import get_engine
from .paper_profile import build_paper_profile, load_paper_profiles


@dataclass(slots=True)
class AgentToolError(Exception):
    """Domain error that can be mapped to API/MCP error responses."""

    message: str
    code: str = "BAD_REQUEST"
    status_code: int = 400

    def __str__(self) -> str:
        return self.message


def _dt(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


def _safe_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _coerce_int(value: Any, name: str, *, default: Optional[int] = None) -> int:
    if value in (None, ""):
        if default is not None:
            return default
        raise AgentToolError(f"{name} 不能为空", "BAD_REQUEST", 400)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise AgentToolError(f"{name} 必须是整数", "BAD_REQUEST", 400) from exc


def _coerce_optional_int(value: Any, name: str) -> Optional[int]:
    if value in (None, ""):
        return None
    return _coerce_int(value, name)


def _coerce_int_list(value: Any, name: str) -> Optional[list[int]]:
    if value in (None, ""):
        return None
    if not isinstance(value, list):
        raise AgentToolError(f"{name} 必须是整数数组", "BAD_REQUEST", 400)
    return [_coerce_int(item, name) for item in value]


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def _paper_node_id(paper_id: int) -> str:
    return f"paper:{paper_id}"


def _node_matches_paper(node_id: Any, paper_id: int) -> bool:
    return str(node_id) in {_paper_node_id(paper_id), str(paper_id)}


def _profile_to_dict(profile) -> dict[str, Any]:
    return {
        "paper_id": profile.paper_id,
        "title": profile.title,
        "authors": profile.authors,
        "venue": profile.venue,
        "year": profile.year,
        "source": profile.source,
        "read_level": profile.read_level,
        "detail_level": profile.detail_level,
        "summary": profile.summary,
        "one_sentence": profile.one_sentence,
        "problem": profile.problem,
        "key_contributions": profile.key_contributions,
        "method": {
            "overview": profile.method_overview,
            "pipeline": profile.method_pipeline,
            "modules": profile.method_modules,
        },
        "experiments": {
            "datasets": profile.datasets,
            "baselines": profile.baselines,
        },
        "code_mapping": profile.code_mapping,
        "limitations": profile.limitations,
        "graph_digest": profile.graph_digest,
    }


def _paper_summary(paper: Paper, profile=None, score: float = 0.0, matched_fields: Optional[list[str]] = None) -> dict[str, Any]:
    title = _safe_text(getattr(profile, "title", None) or paper.title or paper.filename)
    return {
        "paper_id": paper.id,
        "filename": paper.filename,
        "title": title,
        "authors": _safe_text(getattr(profile, "authors", None) or paper.authors),
        "venue": _safe_text(getattr(profile, "venue", None) or paper.venue),
        "year": getattr(profile, "year", None) or paper.year,
        "category": paper.category,
        "status": paper.status,
        "index_status": paper.index_status,
        "source": paper.source,
        "level": paper.level,
        "code_url": paper.code_url,
        "read_level": getattr(profile, "read_level", "none") if profile else "none",
        "detail_level": getattr(profile, "detail_level", "metadata") if profile else "metadata",
        "score": round(score, 4),
        "matched_fields": matched_fields or [],
        "created_at": _dt(paper.created_at),
        "updated_at": _dt(paper.updated_at),
    }


def _field_score(query: str, field_name: str, text: str, weight: float) -> tuple[float, bool]:
    if not query or not text:
        return 0.0, False
    q = query.lower()
    t = text.lower()
    if q in t:
        return weight, True
    tokens = [tok for tok in q.split() if len(tok) > 1]
    if not tokens:
        return 0.0, False
    hits = sum(1 for tok in tokens if tok in t)
    if hits:
        return weight * hits / len(tokens), True
    return 0.0, False


def list_agent_tools() -> list[dict[str, Any]]:
    """Tool descriptors shared by HTTP manifest and MCP tools/list."""

    return [
        {
            "name": "paper_search",
            "description": "Search local papers by title, authors, venue, category, structured analysis, digest, and indexed text.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Research keyword, method name, dataset, or development goal fragment."},
                    "workspace_id": {"type": "integer", "description": "Optional workspace scope."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    "include_expanded": {"type": "boolean", "default": True},
                },
                "required": ["query"],
            },
        },
        {
            "name": "paper_get",
            "description": "Get a paper profile with structured analysis fields, method summary, experiments, limitations, and code mapping.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "integer", "description": "Paper database ID."}
                },
                "required": ["paper_id"],
            },
        },
        {
            "name": "paper_retrieve_evidence",
            "description": "Retrieve evidence snippets from a workspace vector index. Returns snippets and citations without asking the LLM to invent missing facts.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "integer"},
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                    "paper_ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["workspace_id", "query"],
            },
        },
        {
            "name": "graph_get",
            "description": "Get the latest persisted research graph snapshot for a workspace.",
            "input_schema": {
                "type": "object",
                "properties": {"workspace_id": {"type": "integer"}},
                "required": ["workspace_id"],
            },
        },
        {
            "name": "graph_neighbors",
            "description": "Get local graph neighborhood around one paper from the persisted graph snapshot.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "integer"},
                    "paper_id": {"type": "integer"},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 2, "default": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                    "edge_kinds": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["workspace_id", "paper_id"],
            },
        },
        {
            "name": "dev_brief",
            "description": "Create a paper-grounded development brief for a coding agent. It contains evidence snippets, candidate papers, graph context, and implementation guardrails.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "integer"},
                    "goal": {"type": "string", "description": "Feature or engineering task to develop from papers."},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 6},
                    "paper_ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["workspace_id", "goal"],
            },
        },
    ]


def list_workspaces() -> list[dict[str, Any]]:
    engine = get_engine()
    with Session(engine) as session:
        workspaces = session.exec(select(Workspace).order_by(Workspace.updated_at.desc())).all()
        rows: list[dict[str, Any]] = []
        for ws in workspaces:
            paper_ids = session.exec(
                select(WorkspacePaper.paper_id).where(WorkspacePaper.workspace_id == ws.id)
            ).all()
            rows.append(
                {
                    "id": ws.id,
                    "name": ws.name,
                    "paper_count": len(paper_ids),
                    "created_at": _dt(ws.created_at),
                    "updated_at": _dt(ws.updated_at),
                }
            )
        return rows


def list_workspace_papers(workspace_id: int) -> list[dict[str, Any]]:
    engine = get_engine()
    with Session(engine) as session:
        ws = session.get(Workspace, workspace_id)
        if not ws:
            raise AgentToolError("工作区不存在", "NOT_FOUND", 404)

        rows = session.exec(
            select(Paper)
            .join(WorkspacePaper, WorkspacePaper.paper_id == Paper.id)
            .where(WorkspacePaper.workspace_id == workspace_id)
            .order_by(WorkspacePaper.added_at.desc())
        ).all()
        profiles = load_paper_profiles(session, [paper.id for paper in rows])
        return [_paper_summary(paper, profiles.get(paper.id)) for paper in rows]


def search_papers(
    query: str,
    workspace_id: Optional[int] = None,
    limit: int = 10,
    include_expanded: bool = True,
) -> dict[str, Any]:
    query = _safe_text(query)
    if not query:
        raise AgentToolError("query 不能为空", "BAD_REQUEST", 400)

    workspace_id = _coerce_optional_int(workspace_id, "workspace_id")
    limit = min(max(_coerce_int(limit, "limit", default=10), 1), 50)
    include_expanded = _coerce_bool(include_expanded, default=True)
    engine = get_engine()
    with Session(engine) as session:
        if workspace_id is not None:
            ws = session.get(Workspace, workspace_id)
            if not ws:
                raise AgentToolError("工作区不存在", "NOT_FOUND", 404)
            stmt = (
                select(Paper)
                .join(WorkspacePaper, WorkspacePaper.paper_id == Paper.id)
                .where(WorkspacePaper.workspace_id == workspace_id)
            )
        else:
            stmt = select(Paper)
        if not include_expanded:
            stmt = stmt.where(Paper.source != "expanded")

        papers = session.exec(stmt).all()
        profiles = load_paper_profiles(session, [paper.id for paper in papers])

        scored: list[tuple[float, list[str], Paper]] = []
        for paper in papers:
            profile = profiles.get(paper.id)
            fields = {
                "title": _safe_text(getattr(profile, "title", None) or paper.title or paper.filename),
                "authors": _safe_text(getattr(profile, "authors", None) or paper.authors),
                "venue": _safe_text(getattr(profile, "venue", None) or paper.venue),
                "category": _safe_text(paper.category),
                "summary": _safe_text(getattr(profile, "summary", "")),
                "problem": _safe_text(getattr(profile, "problem", "")),
                "contributions": " ".join(getattr(profile, "key_contributions", []) or []),
                "method": _safe_text(getattr(profile, "method_text", "")),
                "experiments": _safe_text(getattr(profile, "experiment_text", "")),
                "implementation": _safe_text(getattr(profile, "implementation_text", "")),
                "full_text": _safe_text((paper.full_text or "")[:20000]),
            }
            weights = {
                "title": 8.0,
                "authors": 2.0,
                "venue": 1.5,
                "category": 1.5,
                "summary": 4.0,
                "problem": 4.0,
                "contributions": 5.0,
                "method": 5.0,
                "experiments": 3.0,
                "implementation": 4.0,
                "full_text": 1.0,
            }
            score = 0.0
            matched: list[str] = []
            for name, text in fields.items():
                delta, hit = _field_score(query, name, text, weights[name])
                score += delta
                if hit:
                    matched.append(name)
            if score > 0:
                scored.append((score, matched, paper))

        scored.sort(key=lambda item: (item[0], item[2].updated_at), reverse=True)
        items = [
            _paper_summary(paper, profiles.get(paper.id), score=score, matched_fields=matched)
            for score, matched, paper in scored[:limit]
        ]
        return {
            "query": query,
            "workspace_id": workspace_id,
            "total": len(scored),
            "items": items,
            "evidence_policy": "Search results are local database matches. Use paper_get or paper_retrieve_evidence before making implementation claims.",
        }


def get_paper_context(paper_id: int) -> dict[str, Any]:
    engine = get_engine()
    with Session(engine) as session:
        paper = session.get(Paper, paper_id)
        if not paper:
            raise AgentToolError("论文不存在", "NOT_FOUND", 404)
        analysis = session.exec(select(AnalysisResult).where(AnalysisResult.paper_id == paper_id)).first()
        profile = build_paper_profile(paper, analysis)

        workspaces = session.exec(
            select(Workspace)
            .join(WorkspacePaper, WorkspacePaper.workspace_id == Workspace.id)
            .where(WorkspacePaper.paper_id == paper_id)
        ).all()

        return {
            "paper": _paper_summary(paper, profile),
            "profile": _profile_to_dict(profile),
            "analysis_available": analysis is not None,
            "analysis": {
                "paper_id": analysis.paper_id,
                "title": analysis.title,
                "authors": analysis.authors,
                "venue": analysis.venue,
                "year": analysis.year,
                "one_sentence": analysis.one_sentence,
                "problem": analysis.problem,
                "key_contributions": analysis.key_contributions,
                "outline": analysis.outline,
                "method": analysis.method,
                "experiments": analysis.experiments,
                "code_mapping": analysis.code_mapping,
                "interview_qa": analysis.interview_qa,
                "limitations": analysis.limitations,
                "related_papers": analysis.related_papers,
                "mermaid_chart": analysis.mermaid_chart,
            } if analysis else None,
            "workspaces": [{"id": ws.id, "name": ws.name} for ws in workspaces],
            "evidence_policy": "Fields are extracted from local paper metadata, graph digest, or saved AI analysis. Treat absent fields as unknown.",
        }


def get_workspace_graph(workspace_id: int) -> dict[str, Any]:
    engine = get_engine()
    with Session(engine) as session:
        ws = session.get(Workspace, workspace_id)
        if not ws:
            raise AgentToolError("工作区不存在", "NOT_FOUND", 404)
        graph = session.exec(select(ResearchGraph).where(ResearchGraph.workspace_id == workspace_id)).first()
        if not graph:
            raise AgentToolError("尚未构建图谱", "NOT_FOUND", 404)
        return {
            "workspace_id": graph.workspace_id,
            "run_id": graph.run_id,
            "nodes": graph.nodes or [],
            "edges": graph.edges or [],
            "stats": graph.stats or {},
            "updated_at": _dt(graph.updated_at),
            "algorithm_policy": "This is a persisted graph snapshot. Agent tools do not rebuild or alter graph construction.",
        }


def graph_neighbors(
    workspace_id: int,
    paper_id: int,
    depth: int = 1,
    limit: int = 50,
    edge_kinds: Optional[list[str]] = None,
) -> dict[str, Any]:
    graph = get_workspace_graph(workspace_id)
    depth = min(max(_coerce_int(depth, "depth", default=1), 1), 2)
    limit = min(max(_coerce_int(limit, "limit", default=50), 1), 200)
    kinds = {kind for kind in (edge_kinds or []) if kind}

    nodes = graph["nodes"]
    edges = graph["edges"]
    node_by_id = {str(node.get("id")): node for node in nodes if node.get("id") is not None}
    center_id = next(
        (candidate for candidate in (_paper_node_id(paper_id), str(paper_id)) if candidate in node_by_id),
        None,
    )
    if center_id is None:
        raise AgentToolError("论文不在当前图谱中", "NOT_FOUND", 404)

    adjacency: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for edge in edges:
        kind = _safe_text(edge.get("kind"))
        if kinds and kind not in kinds:
            continue
        source = str(edge.get("source"))
        target = str(edge.get("target"))
        if not source or not target:
            continue
        adjacency.setdefault(source, []).append((target, edge))
        adjacency.setdefault(target, []).append((source, edge))

    visited = {center_id}
    visit_order = [center_id]
    frontier = [center_id]
    selected_edges: list[dict[str, Any]] = []
    for _ in range(depth):
        next_frontier: list[str] = []
        for node_id in frontier:
            for neighbor_id, edge in adjacency.get(node_id, []):
                if neighbor_id not in node_by_id:
                    continue
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    visit_order.append(neighbor_id)
                    next_frontier.append(neighbor_id)
                selected_edges.append(edge)
                if len(visit_order) >= limit:
                    break
            if len(visit_order) >= limit:
                break
        frontier = next_frontier
        if not frontier or len(visit_order) >= limit:
            break

    visible_order = [node_id for node_id in visit_order[:limit] if node_id in node_by_id]
    visible = set(visible_order)
    dedup_edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for edge in selected_edges:
        source = str(edge.get("source"))
        target = str(edge.get("target"))
        kind = _safe_text(edge.get("kind"))
        if source in visible and target in visible:
            key = (source, target, kind)
            if key not in seen_edges:
                seen_edges.add(key)
                dedup_edges.append(edge)

    return {
        "workspace_id": workspace_id,
        "paper_id": paper_id,
        "depth": depth,
        "center": node_by_id[center_id],
        "nodes": [node_by_id[node_id] for node_id in visible_order],
        "edges": dedup_edges,
        "truncated": len(visited) > len(visible),
        "algorithm_policy": "Neighborhood is filtered from the persisted graph snapshot without recalculating graph edges.",
    }


async def retrieve_evidence(
    workspace_id: int,
    query: str,
    top_k: int = 5,
    paper_ids: Optional[list[int]] = None,
) -> dict[str, Any]:
    query = _safe_text(query)
    if not query:
        raise AgentToolError("query 不能为空", "BAD_REQUEST", 400)
    top_k = min(max(_coerce_int(top_k, "top_k", default=5), 1), 20)
    paper_ids = _coerce_int_list(paper_ids, "paper_ids")

    engine = get_engine()
    with Session(engine) as session:
        ws = session.get(Workspace, workspace_id)
        if not ws:
            raise AgentToolError("工作区不存在", "NOT_FOUND", 404)

    from .deep_research.embedder import embed_query
    from .deep_research.vector_store import query_workspace

    vec = await embed_query(query)
    hits = query_workspace(workspace_id, vec, top_k=top_k, paper_ids=paper_ids)
    if not hits:
        raise AgentToolError("未构建索引或无命中", "CHROMA_NOT_INITIALIZED", 404)

    evidence = []
    for idx, hit in enumerate(hits, start=1):
        evidence.append(
            {
                "rank": idx,
                "paper_id": hit.get("paper_id"),
                "paper_title": hit.get("paper_title") or hit.get("title") or "",
                "section_name": hit.get("section_name") or hit.get("section") or "",
                "snippet": hit.get("snippet") or "",
                "score": float(hit.get("score") or 0.0),
                "chunk_id": hit.get("chunk_id") or hit.get("id"),
                "source": "workspace_vector_index",
            }
        )

    return {
        "workspace_id": workspace_id,
        "query": query,
        "top_k": top_k,
        "paper_ids": paper_ids,
        "evidence": evidence,
        "evidence_policy": "Every implementation claim should cite evidence[].rank or call paper_get for structured context. If evidence is insufficient, say so explicitly.",
    }


async def create_development_brief(
    workspace_id: int,
    goal: str,
    top_k: int = 6,
    paper_ids: Optional[list[int]] = None,
) -> dict[str, Any]:
    goal = _safe_text(goal)
    if not goal:
        raise AgentToolError("goal 不能为空", "BAD_REQUEST", 400)
    evidence_pack = await retrieve_evidence(workspace_id, goal, top_k=top_k, paper_ids=paper_ids)
    evidence = evidence_pack["evidence"]
    candidate_ids: list[int] = []
    for item in evidence:
        pid = item.get("paper_id")
        if isinstance(pid, int) and pid not in candidate_ids:
            candidate_ids.append(pid)

    engine = get_engine()
    with Session(engine) as session:
        profiles = load_paper_profiles(session, candidate_ids)
        candidate_papers = [_profile_to_dict(profiles[pid]) for pid in candidate_ids if pid in profiles]

    graph_context = []
    for pid in candidate_ids[:5]:
        try:
            neighborhood = graph_neighbors(workspace_id, pid, depth=1, limit=12)
            graph_context.append(
                {
                    "paper_id": pid,
                    "neighbor_count": max(len(neighborhood["nodes"]) - 1, 0),
                    "edge_count": len(neighborhood["edges"]),
                    "neighbors": [
                        {"id": node.get("id"), "label": node.get("label"), "meta": node.get("meta", {})}
                        for node in neighborhood["nodes"]
                        if not _node_matches_paper(node.get("id"), pid)
                    ][:8],
                }
            )
        except AgentToolError:
            continue

    return {
        "workspace_id": workspace_id,
        "goal": goal,
        "candidate_papers": candidate_papers,
        "evidence": evidence,
        "graph_context": graph_context,
        "implementation_guardrails": [
            "Start from the cited evidence snippets and paper_get structured fields before editing code.",
            "Keep unsupported assumptions separate from paper-supported requirements.",
            "Do not claim a method detail is from a paper unless it appears in evidence or structured paper context.",
            "Use the graph context for related-work expansion, not as a substitute for textual evidence.",
        ],
        "suggested_agent_flow": [
            "paper_search",
            "paper_retrieve_evidence",
            "paper_get",
            "graph_neighbors",
            "code edit and tests",
            "final report with evidence ranks",
        ],
    }


async def call_agent_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Dispatch by MCP/HTTP tool name, including backward-compatible aliases."""

    arguments = arguments or {}
    if name == "paper_search":
        return search_papers(
            query=arguments.get("query", ""),
            workspace_id=_coerce_optional_int(arguments.get("workspace_id"), "workspace_id"),
            limit=arguments.get("limit", 10),
            include_expanded=arguments.get("include_expanded", True),
        )
    if name == "paper_get":
        return get_paper_context(_coerce_int(arguments.get("paper_id"), "paper_id"))
    if name == "paper_retrieve_evidence":
        return await retrieve_evidence(
            workspace_id=_coerce_int(arguments.get("workspace_id"), "workspace_id"),
            query=arguments.get("query", ""),
            top_k=arguments.get("top_k", 5),
            paper_ids=arguments.get("paper_ids"),
        )
    if name == "graph_get":
        return get_workspace_graph(_coerce_int(arguments.get("workspace_id"), "workspace_id"))
    if name == "graph_neighbors":
        return graph_neighbors(
            workspace_id=_coerce_int(arguments.get("workspace_id"), "workspace_id"),
            paper_id=_coerce_int(arguments.get("paper_id"), "paper_id"),
            depth=arguments.get("depth", 1),
            limit=arguments.get("limit", 50),
            edge_kinds=arguments.get("edge_kinds"),
        )
    if name == "dev_brief":
        return await create_development_brief(
            workspace_id=_coerce_int(arguments.get("workspace_id"), "workspace_id"),
            goal=arguments.get("goal", ""),
            top_k=arguments.get("top_k", 6),
            paper_ids=arguments.get("paper_ids"),
        )

    # Compatibility with the original MCP names.
    if name == "list_workspaces":
        return list_workspaces()
    if name == "list_papers_in_workspace":
        return list_workspace_papers(_coerce_int(arguments.get("workspace_id"), "workspace_id"))
    if name == "get_paper_analysis":
        return get_paper_context(_coerce_int(arguments.get("paper_id"), "paper_id"))
    if name == "get_citation_graph":
        return get_workspace_graph(_coerce_int(arguments.get("workspace_id"), "workspace_id"))

    raise AgentToolError(f"Tool '{name}' not found.", "TOOL_NOT_FOUND", 404)
