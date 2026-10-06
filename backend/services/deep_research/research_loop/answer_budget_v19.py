"""Reserve the checked narrative using unique known source windows and six facts."""

import json


def model_cost(system, data, schema, output, *, native_result=False):
    wire_schema = schema.model_json_schema() if schema else None
    if native_result and schema:
        from .answer_wire_v20 import request_contract
        wire_schema, data = request_contract(schema, data)
    if schema:
        system += "\n严格遵守 JSON schema：" + json.dumps(
            wire_schema, ensure_ascii=False
        )
    encoded = len(system.encode()) + len(
        json.dumps(data, ensure_ascii=False, default=str).encode()
    )
    if native_result and schema:
        from ...llm.structured import result_tool, tool_input_bytes
        encoded += tool_input_bytes({"tools": [result_tool(wire_schema)]})
    return 16 + (encoded + 2) // 3 + output


def envelope(tools, question_id, future_sources=0, future_assets=()):
    from .answering_v19 import (
        Answer,
        Verdict,
        ANSWER_SYSTEM,
        CHECK_SYSTEM,
        answer_output,
        check_output,
    )
    from .source_refs_v15 import wire_context, source_payload
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
    context = wire_context(units, known, papers)
    facts = context["facts"]
    passages = source_payload(context["sources"])
    # Known windows are deduplicated exactly as actual source_refs_v15 requests.
    # Unknown future downloads retain a conservative four-window bound. Padding
    # known abstracts with imaginary windows wrongly prevented useful reading.
    for i in range(future_sources):
        source_windows = None
        if i < len(future_assets):
            from .material_v11 import passages as sample
            source_windows = list(dict.fromkeys(
                c["text"] for c in sample(tools.repo.asset(future_assets[i]), question.text, plan)
            ))[:4]
        if source_windows is None:
            source_windows = ["材" * 2150] * 4
        for j in range(6):
            facts.append({
                "evidence_id": f"future_{i}_{j}", "asset_id": f"future_{i}",
                "fact": "材" * 500, "conditions": {"scope": "材" * 100},
                "source_kind": "full_text", "missing_fields": [],
                "title": "材" * 100, "primary_targets": [],
            })
        for j, quote in enumerate(source_windows):
            passages.append({
                "source_id": f"future_{i}_window_{j}",
                "evidence_refs": [f"future_{i}_{k}" for k in range(6)],
                "quote": quote, "source_kind": "full_text", "title": "材" * 100,
            })
    scope = plan.get("constraints", {}).get(question_id, [])
    count = min(439, 6 * len(units) + 6)
    # A conservative bound for the not-yet-written answer, including IDs/conditions
    # and relation descriptions. Actual requests use the same schemas/prompts.
    claims = [
        {
            "claim_id": f"cl_{i:020}",
            "unit_id": f"U{i % len(units) + 1}" if units else None,
            "text": "材" * 360,
            "kind": "fact",
            "evidence_ids": ["E999"] * 4,
        }
        for i in range(count)
    ]
    unit_payload = [
        {
            "unit_id": f"U{i + 1}",
            "state": "answered",
            "claims": claims[i * 6 : i * 6 + 6],
        }
        for i, u in enumerate(units)
    ]
    draft = {
        "question": question.text,
        "requirements": context["wire_units"],
        "facts": facts,
        "sources": passages,
        "documents": context["documents"],
        "scope_constraints": scope,
        "reading_roles": plan.get("reading_roles", []),
    }
    check = {
        "question": question.text,
        "requirements": context["wire_units"],
        "unit_states": [
            {"unit_id": p["unit_id"], "state": p["state"]} for p in unit_payload
        ],
        "claims": claims,
        "explanation_ids": [],
        "conditions": claims[-2:],
        "relations": [
            {"description": "材" * 300, "evidence_ids": ["ev_" + "x" * 26] * 4}
        ]
        * 3,
        "sources": passages,
        "documents": context["documents"],
        "evidence_navigation": [
            {
                k: f.get(k, "unknown")
                for k in (
                    "evidence_id",
                    "source_attribution",
                    "mechanism_slot",
                    "method_subject",
                )
            }
            for f in facts
        ],
        "scope_constraints": [
            {"constraint_id": f"K{i}", "text": c} for i, c in enumerate(scope, 1)
        ],
    }
    from .timeout_v20 import model_timeout, MINIMAX_ATTEMPT_SECONDS
    native = model_timeout(tools) == MINIMAX_ATTEMPT_SECONDS
    options = {"native_result": True} if native else {}
    tokens = model_cost(ANSWER_SYSTEM, draft, Answer, answer_output(len(units)), **options)
    calls = 2
    if tools.repo.protocol().get("execution_revision") == "question_loop_v20":
        from .checking_v20 import batches, ClaimVerdicts, CoverageVerdicts, CLAIM_SYSTEM, COVERAGE_SYSTEM, CLAIM_OUTPUT, COVERAGE_OUTPUT
        groups = batches(check, prospective=True)
        tokens += sum(model_cost(CLAIM_SYSTEM, g, ClaimVerdicts, CLAIM_OUTPUT, **options) for g in groups)
        # Include the maximum diagnostic verdict serialization in coverage input.
        check["claim_checks"] = [{"claim_id": c["claim_id"], "supported": False,
                                  "negative_explicit": False, "relevant": True, "conditions_preserved": True,
                                  "support_mode": "unsupported", "supported_by": ["S999"] * 4,
                                  "unsupported_parts": ["材" * 50] * 6, "added_information": False,
                                  "reason": "材" * 180} for c in claims]
        tokens += model_cost(COVERAGE_SYSTEM, check, CoverageVerdicts, COVERAGE_OUTPUT, **options)
        calls = len(groups) + 2  # One generation, each support batch and one coverage check.
    else:
        tokens += model_cost(CHECK_SYSTEM, check, Verdict, check_output(count, tools=tools), **options)
    # Core's byte estimator plus a small reserve for serialization and condition
    # fields. Follow-up planning is optional and cannot invalidate a saved answer.
    return {"llm_calls": calls, "llm_tokens": max(18000, tokens + 1800)}


def can_read(tools, question_id, sources, asset_ids=()):
    budget = envelope(
        tools, question_id, future_sources=sources, future_assets=asset_ids
    )
    # Extraction contains at most 7000 source chars each; checks repeat at most
    # four source windows plus bounded contexts and output. Keep all in one cap.
    reading_tokens = 5500 + sources * 14000
    return (
        tools.resources.remaining("llm_calls") >= 1 + sources + budget["llm_calls"]
        and tools.resources.remaining("llm_tokens")
        >= reading_tokens + budget["llm_tokens"]
    )
