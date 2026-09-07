"""Offline, judgment-based evaluation; no application services or network required.

Run ``python -m backend.eval.survey_metrics --example`` for an input template,
then ``python -m backend.eval.survey_metrics input.json --output report.json``.
Input has ``retrieval`` queries (id, ranked_ids, qrels: {paper_id: grade}) and
``claims`` (id, text, support, prediction, citations). Each citation has
evidence_id, exists (true/false/null), text, support, and optional prediction.
Support labels are supported/contradicted/insufficient. Missing/null judgments
are unjudged, never synonymous with insufficient or false. Claim-level support
must be judged against the cited evidence jointly; it is not inferred from the
number of citations or from individual citation labels.

Recall uses binary relevance (grade > 0); nDCG uses linear graded gains and
log2 discounts via sklearn.metrics.dcg_score. Duplicate retrieved IDs are
collapsed at their first occurrence. Unretrieved judged papers contribute to
the ideal ranking but never to the actual DCG. Default unjudged policy skips
query metrics if the evaluated prefix contains unjudged papers; ``zero`` is an
explicit closed-world assumption, and ``error`` rejects such inputs.

SurGE (2025 preprint, 2026 revision) motivates reference coverage and separate
support evaluation; ALCE (EMNLP 2023) motivates distinguishing citation quality
from answer correctness. These are adapted diagnostics, not a reproduction of
either benchmark's full protocol or ALCE's joint/necessary-citation metrics.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
from typing import Protocol

from sklearn.metrics import accuracy_score, classification_report, dcg_score, recall_score

SUPPORT_LABELS = ("supported", "contradicted", "insufficient")
MISSING_PREDICTION = "__missing_prediction__"
REFERENCE_CAVEAT = (
    "Expert survey references are an incomplete reference set, not exhaustive "
    "relevance judgments. Uncited papers are not necessarily irrelevant. "
    "Coverage is relative to the supplied judgments and corpus/time snapshot."
)
PROVENANCE = {
    "retrieval": "scikit-learn recall_score and dcg_score; linear gains, log2 discount",
    "classification": "scikit-learn accuracy_score and classification_report; nominal labels",
    "support": "Explicit claim/citation support judgments; no inferred factual correctness",
    "SurGE2025/2026": "https://arxiv.org/abs/2508.15658",
    "ALCE2023": "https://aclanthology.org/2023.emnlp-main.398/",
    "scope": "Adapted reference-coverage and evidence-support diagnostics, not full benchmark reproduction",
}


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def nominal_report(expected: list, predicted: list, labels: tuple[str, ...]) -> dict:
    """Missing gold is excluded; missing/unknown predictions count as errors.

    Macro averages use the fixed declared label set, including absent classes
    with zero_division=0. Missing predictions do not become a semantic class.
    """
    if len(expected) != len(predicted):
        raise ValueError("Expected and predicted labels must have equal lengths")
    for label in expected:
        if label is not None and label not in labels:
            raise ValueError(f"Unknown reference label: {label!r}")
    pairs = [(gold, pred) for gold, pred in zip(expected, predicted) if gold is not None]
    gold = [pair[0] for pair in pairs]
    predictions = [pred if pred in labels else MISSING_PREDICTION for _, pred in pairs]
    return {
        "n_total": len(expected),
        "n_judged": len(pairs),
        "n_unjudged": len(expected) - len(pairs),
        "n_missing_predictions": sum(pred is None or pred == "" for _, pred in pairs),
        "n_invalid_predictions": sum(pred not in labels and pred not in (None, "") for _, pred in pairs),
        "accuracy": float(accuracy_score(gold, predictions)) if pairs else None,
        "labels": list(labels),
        "macro_policy": "fixed label set; absent classes and zero divisions score zero",
        "report": classification_report(
            gold, predictions, labels=list(labels), output_dict=True, zero_division=0,
        ) if pairs else None,
    }


def _identifier(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def retrieval_metrics(ranked_ids: list[str], qrels: dict, k: int, *, unjudged: str = "skip") -> dict:
    """Evaluate one query at K, preserving unknown judgments in the output."""
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("K must be a positive integer")
    if unjudged not in {"skip", "zero", "error"}:
        raise ValueError("unjudged must be skip, zero, or error")
    if not isinstance(ranked_ids, list) or not isinstance(qrels, dict):
        raise ValueError("ranked_ids must be a list and qrels an object")
    for key in ranked_ids:
        _identifier(key, "ranked paper ID")
    judgments = {}
    for key, grade in qrels.items():
        _identifier(key, "qrels paper ID")
        if grade is None:
            continue
        if isinstance(grade, bool) or not isinstance(grade, (int, float)) or not math.isfinite(grade) or grade < 0:
            raise ValueError(f"Relevance grade for {key!r} must be finite and nonnegative, or null")
        judgments[key] = float(grade)
    ranked = list(dict.fromkeys(ranked_ids))
    prefix = ranked[:k]
    unknown = [key for key in prefix if key not in judgments]
    relevant = sum(grade > 0 for grade in judgments.values())
    result = {
        "k": k, "unjudged_policy": unjudged, "n_retrieved": len(ranked),
        "n_at_k": len(prefix), "n_duplicates_removed": len(ranked_ids) - len(ranked),
        "n_judged": len(judgments), "n_relevant": relevant,
        "unjudged_at_k": unknown,
        "judgment_coverage_at_k": _rate(len(prefix) - len(unknown), len(prefix)),
        "recall": None, "ndcg": None,
    }
    if unknown and unjudged == "error":
        raise ValueError(f"Unjudged retrieved IDs at K={k}: {unknown}")
    if not judgments:
        result["status"] = "no_judgments"
    elif unknown and unjudged == "skip":
        result["status"] = "unjudged_retrieval"
    elif not relevant:
        result["status"] = "no_relevant_judgments"
    else:
        universe = list(dict.fromkeys([*judgments, *prefix]))
        retrieved = set(prefix)
        result["recall"] = float(recall_score(
            [judgments.get(key, 0) > 0 for key in universe],
            [key in retrieved for key in universe], zero_division=0,
        ))
        # Score an explicit ranking with zero padding, not tied unseen papers:
        # otherwise sklearn.ndcg_score can credit documents never retrieved.
        width = max(len(prefix), len(judgments), 2)
        actual = [judgments.get(key, 0.0) for key in prefix]
        ideal = sorted(judgments.values(), reverse=True)
        actual.extend([0.0] * (width - len(actual)))
        ideal.extend([0.0] * (width - len(ideal)))
        order = [list(range(width, 0, -1))]
        result["ndcg"] = float(
            dcg_score([actual], order, k=k, ignore_ties=True)
            / dcg_score([ideal], order, k=k, ignore_ties=True)
        )
        result["status"] = "ok"
    return result


class NLIAdapter(Protocol):
    """A predictor supplies estimates only, never reference support labels."""

    def predict(self, evidence: str, claim: str) -> str: ...


class TransformersNLIAdapter:
    """Opt-in pretrained sequence classifier, local/cache-only by default.

    Pass the checkpoint's explicit label mapping, e.g. {"entailment":
    "supported", "contradiction": "contradicted", "neutral": "insufficient"}.
    LABEL_0 ordering is never guessed. Inputs exceeding the model context raise
    an error instead of silently dropping evidence through truncation.
    """

    def __init__(self, model: str, label_map: dict, *, allow_download: bool = False):
        if not isinstance(label_map, dict) or set(label_map.values()) != set(SUPPORT_LABELS):
            raise ValueError("NLI label_map must map model labels to all three support labels")
        from transformers import AutoModelForSequenceClassification, AutoTokenizer, pipeline

        tokenizer = AutoTokenizer.from_pretrained(model, local_files_only=not allow_download)
        classifier = AutoModelForSequenceClassification.from_pretrained(model, local_files_only=not allow_download)
        if set(classifier.config.id2label.values()) != set(label_map):
            raise ValueError("NLI label_map must exactly match the checkpoint's id2label values")
        self.label_map = label_map
        self.model = model
        self.classifier = pipeline("text-classification", model=classifier, tokenizer=tokenizer, device=-1)

    def predict(self, evidence: str, claim: str) -> str:
        output = self.classifier({"text": evidence, "text_pair": claim}, truncation=False)
        return self.label_map[output[0]["label"]]


def _support_summary(items: list[dict]) -> dict:
    labels = [item.get("support") for item in items]
    for label in labels:
        if label is not None and label not in SUPPORT_LABELS:
            raise ValueError(f"Unknown support judgment: {label!r}")
    counts = Counter(label for label in labels if label is not None)
    judged = sum(counts.values())
    predictions = [item for item in items if item.get("prediction") is not None]
    return {
        "n_total": len(items), "n_judged": judged, "n_unjudged": len(items) - judged,
        "judgment_coverage": _rate(judged, len(items)),
        "counts": {label: counts[label] for label in SUPPORT_LABELS},
        "supported_fraction_among_judged": _rate(counts["supported"], judged),
        "prediction_coverage": _rate(len(predictions), len(items)),
        "prediction_classification": nominal_report(
            labels, [item.get("prediction") for item in items], SUPPORT_LABELS,
        ) if predictions else None,
    }


def claim_evidence_metrics(claims: list[dict], *, nli: NLIAdapter | None = None) -> dict:
    """Keep citation existence, explicit support, and predictions independent."""
    if not isinstance(claims, list):
        raise ValueError("claims must be a list")
    rows = deepcopy(claims)
    seen = set()
    citations = []
    for claim in rows:
        if not isinstance(claim, dict):
            raise ValueError("Each claim must be an object")
        claim_id = _identifier(claim.get("id"), "claim id")
        if claim_id in seen:
            raise ValueError(f"Duplicate claim id: {claim_id}")
        seen.add(claim_id)
        refs = claim.get("citations", [])
        if not isinstance(refs, list):
            raise ValueError("citations must be a list")
        evidence_ids = set()
        for ref in refs:
            if not isinstance(ref, dict):
                raise ValueError("Each citation must be an object")
            evidence_id = _identifier(ref.get("evidence_id"), "evidence_id")
            if evidence_id in evidence_ids:
                raise ValueError(f"Duplicate citation {evidence_id!r} in claim {claim_id!r}")
            evidence_ids.add(evidence_id)
            if ref.get("exists") is not None and not isinstance(ref["exists"], bool):
                raise ValueError("Citation exists must be true, false, or null")
            if ref.get("exists") is False and ref.get("support") in ("supported", "contradicted"):
                raise ValueError("A nonexistent citation cannot have a substantive support judgment")
            if nli is not None and ref.get("prediction") is None and ref.get("exists") is not False:
                if isinstance(ref.get("text"), str) and ref["text"].strip() and isinstance(claim.get("text"), str) and claim["text"].strip():
                    prediction = nli.predict(ref["text"], claim["text"])
                    if prediction not in SUPPORT_LABELS:
                        raise ValueError(f"NLI adapter returned unknown label: {prediction!r}")
                    ref["prediction"] = prediction
                    ref["prediction_source"] = "nli_adapter"
            citations.append(ref)
    exists_judged = [ref for ref in citations if ref.get("exists") is not None]
    exists_count = sum(ref["exists"] for ref in exists_judged)
    return {
        "citation_existence": {
            "n_total": len(citations), "n_judged": len(exists_judged),
            "n_unjudged": len(citations) - len(exists_judged),
            "n_exists": exists_count, "n_missing": len(exists_judged) - exists_count,
            "judgment_coverage": _rate(len(exists_judged), len(citations)),
            "exists_fraction_among_judged": _rate(exists_count, len(exists_judged)),
        },
        "citation_support": _support_summary(citations),
        "claim_support": _support_summary(rows),
        "n_claims_without_citations": sum(not row.get("citations") for row in rows),
        "nli": {
            "enabled": nli is not None,
            "model": getattr(nli, "model", None),
            "scope": "Single citation predictions only; accuracy requires independent support judgments",
        },
        "claims": rows,
    }


def evaluate_dataset(data: dict, *, ks: tuple[int, ...] = (5, 10), unjudged: str = "skip", nli: NLIAdapter | None = None) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Dataset must be a JSON object")
    if not ks:
        raise ValueError("At least one K is required")
    # Validate options even when the dataset contains no retrieval queries.
    for k in ks:
        retrieval_metrics([], {}, k, unjudged=unjudged)
    queries = data.get("retrieval", [])
    if not isinstance(queries, list):
        raise ValueError("retrieval must be a list")
    per_query = []
    seen = set()
    for query in queries:
        if not isinstance(query, dict):
            raise ValueError("Each retrieval query must be an object")
        query_id = _identifier(query.get("id"), "query id")
        if query_id in seen:
            raise ValueError(f"Duplicate query id: {query_id}")
        seen.add(query_id)
        if "ranked_ids" not in query:
            raise ValueError(f"Query {query_id!r} is missing ranked_ids")
        per_query.append({"id": query_id, "at_k": {
            str(k): retrieval_metrics(query["ranked_ids"], query.get("qrels", {}), k, unjudged=unjudged)
            for k in dict.fromkeys(ks)
        }})
    aggregate = {}
    for k in dict.fromkeys(ks):
        scores = [query["at_k"][str(k)] for query in per_query]
        evaluated = [score for score in scores if score["status"] == "ok"]
        aggregate[str(k)] = {
            "n_queries": len(scores), "n_evaluated": len(evaluated),
            "n_skipped": len(scores) - len(evaluated),
            "statuses": dict(Counter(score["status"] for score in scores)),
            "recall": sum(score["recall"] for score in evaluated) / len(evaluated) if evaluated else None,
            "ndcg": sum(score["ndcg"] for score in evaluated) / len(evaluated) if evaluated else None,
        }
    return {
        "schema_version": 1, "meta": data.get("meta", {}),
        "provenance": dict(PROVENANCE), "reference_set_caveat": REFERENCE_CAVEAT,
        "retrieval": {"aggregation": "query macro mean over evaluated queries only", "at_k": aggregate, "queries": per_query},
        "evidence": claim_evidence_metrics(data.get("claims", []), nli=nli),
    }


EXAMPLE = {
    "meta": {"reference_set": "Replace with independently reviewed judgments and corpus snapshot"},
    "retrieval": [{"id": "q1", "ranked_ids": ["p2", "p1"], "qrels": {"p1": 2, "p2": 0, "p3": 1}}],
    "claims": [{
        "id": "c1", "text": "The reported experiment used 100 samples.",
        "support": "supported",
        "citations": [{"evidence_id": "p1", "exists": True,
                       "text": "We evaluated the method on 100 samples.", "support": "supported"}],
    }],
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", nargs="?", type=Path, help="Judgment dataset JSON; use - for stdin")
    parser.add_argument("--output", type=Path, help="Output JSON (default stdout)")
    parser.add_argument("--k", type=int, nargs="+", default=[5, 10])
    parser.add_argument("--unjudged", choices=["skip", "zero", "error"], default="skip")
    parser.add_argument("--example", action="store_true", help="Print an input template and exit")
    parser.add_argument("--nli-model", help="Optional pretrained NLI checkpoint or local path")
    parser.add_argument("--nli-label-map", help="JSON object mapping checkpoint labels to support labels")
    parser.add_argument("--allow-model-download", action="store_true", help="Allow opt-in checkpoint downloads")
    args = parser.parse_args(argv)
    if args.example:
        print(json.dumps(EXAMPLE, indent=2))
        return 0
    if args.input is None:
        parser.error("input is required unless --example is used")
    if bool(args.nli_model) != bool(args.nli_label_map):
        parser.error("--nli-model and --nli-label-map must be supplied together")
    if args.allow_model_download and not args.nli_model:
        parser.error("--allow-model-download requires --nli-model")
    try:
        data = json.loads(sys.stdin.read() if str(args.input) == "-" else args.input.read_text(encoding="utf-8"))
        nli = TransformersNLIAdapter(
            args.nli_model, json.loads(args.nli_label_map), allow_download=args.allow_model_download,
        ) if args.nli_model else None
        report = evaluate_dataset(data, ks=tuple(args.k), unjudged=args.unjudged, nli=nli)
        output = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            print(output, end="")
    except (ValueError, OSError, ImportError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
