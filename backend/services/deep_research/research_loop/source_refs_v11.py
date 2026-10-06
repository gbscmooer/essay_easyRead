"""Request-local aliases resolve only to immutable source spans, never model quotes."""

from copy import deepcopy


def catalog(evidence, papers):
    aliases, spans, facts = {}, {}, []
    for index, row in enumerate(sorted(evidence, key=lambda e: e["evidence_id"]), 1):
        ref = f"E{index}"
        aliases[ref] = row["evidence_id"]
        source_ids = []
        for span in row["spans"]:
            sid = f"S{len(spans) + 1}"
            spans[sid] = {
                "source_id": sid,
                "evidence_ref": ref,
                "evidence_id": row["evidence_id"],
                "quote": span["quote"],
                "source_kind": row["source_kind"],
                "title": papers[row["evidence_id"]].get("title", ""),
            }
            source_ids.append(sid)
        facts.append(
            {
                "evidence_id": ref,
                "fact": row["fact"],
                "source_ids": source_ids,
                "source_kind": row["source_kind"],
                "conditions": row["conditions"],
                "missing_fields": row["missing_fields"],
                "title": papers[row["evidence_id"]].get("title", ""),
                "primary_targets": papers[row["evidence_id"]].get(
                    "primary_targets", []
                ),
            }
        )
    return aliases, spans, facts


def source_payload(spans, cited=None):
    return [
        {k: v for k, v in s.items() if k != "evidence_id"}
        for s in spans.values()
        if cited is None or s["evidence_id"] in cited
    ]


def wire_context(units, known, papers):
    aliases, spans, facts = catalog(list(known.values()), papers)
    unit_map = {f"U{i}": u["unit_id"] for i, u in enumerate(units, 1)}
    asset_map = {
        f"D{i}": aid
        for i, aid in enumerate(dict.fromkeys(e["asset_id"] for e in known.values()), 1)
    }
    wire_units = [
        {**u, "unit_id": next(k for k, v in unit_map.items() if v == u["unit_id"])}
        for u in units
    ]
    assets = [
        {
            "asset_id": alias,
            "title": next(
                papers[eid].get("title", "")
                for eid, e in known.items()
                if e["asset_id"] == aid
            ),
            "evidence_ids": [
                ref for ref, eid in aliases.items() if known[eid]["asset_id"] == aid
            ],
        }
        for alias, aid in asset_map.items()
    ]
    return {
        "evidence": aliases,
        "sources": spans,
        "units": unit_map,
        "assets": asset_map,
        "wire_units": wire_units,
        "facts": facts,
        "documents": assets,
    }


def resolve_answer(raw, context):
    result = deepcopy(raw)

    def resolve(value, table):
        return table.get(value, "invalid_alias:" + value)

    def claim(c):
        c["evidence_ids"] = [resolve(x, context["evidence"]) for x in c["evidence_ids"]]

    for unit in result["units"]:
        unit["unit_id"] = resolve(unit["unit_id"], context["units"])
        for c in unit["claims"]:
            claim(c)
    for e in result["explanations"]:
        claim(e["claim"])
    for c in result["conditions"]:
        claim(c)
    for r in result["relations"]:
        for key in ("subject_asset_id", "object_asset_id"):
            r[key] = resolve(r[key], context["assets"])
        for key in ("subject_evidence_ids", "object_evidence_ids"):
            r[key] = [resolve(x, context["evidence"]) for x in r[key]]
    return result


def checker_payload(claims, raw, context):
    inverse = {v: k for k, v in context["evidence"].items()}
    unit_inverse = {v: k for k, v in context["units"].items()}
    claim_map = {f"C{i}": c["claim_id"] for i, c in enumerate(claims, 1)}
    rows = [
        {
            **{
                k: v
                for k, v in c.items()
                if k not in ("claim_id", "evidence_ids", "unit_id")
            },
            "claim_id": next(k for k, v in claim_map.items() if v == c["claim_id"]),
            "unit_id": unit_inverse.get(c.get("unit_id")),
            "evidence_ids": [inverse.get(e, e) for e in c["evidence_ids"]],
        }
        for c in claims
    ]
    cited = {e for c in claims for e in c["evidence_ids"]}
    # No derived fact summaries or adjacent prose in the independent source check.
    return {
        "requirements": context["wire_units"],
        "claims": rows,
        "unit_states": [
            {
                "unit_id": unit_inverse.get(u["unit_id"], u["unit_id"]),
                "state": u["state"],
            }
            for u in raw["units"]
        ],
        "relations": [
            {
                **relation,
                "subject_asset_id": next(
                    (
                        k
                        for k, v in context["assets"].items()
                        if v == relation["subject_asset_id"]
                    ),
                    relation["subject_asset_id"],
                ),
                "object_asset_id": next(
                    (
                        k
                        for k, v in context["assets"].items()
                        if v == relation["object_asset_id"]
                    ),
                    relation["object_asset_id"],
                ),
                "subject_evidence_ids": [
                    inverse.get(e, e) for e in relation["subject_evidence_ids"]
                ],
                "object_evidence_ids": [
                    inverse.get(e, e) for e in relation["object_evidence_ids"]
                ],
            }
            for relation in raw["relations"]
        ],
        "sources": source_payload(context["sources"], cited),
    }, claim_map


def resolve_checks(raw, context, claim_map):
    result = deepcopy(raw)
    for row in result["claims"]:
        row["claim_id"] = claim_map.get(
            row["claim_id"], "invalid_alias:" + row["claim_id"]
        )
        row["source_ids"] = list(row["supported_by"])
        row["supported_by"] = [
            {
                "evidence_id": context["sources"][s]["evidence_id"],
                "quote": context["sources"][s]["quote"],
            }
            if s in context["sources"]
            else {"evidence_id": "invalid_source:" + s, "quote": ""}
            for s in row["source_ids"]
        ]
    for row in result["units"]:
        row["unit_id"] = context["units"].get(
            row["unit_id"], "invalid_alias:" + row["unit_id"]
        )
    inverse = {k: v for k, v in context["evidence"].items()}
    result["conflicts"] = [
        " ".join(inverse.get(t, t) for t in value.split())
        for value in result["conflicts"]
    ]
    return result
