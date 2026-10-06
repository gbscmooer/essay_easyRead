"""Quote location and semantic support remain separate checks."""
import re
from ....models.research_core import ResearchAsset
from ..document_evidence import DocumentEvidenceReader
from .contracts import AtomicFact, digest

ABSTRACT_FORBIDDEN = {"comparison", "limitation", "counterexample", "condition"}


def project_facts(evidence):
    """Extractive partial delivery: retain complete checked facts, never truncate a claim."""
    blocks, ids, length = [], [], 0
    for row in evidence[:12]:
        block = row["fact"] + "【" + row["evidence_id"] + "】"
        if length + len(block) > 5000:
            break
        blocks.append(block)
        ids.append(row["evidence_id"])
        length += len(block) + 2
    return "材料中已核验的要点：\n\n" + "\n\n".join(blocks), ids


def validated_evidence(run_id: int, question_id: str, asset: ResearchAsset,
                       fact: AtomicFact, verdict: dict, *, reviewer_version="question_loop_v2") -> dict | None:
    payload = asset.inline_payload or {}
    source_kind = payload.get("source_kind", "abstract")
    reader = DocumentEvidenceReader(asset.asset_id, payload.get("text", ""), source_kind=source_kind)
    location = reader.locate_quote(fact.quote)
    if location["location_status"] == "unavailable" or verdict.get("supported") is not True or verdict.get("relevant") is False:
        return None
    if reviewer_version in ("question_loop_v4", "question_loop_v5", "question_loop_v6", "question_loop_v7", "question_loop_v8", "question_loop_v9", "question_loop_v10", "question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20"):
        # Exact technical names in a fact must appear in its supporting span, rather
        # than only somewhere else in the document or in another fact.
        from ..document_evidence import normalize_text
        quote = normalize_text(fact.quote).casefold()
        names = re.findall(r"\b(?:[A-Z]{2,}[A-Za-z0-9_-]*|[A-Z][a-z]+[A-Z][A-Za-z0-9_-]*)\b", fact.fact)
        self_names = set()
        if reviewer_version in ("question_loop_v5", "question_loop_v6", "question_loop_v7", "question_loop_v8", "question_loop_v9", "question_loop_v10", "question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20") and source_kind != "abstract" and re.search(r"\bour\b", quote):
            paper = payload.get("paper", {})
            title = normalize_text(paper.get("title")).casefold()
            introduction = normalize_text(payload.get("text", "")[:7000])
            for name in paper.get("primary_targets", []):
                if (name.casefold() in title or re.search(r"(?:propose|introduce|present|termed|called|named).{0,250}\b" + re.escape(name) + r"\b", introduction, re.I)):
                    self_names.add(name.casefold())
        if any(name.casefold() not in quote and name.casefold() not in self_names for name in names):
            return None
    if source_kind == "abstract" and (fact.evidence_kind in ABSTRACT_FORBIDDEN or fact.conditions):
        return None
    verification = {"quote_located": True, "semantic_check": "model_supported",
                    "status": "abstract_supported" if source_kind == "abstract" else "fulltext_supported",
                    "reviewer_version": reviewer_version, "relevance_checked": verdict.get("relevant") is True,
                    "note": str(verdict.get("reason") or "")[:600]}
    content = {"asset_id": asset.asset_id, "fact": fact.fact, "quote": fact.quote,
               "question_id": question_id, "conditions": fact.conditions}
    return {"evidence_id": "ev_" + digest({"run_id": run_id, **content})[:28],
            "asset_id": asset.asset_id, "doc_content_hash": asset.content_hash,
            "spans": [{"bbox": None, **location, "quote": fact.quote}], "fact": fact.fact,
            "question_ids": [question_id], "evidence_kind": fact.evidence_kind,
            "source_kind": source_kind, "conditions": fact.conditions,
            "missing_fields": fact.missing_fields, "verification": verification}
