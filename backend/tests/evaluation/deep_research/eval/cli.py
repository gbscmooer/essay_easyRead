"""CLI for deep research offline evaluation (run + gate)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .gate import check_gate, load_thresholds
from .harness import (
    _PKG_ROOT,
    compare_reports,
    default_report_path,
    load_config,
    run_evaluation,
    save_report,
)


def _print_suite_summary(report: dict[str, Any], suite: str) -> None:
    suite_data = report.get(suite) or {}
    runners = suite_data.get("runners") or report.get("runners") or {}
    for runner_name, runner_data in runners.items():
        agg = runner_data.get("aggregate") or {}
        p5 = agg.get("precision@5", 0.0)
        r5 = agg.get("recall@5", 0.0)
        print(f"  [{suite}] {runner_name}: P@5={p5:.3f} R@5={r5:.3f} MRR={agg.get('mrr', 0.0):.3f}")


def cmd_run(args: argparse.Namespace) -> int:
    report = run_evaluation(args.config, mode=args.mode)
    output = args.output or default_report_path(load_config(args.config), args.config, mode=args.mode)
    save_report(report, output)
    print(f"Wrote eval report → {output}")
    _print_suite_summary(report, "discovery")
    if report.get("rag", {}).get("runners"):
        _print_suite_summary(report, "rag")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    report_path = args.report or default_report_path(config, args.config, mode=args.mode)
    if args.report is None and not report_path.exists():
        report = run_evaluation(args.config, mode=args.mode)
        save_report(report, report_path)
        print(f"Generated report for gate → {report_path}")
    else:
        with open(report_path, encoding="utf-8") as f:
            report = json.load(f)

    thresholds_path = args.thresholds or (_PKG_ROOT / "thresholds.yaml")
    thresholds = load_thresholds(thresholds_path)
    result = check_gate(report, thresholds, mode=args.mode)
    for line in result.summary_lines():
        print(line)
    return 0 if result.passed else 1


def cmd_compare(args: argparse.Namespace) -> int:
    with open(args.baseline, encoding="utf-8") as f:
        baseline = json.load(f)
    with open(args.current, encoding="utf-8") as f:
        current = json.load(f)
    comparison = compare_reports(baseline, current)
    if args.output:
        save_report(comparison, args.output)
        print(f"Wrote eval comparison → {args.output}")
    for suite, suite_data in comparison["suites"].items():
        for runner, metrics in suite_data["runners"].items():
            headline = metrics.get("ndcg@5") or metrics.get("precision@5") or {}
            print(f"  [{suite}] {runner}: Δ={headline.get('delta', 0.0):+.4f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        type=Path,
        default=_PKG_ROOT / "eval_config.yaml",
        help="Path to eval_config.yaml",
    )
    common.add_argument(
        "--mode",
        choices=["mock", "live"],
        default="mock",
        help="Evaluation mode (CI uses mock with EVAL_ALLOW_NETWORK=0)",
    )

    parser = argparse.ArgumentParser(description="Deep research offline evaluation", parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run discovery + RAG mock evaluation", parents=[common])
    run_p.add_argument("--output", type=Path, default=None, help="Report JSON path")
    run_p.set_defaults(func=cmd_run)

    gate_p = sub.add_parser("gate", help="Check report against thresholds.yaml", parents=[common])
    gate_p.add_argument("--report", type=Path, default=None, help="Existing report JSON")
    gate_p.add_argument(
        "--thresholds",
        type=Path,
        default=None,
        help="thresholds.yaml path (default: package thresholds.yaml)",
    )
    gate_p.set_defaults(func=cmd_gate)

    compare_p = sub.add_parser("compare", help="Compare a frozen baseline report with a current report")
    compare_p.add_argument("baseline", type=Path, help="Frozen baseline JSON")
    compare_p.add_argument("current", type=Path, help="Current report JSON")
    compare_p.add_argument("--output", type=Path, default=None, help="Comparison JSON path")
    compare_p.set_defaults(func=cmd_compare)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
