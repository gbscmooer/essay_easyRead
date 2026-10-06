"""Essay Read MCP stdio server.

Run with:
    python -m backend.mcp_server

The stdio process speaks JSON-RPC for MCP clients.  All domain behavior is
delegated to ``backend.services.agent_tools`` so the MCP adapter stays thin and
does not duplicate database, graph, or RAG logic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import traceback
from typing import Any


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("essay_read_mcp")


LEGACY_TOOL_DESCRIPTORS: list[dict[str, Any]] = [
    {
        "name": "list_workspaces",
        "description": "Backward-compatible alias for listing research workspaces.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_papers_in_workspace",
        "description": "Backward-compatible alias for listing papers in a workspace.",
        "input_schema": {
            "type": "object",
            "properties": {"workspace_id": {"type": "integer"}},
            "required": ["workspace_id"],
        },
    },
    {
        "name": "get_paper_analysis",
        "description": "Backward-compatible alias for paper_get.",
        "input_schema": {
            "type": "object",
            "properties": {"paper_id": {"type": "integer"}},
            "required": ["paper_id"],
        },
    },
    {
        "name": "get_citation_graph",
        "description": "Backward-compatible alias for graph_get.",
        "input_schema": {
            "type": "object",
            "properties": {"workspace_id": {"type": "integer"}},
            "required": ["workspace_id"],
        },
    },
    {
        "name": "query_workspace_rag",
        "description": "Backward-compatible RAG helper. Requires the FastAPI backend running on ESSAY_READ_API_BASE or http://127.0.0.1:8000.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "integer"},
                "question": {"type": "string"},
                "top_k": {"type": "integer", "default": 5},
                "mode": {"type": "string", "enum": ["vector", "graph"], "default": "graph"},
            },
            "required": ["workspace_id", "question"],
        },
    },
]


def _json_text(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _mcp_content(data: Any) -> dict[str, Any]:
    if isinstance(data, str):
        text = data
    else:
        text = _json_text(data)
    return {"content": [{"type": "text", "text": text}]}


def _tool_descriptors() -> list[dict[str, Any]]:
    from backend.services.agent_tools import list_agent_tools

    return list_agent_tools() + LEGACY_TOOL_DESCRIPTORS


def _mcp_tools_list() -> dict[str, Any]:
    return {
        "tools": [
            {
                "name": tool["name"],
                "description": tool["description"],
                "inputSchema": tool["input_schema"],
            }
            for tool in _tool_descriptors()
        ]
    }


def _call_research_query_api(arguments: dict[str, Any]) -> dict[str, Any]:
    import urllib.error
    import urllib.request

    workspace_id = int(arguments.get("workspace_id"))
    payload = {
        "question": arguments.get("question") or arguments.get("query") or "",
        "top_k": int(arguments.get("top_k", 5)),
        "mode": arguments.get("mode", "graph"),
    }
    base_url = os.environ.get("ESSAY_READ_API_BASE", "http://127.0.0.1:8000").rstrip("/")
    url = f"{base_url}/api/research/workspaces/{workspace_id}/query"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"RAG API returned {exc.code}: {body}") from exc


async def _call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    if tool_name == "query_workspace_rag":
        return await asyncio.to_thread(_call_research_query_api, arguments)

    from backend.services.agent_tools import call_agent_tool

    return await call_agent_tool(tool_name, arguments)


def handle_request(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Handle MCP JSON-RPC methods and return the JSON-RPC result payload."""

    params = params or {}
    if method == "initialize":
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "essay-read-mcp", "version": "1.1.0"},
        }

    if method == "tools/list":
        return _mcp_tools_list()

    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {}) or {}
        if not tool_name:
            return {
                "isError": True,
                "content": [{"type": "text", "text": "Missing tool name."}],
            }
        logger.info("Executing MCP tool %s", tool_name)
        try:
            data = asyncio.run(_call_tool(tool_name, arguments))
            return _mcp_content(data)
        except Exception as exc:
            from backend.services.agent_tools import AgentToolError

            if isinstance(exc, AgentToolError):
                logger.warning("MCP tool domain error: %s", exc)
                return {
                    "isError": True,
                    "content": [
                        {
                            "type": "text",
                            "text": _json_text({"detail": exc.message, "code": exc.code}),
                        }
                    ],
                }
            logger.error("MCP tool failed: %s\n%s", exc, traceback.format_exc())
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Internal MCP tool error: {exc}"}],
            }

    return {
        "isError": True,
        "content": [{"type": "text", "text": f"Unsupported method '{method}'."}],
    }


def _write_response(response: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    logger.info("Initializing Essay Read MCP stdio transport")
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
        except (ValueError, TypeError):
            _write_response({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": "Parse error"}})
            continue
        if not isinstance(req, dict):
            _write_response({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32600, "message": "Invalid request"}})
            continue
        if "id" not in req:
            continue
        req_id = req["id"]
        try:
            result = handle_request(req.get("method"), req.get("params", {}))
            response: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
            if result.get("isError"):
                response["error"] = {"code": -32603, "message": result["content"][0]["text"]}
            else:
                response["result"] = result
        except Exception:
            logger.exception("Error handling JSON-RPC request")
            response = {"jsonrpc": "2.0", "id": req_id,
                        "error": {"code": -32603, "message": "Internal error"}}
        _write_response(response)



if __name__ == "__main__":
    main()
