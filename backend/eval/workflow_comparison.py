"""Independent artifact normalization and annotation scoring; never imported by research tools.

This does not manufacture a quality baseline from model self-assessment. Human claim
annotations and frozen material snapshots must be supplied before a switch decision.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..services.deep_research.research_loop.contracts import digest

EVALUATION_VERSION = "workflow_comparison_v1"
FROZEN_GATES = {
    "claim_support_min": 0.95, "required_unit_coverage_min": 0.85,
    "support_improvement_min": 0.01, "coverage_improvement_min": 0.01,
    "cost_ratio_max": 1.25, "latency_ratio_max": 2.0,
    "hard_errors_max": 0, "repetitions_min": 3,
}


def adapt_run(run: dict, *, group: str, track: str, snapshot_hash: str, config_hash: str) -> dict:
    if group not in ("v8", "free_loop", "bounded_loop") or track not in ("native", "equal_cap"):
        raise ValueError("invalid comparison group or resource track")
    report = (run.get("result") or {}).get("survey_report") or {}
    blocks = []
    if report.get("chapters"):
        blocks = [{"source_id": c["question_id"], "text": c["text"]} for c in report["chapters"]]
    else:
        blocks = [{"source_id": "summary", "text": report.get("summary", "")},
                  *[{"source_id": f"finding:{idx}", "text": text} for idx, text in enumerate(report.get("findings") or [])]]
    return {"evaluation_version": EVALUATION_VERSION, "group": group, "track": track,
            "snapshot_hash": snapshot_hash, "config_hash": config_hash, "question": run["question"],
            "run_id": run.get("id"), "blocks_for_atomic_annotation": blocks,
            "report_hash": digest(report), "annotations": None}


def score_annotations(annotations: dict, required_units: list[str]) -> dict:
    claims = annotations.get("claims") or []
    if not claims or not required_units or not annotations.get("independent_review"):
        raise ValueError("independent atomic claim and required-unit annotations are mandatory")
    facts = [c for c in claims if c["kind"] in ("fact", "inference")]
    if not facts or len({c["claim_id"] for c in facts}) != len(facts):
        raise ValueError("claim IDs must be unique and include factual claims")
    if any(c["support"] not in ("supported", "partial", "unsupported", "unjudgeable") for c in facts):
        raise ValueError("unknown support label")
    answered = set(annotations.get("answered_required_units") or [])
    if answered - set(required_units):
        raise ValueError("unknown required unit; evaluator denominator cannot be narrowed")
    return {"claim_support": sum(c["support"] == "supported" for c in facts) / len(facts),
            "required_unit_coverage": len(answered) / len(required_units),
            "claim_denominator": len(facts), "required_unit_denominator": len(required_units),
            "hard_errors": len(annotations.get("hard_errors") or [])}


def switch_gate(baseline: dict, candidate: dict) -> dict:
    reasons = []
    if candidate.get("evaluation_kind") != "independent_material_comparison":
        reasons.append("缺少独立冻结材料对照")
    for key in ("snapshot_hash", "config_hash", "budget_hash", "required_units_hash"):
        if not baseline.get(key) or baseline.get(key) != candidate.get(key):
            reasons.append(f"对照条件不一致：{key}")
    for key, threshold in (("claim_support", FROZEN_GATES["claim_support_min"]),
                           ("required_unit_coverage", FROZEN_GATES["required_unit_coverage_min"])):
        if candidate.get(key, 0) < threshold:
            reasons.append(f"未达到 {key} 门槛")
        improvement = FROZEN_GATES["support_improvement_min" if key == "claim_support" else "coverage_improvement_min"]
        if candidate.get(key, 0) - baseline.get(key, 0) < improvement:
            reasons.append(f"{key} 尚无足够改善")
    if candidate.get("hard_errors", 1) != 0:
        reasons.append("存在硬性完整性错误")
    if candidate.get("repetitions", 0) < FROZEN_GATES["repetitions_min"]:
        reasons.append("重复次数不足")
    for field, maximum in (("cost", FROZEN_GATES["cost_ratio_max"]), ("latency", FROZEN_GATES["latency_ratio_max"])):
        if not baseline.get(field) or candidate.get(field) is None or candidate[field] / baseline[field] > maximum:
            reasons.append(f"缺少可核验的 {field} 或超出门槛")
    if candidate.get("usage_known") is not True:
        reasons.append("实际用量仍有未知窗口")
    return {"passed": not reasons, "reasons": reasons, "gates_version": EVALUATION_VERSION}


def main():
    parser = argparse.ArgumentParser(description="Normalize recorded run outputs for independent workflow evaluation")
    parser.add_argument("run_json", type=Path)
    parser.add_argument("--group", required=True, choices=("v8", "free_loop", "bounded_loop"))
    parser.add_argument("--track", required=True, choices=("native", "equal_cap"))
    parser.add_argument("--snapshot-hash", required=True)
    parser.add_argument("--config-hash", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = adapt_run(json.loads(args.run_json.read_text()), group=args.group, track=args.track,
                       snapshot_hash=args.snapshot_hash, config_hash=args.config_hash)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
