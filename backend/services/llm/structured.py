"""Explicit structured return channel; no function is executed by this module."""

import json
from copy import deepcopy

RESULT_FUNCTION = "emit_structured_result"


def result_tool(schema):
    body = deepcopy(schema)
    definitions = body.pop("$defs", None)
    parameters = {
        "type": "object",
        "properties": {"result": body},
        "required": ["result"],
        "additionalProperties": False,
    }
    if definitions:
        parameters["$defs"] = definitions
    return {
        "type": "function",
        "function": {
            "name": RESULT_FUNCTION,
            "description": "Return the requested structured value in the single result parameter.",
            "parameters": parameters,
        },
    }


def unwrap_result(value, expected=dict):
    if (
        not isinstance(value, dict)
        or set(value) != {"result"}
        or not isinstance(value["result"], expected)
    ):
        raise ValueError(
            "Structured result must contain only the typed result parameter"
        )
    return value["result"]


def requests_result_tool(extra_body):
    tools = (extra_body or {}).get("tools") or []
    return (
        isinstance(tools, list)
        and len(tools) == 1
        and isinstance(tools[0], dict)
        and tools[0].get("type") == "function"
        and isinstance(tools[0].get("function"), dict)
        and tools[0].get("function", {}).get("name") == RESULT_FUNCTION
    )


def tool_input_bytes(extra_body):
    """Keep the Core and prospective reservation aware of the extra schema."""
    return (
        len(json.dumps({"tools": extra_body["tools"]}, ensure_ascii=False).encode())
        if requests_result_tool(extra_body)
        else 0
    )


def result_arguments(message):
    calls = getattr(message, "tool_calls", None) or []
    if len(calls) != 1:
        return None
    call = calls[0]
    function = getattr(call, "function", None)
    if (
        getattr(call, "type", None) != "function"
        or getattr(function, "name", None) != RESULT_FUNCTION
    ):
        return None
    args = getattr(function, "arguments", None)
    return args if isinstance(args, str) and args.strip() else None
