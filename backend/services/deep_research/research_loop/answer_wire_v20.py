"""Request-local alias contracts; never translate an invalid source selection."""

from copy import deepcopy


def request_contract(schema, data):
    """Share the exact native schema/input with prospective accounting."""
    spec, payload = deepcopy(schema.model_json_schema()), deepcopy(data)
    name = schema.__name__
    if name not in ("Answer", "Verdict", "ClaimVerdicts", "CoverageVerdicts") or not data:
        return spec, payload
    definitions = spec.get("$defs", {})

    def enum(definition, field, values, *, array=False):
        prop = definitions.get(definition, {}).get("properties", {}).get(field)
        if prop is not None and values:
            target = prop.get("items", {}) if array else prop
            target["enum"] = list(dict.fromkeys(values))

    units = [u["unit_id"] for u in data.get("requirements", [])]
    documents = [d["asset_id"] for d in data.get("documents", [])]
    if name == "Answer":
        enum("Unit", "unit_id", units)
        enum(
            "Claim",
            "evidence_ids",
            [f["evidence_id"] for f in data.get("facts", [])],
            array=True,
        )
        for side in ("subject_asset_id", "object_asset_id"):
            enum("RelationProposal", side, documents)
    else:
        source_ids = [s["source_id"] for s in data.get("sources", [])]
        claim_ids = [c["claim_id"] for c in data.get("claims", [])]
        enum("ClaimCheck", "claim_id", claim_ids)
        enum("ClaimCheck", "supported_by", source_ids, array=True)
        enum("UnitCheck", "unit_id", units)
        enum("CoverageElement", "document_id", ["", *documents])
        enum("CoverageElement", "claim_ids", claim_ids, array=True)
        enum(
            "ScopeCheck",
            "constraint_id",
            [c["constraint_id"] for c in data.get("scope_constraints", [])],
        )
        enum("ScopeCheck", "violating_claim_ids", claim_ids, array=True)
        for claim in payload.get("claims", []):
            cited = set(claim["evidence_ids"])
            claim["allowed_support_sources"] = [
                s["source_id"]
                for s in payload.get("sources", [])
                if cited.intersection(s.get("evidence_refs", []))
            ]
    return spec, payload


def source_reference_errors(raw, data):
    """An E label is never a substitute for S, even when both contain a number."""
    claims = {c["claim_id"]: c for c in data.get("claims", [])}
    sources = data.get("sources", [])
    issues = []
    for index, check in enumerate(raw.get("claims", [])):
        claim = claims.get(check["claim_id"])
        allowed = [
            s["source_id"]
            for s in sources
            if claim
            and set(claim["evidence_ids"]).intersection(s.get("evidence_refs", []))
        ]
        if any(sid not in allowed for sid in check["supported_by"]):
            issues.append(
                {
                    "path": f"claims.{index}.supported_by",
                    "type": "source_reference",
                    "allowed": allowed,
                }
            )
    return issues
