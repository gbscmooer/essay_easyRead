"""Frozen, bounded follow-ups selected from actual coverage gaps and assets."""
from ....models.research_core import ResearchAsset, ResearchQuestion, ResearchTask
from .contracts import Action, digest

MAX_GAP_ROUNDS = 2


def plans(tools, question_id):
    return [a for a in tools.repo.rows(ResearchAsset) if a.content_kind == "gap_plan" and a.status == "active"
            and (a.inline_payload or {}).get("question_id") == question_id]


def latest_plan(tools, question_id):
    rows = plans(tools, question_id)
    return (rows[-1].inline_payload or {}) if rows else {}


def authorized(tools, action):
    question = next((q for q in tools.repo.rows(ResearchQuestion) if q.question_id == action.question_id), None)
    if question is None or question.status == "supported" or not question.answer_revision_id or not question.gaps:
        return False
    plan = latest_plan(tools, action.question_id)
    if not action.gap_key or action.gap_key != plan.get("gap_key"):
        return False
    if plan.get("execution_policy") == "gap_batch_v2":
        assessments = [t for t in tools.repo.rows(ResearchTask)
                       if t.kind == "assess_answer" and t.subquestion_id == action.question_id]
        # The whole round is bound to one checked answer. New evidence from its
        # first step must not invalidate the other sources before they are read.
        if not assessments or assessments[-1].state != "succeeded" or assessments[-1].payload.get("query") != plan.get("evidence_hash"):
            return False
    else:
        current = digest(sorted(e["evidence_id"] for e in tools.evidence(action.question_id)))
        if current != plan.get("evidence_hash"):
            return False
    return any(Action.model_validate(step).key(tools.repo.protocol()["hash"]) == action.key(tools.repo.protocol()["hash"])
               for step in plan.get("steps", []))


def freeze_followups(tools, action, answer, proposed):
    policy = (tools.plan() or {}).get("feedback_policy")
    if policy not in ("gap_followup_v1", "gap_followup_v2") or answer["status"] == "supported":
        return []
    if len(plans(tools, action.question_id)) >= MAX_GAP_ROUNDS:
        answer["assessment"]["followup_stop_reason"] = "gap_round_limit"
        return []
    documents = {a.asset_id: a for a in tools.ranked_documents(action.question_id)}
    gap_key = digest({"question_id": action.question_id, "evidence_hash": action.query})
    steps = []
    for spec in proposed:
        if spec["tool"] == "read_documents":
            if any(aid not in documents or not (documents[aid].inline_payload or {}).get("text", "").strip()
                   or (documents[aid].inline_payload or {}).get("source_kind") == "abstract" for aid in spec["asset_ids"]):
                continue
        elif spec["tool"] == "fetch_document":
            if ((tools.repo.protocol() or {}).get("execution_revision") not in ("question_loop_v9", "question_loop_v10", "question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20") or
                    len(spec["asset_ids"]) != 1 or spec["asset_ids"][0] not in documents or
                    not tools.repo.run().options.get("search_online", True) or
                    (documents[spec["asset_ids"][0]].inline_payload or {}).get("source_kind") != "abstract"):
                continue
        elif not tools.repo.run().options.get("search_online", True):
            continue
        step = Action(tool=spec["tool"], question_id=action.question_id,
                      asset_id=spec["asset_ids"][0] if spec["tool"] == "fetch_document" else "",
                      asset_ids=[] if spec["tool"] == "fetch_document" else spec["asset_ids"],
                      query=spec["query"], reason=spec["reason"], gap_key=gap_key,
                      focus_queries=[spec["query"]] if spec["tool"] == "read_documents" else [])
        steps.append(step.model_dump())
    payload = {"question_id": action.question_id, "evidence_hash": action.query, "gap_key": gap_key,
               "gaps": answer["gaps"], "steps": steps, "round": len(plans(tools, action.question_id)) + 1}
    if policy == "gap_followup_v2":
        payload["execution_policy"] = "gap_batch_v2"
    answer["assessment"]["followup"] = payload
    return [{"content_kind": "gap_plan", "inline_payload": payload, "content_hash": digest(payload), "parse_status": "parsed"}]


def can_execute(resources, action):
    """Leave synthesis, support and coverage in the original shared envelope."""
    sources = len(action.asset_ids) if action.tool == "read_documents" else 1
    calls = 1 + sources + 3  # Extraction, each source check, final answer checks.
    tokens = 12000 + 6000 + sources * 5000
    return (resources.remaining("llm_calls") >= calls and resources.remaining("llm_tokens") >= tokens
            and resources.remaining("actions") >= 2)
