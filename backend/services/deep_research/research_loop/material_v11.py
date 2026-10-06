"""Keep complete selected source windows; never cut off a mechanism at 1400 chars."""

import re
from .material_v10 import outside_scope, description_only


def passages(
    asset, question, plan, focus_queries=(), exclude_quotes=(), targeted=False
):
    from .material import body_material
    from .material_v9 import overview_material

    payload = asset.inline_payload or {}
    text = payload.get("text", "")
    if payload.get("source_kind") == "abstract":
        return [{"passage_id": "abstract", "text": text[:7000]}]
    queries = [q["query"] for q in plan.get("queries", [])]
    sampler = (
        overview_material
        if payload.get("paper", {}).get("research_role") == "survey"
        else body_material
    )
    options = {
        "focus_queries": focus_queries or plan.get("reading_queries", []),
        "exclude_quotes": exclude_quotes,
        "targeted": targeted,
        "max_chars": 9000,
    }
    if sampler is overview_material:
        options["question"] = question
    else:
        options.update(
            return_passages=True,
            primary_targets=payload.get("paper", {}).get("primary_targets", []),
        )
    chunks = sampler(asset.asset_id, text, queries, **options)
    result, used, ranges = [], 0, []
    for chunk in chunks:
        start = text.find(chunk["text"])
        if start < 0:
            continue
        # Retain its sampled window and finish the trailing source sentence within
        # a bounded extension. No unseen prose is added to a fact by the checker.
        stop = start + len(chunk["text"])
        offset = start
        while offset < stop:
            end = min(offset + 1800, len(text))
            tail = re.search(r"[.!?。](?:\s|$)", text[end : min(end + 350, len(text))])
            if tail:
                end += tail.end()
            if not any(offset < b and a < end for a, b in ranges):
                quote = text[offset:end]
                if used + len(quote) <= 8000:
                    result.append({"passage_id": f"p_{offset}", "text": quote})
                    ranges.append((offset, end))
                    used += len(quote)
            offset = end
    return result
