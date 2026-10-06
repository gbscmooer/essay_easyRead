"""Deduplicate immutable original windows while preserving claim-local ownership."""

from copy import deepcopy

from .contracts import digest
from . import source_refs_v11 as previous

resolve_answer = previous.resolve_answer


def catalog(evidence, papers):
    aliases, spans, facts, windows = {}, {}, [], {}
    for i, row in enumerate(sorted(evidence, key=lambda e: e["evidence_id"]), 1):
        ref, eid = f"E{i}", row["evidence_id"]
        aliases[ref] = eid
        source_ids = []
        for span in row["spans"]:
            key = digest(
                {
                    "asset_id": row["asset_id"],
                    "doc_content_hash": row.get("doc_content_hash"),
                    "source_kind": row["source_kind"],
                    "span": span,
                }
            )
            if key not in windows:
                sid = f"S{len(spans) + 1}"
                windows[key] = sid
                spans[sid] = {
                    "source_id": sid,
                    "evidence_refs": [],
                    "evidence_ids": [],
                    "quote": span["quote"],
                    "source_kind": row["source_kind"],
                    "title": papers[eid].get("title", ""),
                    "window_hash": key,
                }
            sid = windows[key]
            if eid not in spans[sid]["evidence_ids"]:
                spans[sid]["evidence_ids"].append(eid)
                spans[sid]["evidence_refs"].append(ref)
            source_ids.append(sid)
        facts.append(
            {
                "evidence_id": ref,
                "fact": row["fact"],
                "source_ids": list(dict.fromkeys(source_ids)),
                "source_kind": row["source_kind"],
                "conditions": row["conditions"],
                "missing_fields": row["missing_fields"],
                "title": papers[eid].get("title", ""),
                "primary_targets": papers[eid].get("primary_targets", []),
            }
        )
    return aliases, spans, facts


def source_payload(spans, cited=None):
    result = []
    for source in spans.values():
        allowed = [
            ref
            for ref, eid in zip(source["evidence_refs"], source["evidence_ids"])
            if cited is None or eid in cited
        ]
        if allowed:
            result.append(
                {
                    **{
                        k: v
                        for k, v in source.items()
                        if k not in ("evidence_ids", "evidence_refs", "window_hash")
                    },
                    "evidence_refs": allowed,
                }
            )
    return result


def wire_context(units, known, papers):
    context = previous.wire_context(units, known, papers)
    aliases, spans, facts = catalog(list(known.values()), papers)
    context.update(evidence=aliases, sources=spans, facts=facts)
    return context


def checker_payload(claims, raw, context):
    payload, claim_map = previous.checker_payload(
        claims, raw, {**context, "sources": {}}
    )
    cited = {eid for c in claims for eid in c["evidence_ids"]}
    payload["sources"] = source_payload(context["sources"], cited)
    context["claim_evidence"] = {c["claim_id"]: set(c["evidence_ids"]) for c in claims}
    return payload, claim_map


def resolve_checks(raw, context, claim_map):
    result = previous.resolve_checks(
        {**deepcopy(raw), "claims": []}, context, claim_map
    )
    result["claims"] = []
    for check in raw["claims"]:
        row = deepcopy(check)
        cid = claim_map.get(row["claim_id"], "invalid_alias:" + row["claim_id"])
        row["claim_id"] = cid
        row["source_ids"] = list(row["supported_by"])
        excerpts = []
        for sid in row["source_ids"]:
            source = context["sources"].get(sid)
            ids = (
                [
                    e
                    for e in source["evidence_ids"]
                    if e in context.get("claim_evidence", {}).get(cid, set())
                ]
                if source
                else []
            )
            excerpts.extend(
                {"evidence_id": eid, "quote": source["quote"]} for eid in ids
            )
            if not ids:
                excerpts.append({"evidence_id": "invalid_source:" + sid, "quote": ""})
        row["supported_by"] = excerpts
        result["claims"].append(row)
    return result
