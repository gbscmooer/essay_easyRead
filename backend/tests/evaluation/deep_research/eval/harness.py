"""Evaluation harness — discovery + RAG suites, aggregate metrics."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import yaml

from .metrics import duplicate_rate, mrr, ndcg_at_k, precision_at_k, recall_at_k, wrong_id_rate
from .network import network_allowed, offline_guard
from .runners import (
    CurrentExpandRunner,
    DiscoveryV2Runner,
    KeywordRunner,
    RagHybridRunner,
    RagVectorRerankRunner,
    RagVectorRunner,
    RunnerResult,
    S2SearchRunner,
)

_PKG_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CONFIG = _PKG_ROOT / "eval_config.yaml"


def _config_root(config: dict[str, Any], config_path: Path) -> Path:
    rel_root = config.get("paths", {}).get("root", ".")
    return (config_path.parent / rel_root).resolve()


def _pkg_path(relative: str, config: dict[str, Any], config_path: Path) -> Path:
    return _config_root(config, config_path) / relative


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or _DEFAULT_CONFIG
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_seeds(config: dict[str, Any], config_path: Path) -> list[dict[str, Any]]:
    rel = config["dataset"]["seeds_path"]
    return load_jsonl(_pkg_path(rel, config, config_path))


def load_corpus(config: dict[str, Any], config_path: Path) -> list[dict[str, Any]]:
    rel = config["dataset"]["corpus_path"]
    return load_jsonl(_pkg_path(rel, config, config_path))


def load_rag_queries(config: dict[str, Any], config_path: Path) -> list[dict[str, Any]]:
    rag_cfg = config.get("rag") or {}
    rel = rag_cfg.get("queries_path", "datasets/rag/queries.jsonl")
    return load_jsonl(_pkg_path(rel, config, config_path))


def build_discovery_runners(config: dict[str, Any], config_path: Path) -> dict[str, Any]:
    runners_cfg = config.get("runners", {})
    root = _config_root(config, config_path)
    out: dict[str, Any] = {}
    if runners_cfg.get("keyword", {}).get("enabled", True):
        out["keyword"] = KeywordRunner()
    if runners_cfg.get("s2_search", {}).get("enabled", True):
        fixture = runners_cfg.get("s2_search", {}).get("fixture", "fixtures/mock/s2_search_responses.json")
        out["s2_search"] = S2SearchRunner(root / fixture)
    if runners_cfg.get("current_expand", {}).get("enabled", True):
        fixture = runners_cfg.get("current_expand", {}).get("fixture", "fixtures/mock/expand_references.json")
        out["current_expand"] = CurrentExpandRunner(root / fixture)
    if runners_cfg.get("discovery_v2", {}).get("enabled", True):
        fixture = runners_cfg.get("discovery_v2", {}).get("fixture", "fixtures/mock/discovery_v2_pools.json")
        out["discovery_v2"] = DiscoveryV2Runner(root / fixture)
    return out


def build_rag_runners(config: dict[str, Any], config_path: Path) -> dict[str, Any]:
    rag_cfg = config.get("rag") or {}
    runners_cfg = rag_cfg.get("runners") or {}
    if not rag_cfg.get("enabled", True):
        return {}
    root = _config_root(config, config_path)
    fixture = runners_cfg.get("fixture", "fixtures/mock/rag_chunk_pools.json")
    fixture_path = root / fixture
    out: dict[str, Any] = {}
    if runners_cfg.get("rag_vector", {}).get("enabled", True):
        out["rag_vector"] = RagVectorRunner(fixture_path)
    if runners_cfg.get("rag_vector_rerank", {}).get("enabled", True):
        out["rag_vector_rerank"] = RagVectorRerankRunner(fixture_path)
    if runners_cfg.get("rag_hybrid", {}).get("enabled", True):
        out["rag_hybrid"] = RagHybridRunner(fixture_path)
    return out


# Back-compat alias
build_runners = build_discovery_runners


def _identity_pairs(
    ranked: list[str],
    identity_gt: dict[str, str],
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for pid in ranked:
        if pid in identity_gt:
            pairs.append((pid, identity_gt[pid]))
    return pairs


def score_result(
    result: RunnerResult,
    item: dict[str, Any],
    *,
    k_values: list[int],
    relevant_key: str = "relevant_ids",
    id_key: str = "seed_id",
) -> dict[str, Any]:
    relevant = set(str(x) for x in item.get(relevant_key, []))
    ranked = result.ranked_ids
    identity_gt = {str(k): str(v) for k, v in (item.get("identity_gt") or {}).items()}
    id_pairs = _identity_pairs(ranked, identity_gt)

    metrics: dict[str, float] = {}
    for k in k_values:
        metrics[f"precision@{k}"] = round(precision_at_k(ranked, relevant, k), 4)
        metrics[f"recall@{k}"] = round(recall_at_k(ranked, relevant, k), 4)
        metrics[f"ndcg@{k}"] = round(ndcg_at_k(ranked, relevant, k), 4)
    metrics["mrr"] = round(mrr(ranked, relevant), 4)
    metrics["wrong_id_rate"] = round(wrong_id_rate(id_pairs), 4) if id_pairs else 0.0
    metrics["duplicate_rate"] = round(duplicate_rate(ranked), 4)

    return {
        id_key: result.seed_id,
        "ranked_ids": ranked,
        relevant_key: sorted(relevant),
        "metrics": metrics,
        "metadata": result.metadata,
    }


def aggregate_seed_metrics(per_seed: list[dict[str, Any]]) -> dict[str, float]:
    if not per_seed:
        return {}
    keys = per_seed[0]["metrics"].keys()
    return {key: round(mean(s["metrics"][key] for s in per_seed), 4) for key in keys}


def _run_suite(
    runners: dict[str, Any],
    items: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    k_values: list[int],
    default_top_k: int,
    runners_cfg: dict[str, Any],
    run_method: str,
    item_key: str,
    relevant_key: str,
    id_key: str,
    corpus: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    suite_report: dict[str, Any] = {}
    for runner_name, runner in runners.items():
        runner_top_k = int(runners_cfg.get(runner_name, {}).get("top_k", default_top_k))
        seed_reports: list[dict[str, Any]] = []
        for item in items:
            if run_method == "run_seed":
                result = runner.run_seed(item, corpus or [], top_k=runner_top_k)
            else:
                result = runner.run_query(item, top_k=runner_top_k)
            seed_reports.append(
                score_result(
                    result,
                    item,
                    k_values=k_values,
                    relevant_key=relevant_key,
                    id_key=id_key,
                )
            )
        suite_report[runner_name] = {
            "top_k": runner_top_k,
            "per_seed": seed_reports,
            "aggregate": aggregate_seed_metrics(seed_reports),
        }
    return suite_report


@offline_guard
def run_evaluation(
    config_path: Path | None = None,
    *,
    mode: str = "mock",
) -> dict[str, Any]:
    path = config_path or _DEFAULT_CONFIG
    config = load_config(path)
    k_values = list(config.get("metrics", {}).get("k_values", [5, 10]))
    default_top_k = int(config.get("metrics", {}).get("default_top_k", max(k_values)))

    seeds = load_seeds(config, path)
    corpus = load_corpus(config, path)
    discovery_runners = build_discovery_runners(config, path)
    rag_runners = build_rag_runners(config, path)
    rag_queries = load_rag_queries(config, path) if rag_runners else []

    discovery_results = _run_suite(
        discovery_runners,
        seeds,
        config,
        k_values=k_values,
        default_top_k=default_top_k,
        runners_cfg=config.get("runners", {}),
        run_method="run_seed",
        item_key="seed",
        relevant_key="relevant_ids",
        id_key="seed_id",
        corpus=corpus,
    )

    rag_results = _run_suite(
        rag_runners,
        rag_queries,
        config,
        k_values=k_values,
        default_top_k=int((config.get("rag") or {}).get("default_top_k", 5)),
        runners_cfg=(config.get("rag") or {}).get("runners", {}),
        run_method="run_query",
        item_key="query",
        relevant_key="relevant_chunk_ids",
        id_key="query_id",
    ) if rag_runners else {}

    report: dict[str, Any] = {
        "schema_version": "deep_research_eval_v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "eval_allow_network": network_allowed(),
        "config_path": str(path),
        "k_values": k_values,
        "discovery": {
            "dataset": {"seeds": len(seeds), "corpus": len(corpus)},
            "runners": discovery_results,
        },
        "rag": {
            "dataset": {"queries": len(rag_queries)},
            "runners": rag_results,
        },
        # v1 back-compat flat runners key
        "runners": discovery_results,
        "dataset": {"seeds": len(seeds), "corpus": len(corpus)},
    }
    return report


def save_report(report: dict[str, Any], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return output_path


def compare_reports(
    baseline: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    """按 suite/runner/aggregate metric 生成可机器读取的前后差异。"""
    suites: dict[str, Any] = {}
    for suite in ("discovery", "rag"):
        base_runners = (baseline.get(suite) or {}).get("runners") or {}
        current_runners = (current.get(suite) or {}).get("runners") or {}
        runner_names = sorted(set(base_runners) | set(current_runners))
        runner_deltas: dict[str, Any] = {}
        for runner_name in runner_names:
            base_metrics = (base_runners.get(runner_name) or {}).get("aggregate") or {}
            current_metrics = (current_runners.get(runner_name) or {}).get("aggregate") or {}
            metric_names = sorted(set(base_metrics) | set(current_metrics))
            runner_deltas[runner_name] = {
                metric: {
                    "baseline": float(base_metrics.get(metric, 0.0)),
                    "current": float(current_metrics.get(metric, 0.0)),
                    "delta": round(
                        float(current_metrics.get(metric, 0.0))
                        - float(base_metrics.get(metric, 0.0)),
                        4,
                    ),
                }
                for metric in metric_names
            }
        suites[suite] = {"runners": runner_deltas}
    return {
        "schema_version": "deep_research_eval_compare_v1",
        "baseline_generated_at": baseline.get("generated_at"),
        "current_generated_at": current.get("generated_at"),
        "suites": suites,
    }


def default_report_path(config: dict[str, Any], config_path: Path, *, mode: str) -> Path:
    rel = config.get("paths", {}).get("reports_dir", "reports")
    name = "mock_gate_report.json" if mode == "mock" else f"{mode}_report.json"
    return _config_root(config, config_path) / rel / name
