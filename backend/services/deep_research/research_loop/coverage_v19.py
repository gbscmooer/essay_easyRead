"""Reuse checked claims only when their original support covers this obligation."""

from ..document_evidence import normalize_text

SLOTS = {"input": "输入/正常参考", "operation": "建模操作", "scoring": "测试异常判据"}


def element_supported(element, ids, accepted, known):
    cids = element.get("claim_ids", [])
    quote = normalize_text(element.get("support_quote", ""))
    if not element.get("covered") or not cids or len(quote) < 10:
        return False
    if any(cid not in ids or cid not in accepted for cid in cids):
        return False
    aid, kind = element.get("document_id"), element.get("kind")
    for cid in cids:
        for span in accepted[cid].get("check", {}).get("supported_by", []):
            evidence = known.get(span.get("evidence_id"), {})
            if aid and evidence.get("asset_id") != aid:
                continue
            # A survey's taxonomy can explain a class, but cannot stand in for an
            # original paper's own input/operation/test criterion.
            if kind in SLOTS and evidence.get("verification", {}).get("source_attribution") != "own":
                continue
            if quote in normalize_text(span.get("quote", "")):
                return True
    return False


def element_complete(review, ids, accepted, known):
    elements = review.get("elements", [])
    if not elements or review.get("missing_elements"):
        return False
    if not all(element_supported(e, ids, accepted, known) for e in elements):
        return False
    if review.get("scoring_chain_required"):
        methods = {e.get("document_id") for e in elements if e.get("kind") in SLOTS}
        methods.update(
            known[eid]["asset_id"]
            for cid in ids if cid in accepted
            for eid in accepted[cid].get("evidence_ids", []) if eid in known
            if known[eid].get("verification", {}).get("source_attribution") == "own"
            and known[eid].get("evidence_kind") in ("fact", "mechanism")
        )
        if not methods or "" in methods:
            return False
        covered = {(e["document_id"], e["kind"]) for e in elements}
        if any((aid, slot) not in covered for aid in methods for slot in SLOTS):
            return False
    return True


def element_gaps(review, ids, accepted, known):
    if element_complete(review, ids, accepted, known):
        return []
    gaps = list(review.get("missing_elements", []))
    for element in review.get("elements", []):
        if not element_supported(element, ids, accepted, known):
            gaps.append(SLOTS.get(element.get("kind"), "必要内容") + "尚无对应断言及原文依据")
    if review.get("scoring_chain_required"):
        methods = {e.get("document_id") for e in review.get("elements", []) if e.get("kind") in SLOTS}
        covered = {(e.get("document_id"), e.get("kind")) for e in review.get("elements", []) if element_supported(e, ids, accepted, known)}
        for aid in sorted(methods):
            for slot, label in SLOTS.items():
                if (aid, slot) not in covered:
                    gaps.append(label + "尚未形成完整有据回答")
    return list(dict.fromkeys(gaps or ["必要内容与原文支持的对应关系尚未核查"]))[:8]


def reusable_ids(review, local_ids, accepted, request):
    """Only explicit coverage links can add a claim to another unit's visible body."""
    result = list(local_ids)
    for element in review.get("elements", []):
        for cid in element.get("claim_ids", []):
            claim = accepted.get(cid)
            if claim and "relation_index" not in claim and cid not in result and claim.get("kind") == request.get("response_kind", "fact"):
                result.append(cid)
    return result


def render_units(ledger):
    sections = []
    for unit in ledger:
        claims = unit.get("accepted_claims", [])
        lines = ["### " + unit["requirement"]]
        for claim in claims:
            prefix = "依据材料的建议：" if claim.get("kind") == "inference" else ""
            lines.append(prefix + claim["text"] + "".join("【" + eid + "】" for eid in claim["evidence_ids"]))
        if not unit.get("resolved"):
            missing = unit.get("check", {}).get("missing_elements", [])
            lines.append("待补充：" + ("；".join(missing[:3]) if missing else "该项需求仍只有部分材料依据。"))
        sections.append("\n\n".join(lines))
    return "\n\n".join(sections)
