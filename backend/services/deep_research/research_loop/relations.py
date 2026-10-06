"""Two-sided relation gates; explicit adoption needs an original cross-reference."""
from .contracts import digest

LABELS = {"shared_task": "共同任务", "method_difference": "方法差异", "uses": "采用", "extends": "扩展", "contradicts": "相矛盾"}
EXPLICIT = {"uses", "extends", "contradicts"}


def checked_relations(proposals, checks, known, papers):
    indexed = {}
    for check in checks:
        indexed.setdefault(check["index"], []).append(check)
    accepted = []
    for index, proposal in enumerate(proposals):
        reviews = indexed.get(index, [])
        review = reviews[0] if len(reviews) == 1 else {}
        if not all(review.get(k) is True for k in ("supported", "relevant", "conditions_preserved")):
            continue
        left, right = proposal["subject_asset_id"], proposal["object_asset_id"]
        sides = [proposal["subject_evidence_ids"], proposal["object_evidence_ids"]]
        if left == right or any(not ids or any(eid not in known or known[eid]["asset_id"] != aid for eid in ids)
                                for aid, ids in zip((left, right), sides)):
            continue
        lp, rp = papers[sides[0][0]], papers[sides[1][0]]
        if lp.get("key") == rp.get("key"):
            continue
        if proposal["predicate"] in EXPLICIT:
            # Matching alone never proves a relation, but without any original pointer
            # two independent descriptions cannot establish adoption or contradiction.
            pointers = []
            for ids, other in ((sides[0], rp), (sides[1], lp)):
                names = [*other.get("primary_targets", []), other.get("title", "")]
                pointers.append(any(name and name.casefold() in span["quote"].casefold()
                    for eid in ids for span in known[eid]["spans"] for name in names))
            if not any(pointers) or review.get("explicit_reference") is not True:
                continue
        ids = list(dict.fromkeys([*sides[0], *sides[1]]))
        accepted.append({"relation_id": "rel_" + digest({"proposal": proposal, "evidence": [known[e] for e in ids]})[:24],
            "subject_type": "paper", "subject": lp.get("title", left), "predicate": proposal["predicate"],
            "object_type": "paper", "object": rp.get("title", right), "scope": proposal["scope"],
            "conditions": {**proposal}, "evidence_ids": ids,
            "verification_state": "abstract_supported" if any(known[e]["source_kind"] == "abstract" for e in ids) else "fulltext_supported",
            "verification_note": review["reason"], "reviewer_version": "question_loop_v7"})
    return accepted


def project_relations(relations):
    return ["- **" + LABELS[r["predicate"]] + "**：" + r["conditions"]["description"] +
        ("（" + r["scope"] + "）" if r["scope"] else "") + "".join("【" + eid + "】" for eid in r["evidence_ids"])
        for r in relations]
