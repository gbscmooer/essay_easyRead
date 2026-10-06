"""Parsing helpers shared by all structured LLM calls."""

from __future__ import annotations

import json
import re
from typing import Any


def final_content_after_thinking(text: str) -> str:
    """Separate complete leading think blocks; never parse reasoning as output.

    The adapter uses this only when its caller explicitly requests separate
    reasoning. A tag inside an actual JSON string is left intact.
    """
    value = str(text or "").lstrip()
    while value.startswith("<think>"):
        depth, end = 0, None
        for tag in re.finditer(r"</?think>", value):
            depth += 1 if tag.group() == "<think>" else -1
            if depth == 0:
                end = tag.end()
                break
        if end is None:
            return ""
        value = value[end:].lstrip()
    return value


def strip_markdown_fence(text: str) -> str:
    framed = str(text or "").strip()
    if framed.startswith("```") and framed.endswith("```"):
        first, separator, body = framed.partition("\n")
        if separator and first.lower() in ("```", "```json"):
            return body[:-3].strip()
    return framed


def extract_balanced_json(text: str) -> str | None:
    value = strip_markdown_fence(text)
    positions = [pos for pos in (value.find("{"), value.find("[")) if pos >= 0]
    if not positions:
        return None
    start = min(positions)
    opening = value[start]
    closing = "}" if opening == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(value)):
        char = value[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return value[start:index + 1]
    return None


def parse_json(text: str, *, expected: type = dict) -> Any:
    framed = strip_markdown_fence(text)
    candidate = extract_balanced_json(framed) or framed
    data = json.loads(candidate)
    if not isinstance(data, expected):
        label = "object" if expected is dict else "array" if expected is list else expected.__name__
        raise ValueError(f"LLM structured output is not a JSON {label}")
    return data
