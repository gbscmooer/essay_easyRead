"""Bound diagnostic explanations without repairing claims or support decisions."""

from copy import deepcopy
from hashlib import sha256


def bound_reasons(raw, schema):
    """Only known Schema `reason` strings may be shortened; preserve all else.

    A long explanation must not erase independently checked booleans or source
    IDs. Unknown fields, facts, quotes, missing elements and invalid verdicts
    still fail the original Schema. Audit records contain no response text.
    """
    root = schema.model_json_schema()
    result, audit = deepcopy(raw), []

    def resolve(node):
        ref = node.get("$ref", "")
        if ref.startswith("#/$defs/"):
            return root.get("$defs", {}).get(ref.removeprefix("#/$defs/"), node)
        return node

    def visit(value, node, path=()):
        node = resolve(node)
        if "anyOf" in node:
            # Nullable navigation choices use anyOf(ref, null). Only traverse
            # object/array branches; final union validation remains unchanged.
            for branch in node["anyOf"]:
                branch = resolve(branch)
                if (
                    isinstance(value, dict)
                    and (branch.get("type") == "object" or "properties" in branch)
                ) or (isinstance(value, list) and branch.get("type") == "array"):
                    visit(value, branch, path)
            return
        if isinstance(value, dict):
            for key, rule in node.get("properties", {}).items():
                if key not in value:
                    continue
                rule = resolve(rule)
                limit = rule.get("maxLength")
                text = value[key]
                if (
                    key == "reason"
                    and rule.get("type") == "string"
                    and isinstance(text, str)
                    and isinstance(limit, int)
                    and len(text) > limit
                ):
                    marker = "…[诊断截短]"
                    value[key] = text[: max(0, limit - len(marker))] + marker[:limit]
                    audit.append(
                        {
                            "path": ".".join(map(str, (*path, key))),
                            "original_length": len(text),
                            "limit": limit,
                            "original_hash": sha256(text.encode()).hexdigest(),
                        }
                    )
                else:
                    visit(text, rule, (*path, key))
        elif isinstance(value, list):
            for i, child in enumerate(value):
                visit(child, node.get("items", {}), (*path, i))

    visit(result, root)
    return result, audit
