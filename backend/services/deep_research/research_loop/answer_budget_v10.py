"""Size-aware final answer envelopes; original run caps never increase."""

import json


def model_cost(system, data, schema, output):
    if schema:
        system += "\n严格遵守 JSON schema：" + json.dumps(
            schema.model_json_schema(), ensure_ascii=False
        )
    encoded = len(system.encode()) + len(
        json.dumps(data, ensure_ascii=False, default=str).encode()
    )
    return 16 + (encoded + 2) // 3 + output


def envelope(tools, question_id, future_sources=0, future_assets=()):
    from .answering_v10 import (
        Answer,
        Verdict,
        ANSWER_SYSTEM,
        CHECK_SYSTEM,
        answer_output,
        check_output,
    )
    from .answering import support_materials
    from ....models.research_core import ResearchQuestion

    question = next(
        q for q in tools.repo.rows(ResearchQuestion) if q.question_id == question_id
    )
    plan = tools.plan() or {}
    units = plan.get("requirement_ledger", {}).get(question_id, [])
    evidence = tools.evidence(question_id)
    known = {e["evidence_id"]: e for e in evidence}
    papers = {
        eid: (tools.repo.asset(e["asset_id"]).inline_payload or {}).get("paper", {})
        for eid, e in known.items()
    }
    facts = [
        {
            **{
                k: e[k]
                for k in (
                    "evidence_id",
                    "asset_id",
                    "fact",
                    "conditions",
                    "source_kind",
                    "missing_fields",
                )
            },
            "title": papers[e["evidence_id"]].get("title"),
            "primary_targets": papers[e["evidence_id"]].get("primary_targets", []),
        }
        for e in evidence
    ]
    proofs, passages = support_materials(known, known, papers)
    # Pending reading may accept at most four 500-char facts with four 1400-char
    # source windows per paper. No predicted fact is passed to generation.
    for i in range(future_sources):
        source_windows = []
        if i < len(future_assets):
            from .material_v10 import passages as sample

            chunks = sample(tools.repo.asset(future_assets[i]), question.text, plan)
            source_windows = sorted(
                (c["text"] for c in chunks), key=lambda t: len(t.encode()), reverse=True
            )[:4]
        for j in range(4):
            eid = f"future_{i}_{j}"
            facts.append(
                {
                    "evidence_id": eid,
                    "asset_id": f"future_{i}",
                    "fact": "材" * 500,
                    "conditions": {"scope": "材" * 100},
                    "source_kind": "full_text",
                    "missing_fields": [],
                    "title": "材" * 100,
                    "primary_targets": [],
                }
            )
            proofs.append(
                {
                    "evidence_id": eid,
                    "fact": "材" * 500,
                    "passage_ids": [eid],
                    "source_kind": "full_text",
                    "conditions": {"scope": "材" * 100},
                }
            )
            passages.append(
                {
                    "passage_id": eid,
                    "asset_id": f"future_{i}",
                    "quote": source_windows[j]
                    if j < len(source_windows)
                    else "材" * 1400,
                    "source_kind": "full_text",
                    "paper_title": "材" * 100,
                }
            )
    scope = plan.get("constraints", {}).get(question_id, [])
    count = min(295, 4 * len(units) + 6)
    # A conservative bound for the not-yet-written answer, including IDs/conditions
    # and relation descriptions. Actual requests use the same schemas/prompts.
    claims = [
        {
            "claim_id": f"cl_{i:020}",
            "unit_id": units[i % len(units)]["unit_id"] if units else None,
            "text": "材" * 320,
            "kind": "fact",
            "evidence_ids": ["ev_" + "x" * 26] * 4,
        }
        for i in range(count)
    ]
    unit_payload = [
        {
            "unit_id": u["unit_id"],
            "state": "answered",
            "claims": claims[i * 4 : i * 4 + 4],
        }
        for i, u in enumerate(units)
    ]
    draft = {
        "question": question.text,
        "requirements": units,
        "facts": facts,
        "scope_constraints": scope,
        "reading_roles": plan.get("reading_roles", []),
    }
    check = {
        "question": question.text,
        "requirements": units,
        "units": unit_payload,
        "claims": claims,
        "explanation_ids": [],
        "conditions": claims[-2:],
        "relations": [
            {"description": "材" * 300, "evidence_ids": ["ev_" + "x" * 26] * 4}
        ]
        * 3,
        "proofs": proofs,
        "passages": passages,
        "scope_constraints": scope,
    }
    tokens = model_cost(
        ANSWER_SYSTEM, draft, Answer, answer_output(len(units))
    ) + model_cost(CHECK_SYSTEM, check, Verdict, check_output(count))
    # Core's byte estimator plus a small reserve for serialization and condition
    # fields. Follow-up planning is optional and cannot invalidate a saved answer.
    return {"llm_calls": 2, "llm_tokens": max(18000, tokens + 1800)}


def can_read(tools, question_id, sources, asset_ids=()):
    budget = envelope(
        tools, question_id, future_sources=sources, future_assets=asset_ids
    )
    # Extraction contains at most 7000 source chars each; checks repeat at most
    # four source windows plus bounded contexts and output. Keep all in one cap.
    reading_tokens = 4500 + sources * 9500
    return (
        tools.resources.remaining("llm_calls") >= 1 + sources + budget["llm_calls"]
        and tools.resources.remaining("llm_tokens")
        >= reading_tokens + budget["llm_tokens"]
    )
