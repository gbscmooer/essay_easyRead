"""Delivery policy for standalone RAG and follow-up answers; no workflow mutation."""

import hashlib


def deliver_audited_answer(candidate, audit):
    text_hash = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    claims = audit.get("claims") or []
    checked = (
        audit.get("status") == "completed"
        and audit.get("coverage_complete") is True
        and audit.get("binding", {}).get("text_hash") == text_hash
        and bool(claims)
        and not audit.get("truncated")
        and all(
            c.get("label") == "supported" and not c.get("validation_errors")
            for c in claims
        )
    )
    if checked:
        return candidate, "internally_checked"
    # Keep the candidate only as a diagnostic asset in callers that persist it.
    # Counts of zero on an incomplete audit must never silently imply support.
    if audit.get("unsupported_count") or any(
        c.get("label") in {"contradicted", "insufficient"} for c in claims
    ):
        return (
            "本轮候选回答包含无法由所引资料支持的结论，未作为正式答案发布。请补充材料或缩小问题范围；可查看来源自行核查。",
            "withheld",
        )
    return (
        "本轮回答的证据核查未完成，暂未发布文献结论。请稍后重试或补充资料；可查看来源自行核查。",
        "unchecked",
    )
