"""Regression gate — compare eval report against thresholds.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_PKG_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_THRESHOLDS = _PKG_ROOT / "thresholds.yaml"


@dataclass
class GateViolation:
    suite: str
    runner: str
    metric: str
    actual: float
    expected: str
    message: str


@dataclass
class GateResult:
    mode: str
    passed: bool
    violations: list[GateViolation] = field(default_factory=list)
    checks_run: int = 0

    def summary_lines(self) -> list[str]:
        if self.passed:
            return [f"GATE PASSED ({self.mode}): {self.checks_run} checks"]
        lines = [f"GATE FAILED ({self.mode}): {len(self.violations)} violation(s)"]
        for v in self.violations:
            lines.append(f"  [{v.suite}/{v.runner}] {v.metric}: {v.actual} (expected {v.expected}) — {v.message}")
        return lines


def load_thresholds(path: Path | None = None) -> dict[str, Any]:
    p = path or _DEFAULT_THRESHOLDS
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _suite_runners(report: dict[str, Any], suite: str) -> dict[str, Any]:
    if suite in report and isinstance(report[suite], dict):
        return report[suite].get("runners") or {}
    if suite == "discovery" and "runners" in report:
        return report["runners"]
    return {}


def _metric_bounds(rule: dict[str, Any]) -> tuple[str | None, float | None]:
    if "min" in rule:
        return "min", float(rule["min"])
    if "max" in rule:
        return "max", float(rule["max"])
    return None, None


def _check_bounds(
    *,
    suite: str,
    runner: str,
    metric: str,
    actual: float,
    rule: dict[str, Any],
) -> GateViolation | None:
    kind, bound = _metric_bounds(rule)
    if kind == "min" and actual < bound:
        return GateViolation(
            suite=suite,
            runner=runner,
            metric=metric,
            actual=actual,
            expected=f">= {bound}",
            message=f"below minimum {bound}",
        )
    if kind == "max" and actual > bound:
        return GateViolation(
            suite=suite,
            runner=runner,
            metric=metric,
            actual=actual,
            expected=f"<= {bound}",
            message=f"above maximum {bound}",
        )
    return None


def check_gate(
    report: dict[str, Any],
    thresholds: dict[str, Any],
    *,
    mode: str = "mock",
) -> GateResult:
    mode_cfg = (thresholds.get("modes") or {}).get(mode) or {}
    violations: list[GateViolation] = []
    checks = 0

    for suite, suite_rules in mode_cfg.items():
        if suite == "regression":
            continue
        if not isinstance(suite_rules, dict):
            continue
        runners_report = _suite_runners(report, suite)
        for runner_name, rules in suite_rules.items():
            if not isinstance(rules, dict):
                continue
            agg = (runners_report.get(runner_name) or {}).get("aggregate") or {}
            for metric, rule in rules.items():
                if not isinstance(rule, dict):
                    continue
                if metric not in agg:
                    violations.append(
                        GateViolation(
                            suite=suite,
                            runner=runner_name,
                            metric=metric,
                            actual=float("nan"),
                            expected=str(rule),
                            message="metric missing from report",
                        )
                    )
                    continue
                checks += 1
                v = _check_bounds(
                    suite=suite,
                    runner=runner_name,
                    metric=metric,
                    actual=float(agg[metric]),
                    rule=rule,
                )
                if v:
                    violations.append(v)

    regression = mode_cfg.get("regression") or {}
    for pair_name, pair_rules in regression.items():
        if not isinstance(pair_rules, dict):
            continue
        if pair_name.startswith("rag_"):
            continue
        if "_vs_" not in pair_name:
            continue
        left, right = pair_name.split("_vs_", 1)
        # suite prefix e.g. discovery_v2_vs_current_expand
        suite = "discovery"
        for metric, rule in pair_rules.items():
            if not isinstance(rule, dict):
                continue
            left_agg = (_suite_runners(report, suite).get(left) or {}).get("aggregate") or {}
            right_agg = (_suite_runners(report, suite).get(right) or {}).get("aggregate") or {}
            if metric not in left_agg or metric not in right_agg:
                violations.append(
                    GateViolation(
                        suite="regression",
                        runner=pair_name,
                        metric=metric,
                        actual=float("nan"),
                        expected=str(rule),
                        message="metric missing for regression compare",
                    )
                )
                continue
            checks += 1
            delta = float(left_agg[metric]) - float(right_agg[metric])
            min_delta = rule.get("min_delta")
            if min_delta is not None and delta < float(min_delta):
                violations.append(
                    GateViolation(
                        suite="regression",
                        runner=pair_name,
                        metric=metric,
                        actual=round(delta, 4),
                        expected=f"delta >= {min_delta}",
                        message=f"{left} - {right} = {delta:.4f}",
                    )
                )

    rag_regression = regression.get("rag_hybrid_vs_rag_vector") or {}
    if rag_regression:
        rag_runners = _suite_runners(report, "rag")
        for metric, rule in rag_regression.items():
            if not isinstance(rule, dict):
                continue
            left_agg = (rag_runners.get("rag_hybrid") or {}).get("aggregate") or {}
            right_agg = (rag_runners.get("rag_vector") or {}).get("aggregate") or {}
            if metric not in left_agg or metric not in right_agg:
                violations.append(
                    GateViolation(
                        suite="regression",
                        runner="rag_hybrid_vs_rag_vector",
                        metric=metric,
                        actual=float("nan"),
                        expected=str(rule),
                        message="metric missing for rag regression compare",
                    )
                )
                continue
            checks += 1
            delta = float(left_agg[metric]) - float(right_agg[metric])
            min_delta = rule.get("min_delta")
            if min_delta is not None and delta < float(min_delta):
                violations.append(
                    GateViolation(
                        suite="regression",
                        runner="rag_hybrid_vs_rag_vector",
                        metric=metric,
                        actual=round(delta, 4),
                        expected=f"delta >= {min_delta}",
                        message=f"rag_hybrid - rag_vector = {delta:.4f}",
                    )
                )

    return GateResult(mode=mode, passed=not violations, violations=violations, checks_run=checks)
