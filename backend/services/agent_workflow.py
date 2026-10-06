"""LangGraph workflow definition for paper-grounded development agents.

The default API exposes a serializable workflow spec.  LangGraph itself is an
optional dependency: importing this module must not require it, so normal backend
startup and tests remain unaffected.
"""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, TypedDict


class PaperDrivenDevState(TypedDict, total=False):
    """State carried by the optional LangGraph workflow."""

    workspace_id: int
    goal: str
    search_query: str
    search_results: dict[str, Any]
    selected_paper_ids: list[int]
    evidence_pack: dict[str, Any]
    paper_contexts: list[dict[str, Any]]
    graph_context: list[dict[str, Any]]
    dev_brief: dict[str, Any]
    final_report: dict[str, Any]


ToolCaller = Callable[[str, dict[str, Any]], Awaitable[Any] | Any]


def get_langgraph_workflow_spec() -> dict[str, Any]:
    """Return the stable workflow contract used by external agents."""

    return {
        "name": "paper_grounded_development",
        "version": "1.0.0",
        "state_schema": {
            "workspace_id": "int",
            "goal": "str",
            "search_query": "str",
            "search_results": "dict",
            "selected_paper_ids": "list[int]",
            "evidence_pack": "dict",
            "paper_contexts": "list[dict]",
            "graph_context": "list[dict]",
            "dev_brief": "dict",
            "final_report": "dict",
        },
        "nodes": [
            {
                "id": "understand_task",
                "description": "Normalize the user's development goal into a paper-search query.",
                "tool": None,
            },
            {
                "id": "search_papers",
                "description": "Find candidate papers in the selected workspace or local library.",
                "tool": "paper_search",
            },
            {
                "id": "retrieve_evidence",
                "description": "Retrieve vector-index evidence snippets for the development goal.",
                "tool": "paper_retrieve_evidence",
            },
            {
                "id": "load_paper_context",
                "description": "Load structured paper profiles for evidence-bearing papers.",
                "tool": "paper_get",
            },
            {
                "id": "expand_graph_context",
                "description": "Read local graph neighborhoods for selected papers without rebuilding the graph.",
                "tool": "graph_neighbors",
            },
            {
                "id": "create_dev_brief",
                "description": "Create a development brief with citations and implementation guardrails.",
                "tool": "dev_brief",
            },
            {
                "id": "final_report",
                "description": "Return the evidence-backed brief for a coding agent to execute.",
                "tool": None,
            },
        ],
        "edges": [
            ["understand_task", "search_papers"],
            ["search_papers", "retrieve_evidence"],
            ["retrieve_evidence", "load_paper_context"],
            ["load_paper_context", "expand_graph_context"],
            ["expand_graph_context", "create_dev_brief"],
            ["create_dev_brief", "final_report"],
        ],
        "tool_contract": {
            "call_signature": "await call_tool(name: str, arguments: dict) -> dict",
            "required_tools": [
                "paper_search",
                "paper_retrieve_evidence",
                "paper_get",
                "graph_neighbors",
                "dev_brief",
            ],
        },
        "evidence_policy": [
            "Coding plans must cite evidence ranks or structured paper fields.",
            "Unsupported paper claims must be labeled as assumptions.",
            "The workflow consumes persisted graph snapshots and does not alter the graph algorithm.",
        ],
    }


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def build_langgraph_workflow(call_tool: ToolCaller):
    """Build a LangGraph StateGraph for paper-grounded development.

    Args:
        call_tool: async or sync callable with signature
            ``call_tool(name, arguments)``. It can call the HTTP API, the MCP
            server, or ``services.agent_tools.call_agent_tool`` directly.

    Raises:
        RuntimeError: if ``langgraph`` is not installed.
    """

    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise RuntimeError(
            "LangGraph is optional. Install langgraph to build the executable workflow."
        ) from exc

    async def understand_task(state: PaperDrivenDevState) -> PaperDrivenDevState:
        goal = str(state.get("goal") or "").strip()
        return {"search_query": goal}

    async def search_node(state: PaperDrivenDevState) -> PaperDrivenDevState:
        data = await _maybe_await(
            call_tool(
                "paper_search",
                {
                    "query": state["search_query"],
                    "workspace_id": state.get("workspace_id"),
                    "limit": 8,
                    "include_expanded": True,
                },
            )
        )
        selected = [
            int(item["paper_id"])
            for item in data.get("items", [])
            if item.get("paper_id") is not None
        ][:8]
        return {"search_results": data, "selected_paper_ids": selected}

    async def evidence_node(state: PaperDrivenDevState) -> PaperDrivenDevState:
        from .agent_tools import AgentToolError
        try:
            data = await _maybe_await(
                call_tool(
                    "paper_retrieve_evidence",
                    {
                        "workspace_id": state["workspace_id"],
                        "query": state["goal"],
                        "top_k": 8,
                        "paper_ids": state.get("selected_paper_ids") or None,
                    },
                )
            )
        except AgentToolError as exc:
            if exc.code != "CHROMA_NOT_INITIALIZED":
                raise
            data = {"evidence": [], "warnings": ["工作区尚无向量索引，本次没有全文检索依据"]}
        evidence_paper_ids: list[int] = []
        for item in data.get("evidence", []):
            pid = item.get("paper_id")
            if isinstance(pid, int) and pid not in evidence_paper_ids:
                evidence_paper_ids.append(pid)
        return {"evidence_pack": data, "selected_paper_ids": evidence_paper_ids or state.get("selected_paper_ids", [])}

    async def paper_context_node(state: PaperDrivenDevState) -> PaperDrivenDevState:
        contexts = []
        for paper_id in state.get("selected_paper_ids", [])[:6]:
            contexts.append(await _maybe_await(call_tool("paper_get", {"paper_id": paper_id})))
        return {"paper_contexts": contexts}

    async def graph_node(state: PaperDrivenDevState) -> PaperDrivenDevState:
        graph_context = []
        for paper_id in state.get("selected_paper_ids", [])[:5]:
            try:
                graph_context.append(
                    await _maybe_await(
                        call_tool(
                            "graph_neighbors",
                            {
                                "workspace_id": state["workspace_id"],
                                "paper_id": paper_id,
                                "depth": 1,
                                "limit": 20,
                            },
                        )
                    )
                )
            except Exception:
                continue
        return {"graph_context": graph_context}

    async def brief_node(state: PaperDrivenDevState) -> PaperDrivenDevState:
        data = await _maybe_await(
            call_tool(
                "dev_brief",
                {
                    "workspace_id": state["workspace_id"],
                    "goal": state["goal"],
                    "top_k": 8,
                    "paper_ids": state.get("selected_paper_ids") or None,
                },
            )
        )
        return {"dev_brief": data}

    async def report_node(state: PaperDrivenDevState) -> PaperDrivenDevState:
        return {
            "final_report": {
                "goal": state.get("goal"),
                "dev_brief": state.get("dev_brief"),
                "evidence_pack": state.get("evidence_pack"),
                "paper_context_count": len(state.get("paper_contexts", [])),
                "graph_context_count": len(state.get("graph_context", [])),
            }
        }

    graph = StateGraph(PaperDrivenDevState)
    graph.add_node("understand_task", understand_task)
    graph.add_node("search_papers", search_node)
    graph.add_node("retrieve_evidence", evidence_node)
    graph.add_node("load_paper_context", paper_context_node)
    graph.add_node("expand_graph_context", graph_node)
    graph.add_node("create_dev_brief", brief_node)
    graph.add_node("final_report", report_node)

    graph.set_entry_point("understand_task")
    graph.add_edge("understand_task", "search_papers")
    graph.add_edge("search_papers", "retrieve_evidence")
    graph.add_edge("retrieve_evidence", "load_paper_context")
    graph.add_edge("load_paper_context", "expand_graph_context")
    graph.add_edge("expand_graph_context", "create_dev_brief")
    graph.add_edge("create_dev_brief", "final_report")
    graph.add_edge("final_report", END)
    return graph.compile()
