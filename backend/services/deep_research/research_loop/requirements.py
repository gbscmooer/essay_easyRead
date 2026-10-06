"""Freeze obligations from user criteria, independently of generated answer omissions."""
import re

from .contracts import digest

GENERIC_POLICY = "回答原问题全部限定条件；每项事实提供材料依据；证据不足明确说明"
UNCERTAIN = re.compile(r"未知|未(?:能|获|明确|覆盖|确认|说明)|尚不明确|不明确|证据不足|无法(?:确定|确认|判断)|(?:无|没有|缺乏)(?:进一步)?[^。；\n]{0,16}证据|未报道|未报告|not (?:known|established|reported)|unknown", re.I)


def requirement_units(question, plan):
    names = list(dict.fromkeys(t["name"] for t in plan.get("targets", []) if t["question_id"] == question.question_id))
    criteria = [(i, c) for i, c in enumerate(question.answer_criteria) if c != GENERIC_POLICY]
    if not criteria:
        # A protocol without decomposed criteria still owes the entire user question.
        criteria = [(question.answer_criteria.index(GENERIC_POLICY) if GENERIC_POLICY in question.answer_criteria else 0, question.text)]
    units = []
    for index, criterion in criteria:
        explicit = [n for n in names if n.casefold() in criterion.casefold()]
        for subject in explicit or names or [""]:
            key = {"question_id": question.question_id, "criterion_index": index, "requirement": criterion, "subject": subject}
            units.append({"unit_id": "req_" + digest(key)[:20], **key})
    return units


def checked_units(units, generated, checks, known, papers):
    answers, verdicts = {}, {}
    for row in generated:
        answers.setdefault(row["unit_id"], []).append(row)
    for row in checks:
        verdicts.setdefault(row["unit_id"], []).append(row)
    ledger = []
    for unit in units:
        candidates, reviews = answers.get(unit["unit_id"], []), verdicts.get(unit["unit_id"], [])
        answer = candidates[0] if len(candidates) == 1 else {}
        review = reviews[0] if len(reviews) == 1 else {}
        ids = list(dict.fromkeys(answer.get("evidence_ids", [])))
        valid_ids = bool(ids) and all(eid in known for eid in ids)
        subject = unit["subject"].casefold()
        # A named method cannot be answered solely by another method's original paper.
        has_subject = not subject or any(subject in [n.casefold() for n in papers[eid].get("primary_targets", [])]
            or (not papers[eid].get("primary_targets") and (subject in papers[eid].get("title", "").casefold()
                or subject in known[eid]["fact"].casefold()))
            for eid in ids if eid in known)
        resolved = (answer.get("state") == "answered" and valid_ids and has_subject and
                    not UNCERTAIN.search(answer.get("text", "")) and
                    all(review.get(k) is True for k in ("supported", "complete", "relevant")))
        ledger.append({**unit, "resolved": resolved, "answer": answer.get("text", "") if resolved else "", "kind": answer.get("kind", "fact"),
            "evidence_ids": ids if resolved else [], "check": review,
            "gap": "" if resolved else ((unit["subject"] + "：") if unit["subject"] else "") + unit["requirement"]})
    return ledger
