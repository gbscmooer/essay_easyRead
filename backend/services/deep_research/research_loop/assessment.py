"""Program gates for model-supported answers; never claims human verification."""
from .contracts import AnswerDraft
import re


def assess_draft(question: dict, draft: AnswerDraft, evidence: list[dict], verdict: dict) -> dict:
    known = {row["evidence_id"]: row for row in evidence}
    gaps = list(draft.gaps)
    inline_ids = re.findall(r"【(ev_[A-Za-z0-9_]+)】", draft.conclusion)
    invalid = [eid for eid in [*draft.evidence_ids, *inline_ids] if eid not in known or (eid in inline_ids and eid not in draft.evidence_ids)]
    ids = [eid for eid in draft.evidence_ids if eid in known]
    if invalid:
        gaps.append("答案引用未知、隔离或不同问题的证据")
    uncited = [p for p in re.split(r"\n\s*\n", draft.conclusion) if len(p.strip()) > 80
               and not re.search(r"【ev_[A-Za-z0-9_]+】", p)]
    if uncited:
        gaps.append("答案包含未绑定证据的事实段落")
    criteria = question["answer_criteria"]
    unmet = [criterion for criterion in criteria if criterion not in draft.criteria_met
             or ("criteria_met" in verdict and criterion not in verdict["criteria_met"])]
    if unmet:
        gaps.extend(unmet)
    if not ids or not draft.conclusion:
        gaps.append("缺少可支持答案的证据")
    if verdict.get("supported") is not True:
        gaps.append(str(verdict.get("reason") or "结论与依据的语义支持尚未确认"))
    if verdict.get("conditions_preserved") is not True:
        gaps.append("答案未完整保留材料中的适用条件")
    kinds = {known[eid]["evidence_kind"] for eid in ids}
    for kind in question.get("expected_evidence", []):
        # 'fact' accepts any supported atomic evidence; specific requirements remain mandatory.
        if kind != "fact" and kind not in kinds:
            gaps.append(f"仍缺问题要求的 {kind} 类型证据")
    if "comparison" in question.get("expected_evidence", []):
        comparable = [known[eid] for eid in ids if known[eid]["evidence_kind"] == "comparison"
                      and all(known[eid]["conditions"].get(k) for k in ("dataset", "metric", "setting"))
                      and not known[eid]["missing_fields"]]
        if not comparable:
            gaps.append("比较仍缺数据集、指标或实验设置，不能得出性能排序")
    conflicts = []
    for conflict in verdict.get("conflicts", []):
        cited = set(re.findall(r"ev_[A-Za-z0-9_]+", conflict)) & known.keys()
        if len(cited) >= 2:
            conflicts.append(conflict)
        else:
            gaps.append("检查提示（非已证实的材料冲突）：" + conflict)
    conflicts = list(dict.fromkeys(conflicts))
    status = "conflicted" if conflicts else ("supported" if draft.status == "supported" and not gaps else "insufficient")
    # An unsupported generated conclusion is not preserved as a factual answer.
    conclusion = draft.conclusion if verdict.get("supported") is True and verdict.get("conditions_preserved") is True and not invalid and not uncited and ids else ""
    return {"conclusion": conclusion, "kind": draft.kind, "status": status,
            "evidence_ids": ids, "conditions": draft.conditions, "conflicts": conflicts,
            "gaps": list(dict.fromkeys(gaps)),
            "assessment": {"method": "program_gates_and_model_support_v1", "criteria_met": draft.criteria_met,
                           "semantic_check": verdict, "human_reviewed": False}}
