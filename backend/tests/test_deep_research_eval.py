"""Hand-calculated metric tests and offline harness integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.tests.evaluation.deep_research.eval.harness import (
    compare_reports,
    run_evaluation,
    save_report,
)
from backend.tests.evaluation.deep_research.eval.metrics.identity import duplicate_rate, wrong_id_rate
from backend.tests.evaluation.deep_research.eval.metrics.ir import (
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from backend.tests.evaluation.deep_research.eval.network import (
    enforce_network_policy,
    guard_network_call,
    network_allowed,
)
from backend.tests.evaluation.deep_research.eval.runners.current_expand import CurrentExpandRunner

_PKG = Path(__file__).resolve().parent / "evaluation" / "deep_research"


# ---------------------------------------------------------------------------
# IR metrics — values computed by hand (see docstrings in assertions)
# ---------------------------------------------------------------------------

class TestIRMetricsHandCalculated:
    def test_precision_at_3(self):
        # ranked[:3] = A, X, B → 2 relevant of 3
        relevant = {"A", "B", "C"}
        ranked = ["A", "X", "B", "Y", "C"]
        assert precision_at_k(ranked, relevant, 3) == pytest.approx(2 / 3)

    def test_recall_at_3(self):
        # 2 of 3 relevant items found in top-3
        relevant = {"A", "B", "C"}
        ranked = ["A", "X", "B", "Y", "C"]
        assert recall_at_k(ranked, relevant, 3) == pytest.approx(2 / 3)

    def test_mrr_first_hit_at_rank_2(self):
        relevant = {"A", "B"}
        ranked = ["X", "B", "A"]
        assert mrr(ranked, relevant) == pytest.approx(0.5)

    def test_mrr_first_hit_at_rank_1(self):
        relevant = {"A", "B"}
        ranked = ["A", "X", "B"]
        assert mrr(ranked, relevant) == pytest.approx(1.0)

    def test_ndcg_at_3(self):
        # DCG@3 = 1/log2(2) + 0 + 1/log2(4) = 1.5
        # IDCG@3 = 1/log2(2) + 1/log2(3) + 1/log2(4) ≈ 2.1309
        import math

        relevant = {"A", "B", "C"}
        ranked = ["A", "X", "B"]
        dcg = 1 / math.log2(2) + 1 / math.log2(4)
        idcg = 1 / math.log2(2) + 1 / math.log2(3) + 1 / math.log2(4)
        assert ndcg_at_k(ranked, relevant, 3) == pytest.approx(dcg / idcg)

    def test_empty_relevant_returns_zero(self):
        assert recall_at_k(["A"], set(), 5) == 0.0
        assert ndcg_at_k(["A"], set(), 5) == 0.0
        assert mrr(["A"], set()) == 0.0


class TestIdentityMetricsHandCalculated:
    def test_wrong_id_rate_half_mismatch(self):
        pairs = [("oa:correct", "oa:correct"), ("s2:wrong", "oa:canonical")]
        assert wrong_id_rate(pairs) == pytest.approx(0.5)

    def test_duplicate_rate_one_third(self):
        assert duplicate_rate(["A", "A", "B"]) == pytest.approx(1 / 3)

    def test_duplicate_rate_none(self):
        assert duplicate_rate(["A", "B", "C"]) == 0.0


class TestCurrentExpandRanking:
    def test_filters_pdf_and_sorts_by_citations(self):
        refs = [
            {"ext_id": "low", "citation_count": 10, "pdf_url": "http://x"},
            {"ext_id": "high", "citation_count": 100, "pdf_url": "http://y"},
            {"ext_id": "nopdf", "citation_count": 999, "pdf_url": None},
        ]
        ranked = CurrentExpandRunner.rank_references(refs, top_k=2)
        assert ranked == ["high", "low"]


class TestNetworkGuard:
    def test_default_is_offline(self, monkeypatch):
        monkeypatch.delenv("EVAL_ALLOW_NETWORK", raising=False)
        assert network_allowed() is False
        with pytest.raises(RuntimeError, match="EVAL_ALLOW_NETWORK=0"):
            guard_network_call("test call")

    def test_allow_network_when_set(self, monkeypatch):
        monkeypatch.setenv("EVAL_ALLOW_NETWORK", "1")
        assert network_allowed() is True
        guard_network_call("test call")  # should not raise

    def test_socket_is_hard_blocked_in_offline_policy(self, monkeypatch):
        import socket

        monkeypatch.setenv("EVAL_ALLOW_NETWORK", "0")
        original_socket = socket.socket
        with enforce_network_policy():
            with pytest.raises(RuntimeError, match="socket connection"):
                socket.create_connection(("example.com", 443))
        assert socket.socket is original_socket


class TestHarnessOffline:
    def test_run_evaluation_all_runners(self, monkeypatch, tmp_path):
        monkeypatch.setenv("EVAL_ALLOW_NETWORK", "0")
        config = _PKG / "eval_config.yaml"
        report = run_evaluation(config)

        assert report["schema_version"] == "deep_research_eval_v2"
        assert report["eval_allow_network"] is False
        discovery_runners = report["discovery"]["runners"]
        assert set(discovery_runners) == {
            "keyword", "s2_search", "current_expand", "discovery_v2",
        }
        assert report["discovery"]["dataset"]["seeds"] == 5
        assert set(report["rag"]["runners"]) == {
            "rag_vector", "rag_vector_rerank", "rag_hybrid",
        }
        assert report["rag"]["dataset"]["queries"] == 5

        for runner_name, runner_data in discovery_runners.items():
            assert len(runner_data["per_seed"]) == 5
            agg = runner_data["aggregate"]
            assert "precision@5" in agg
            assert "mrr" in agg
            assert "wrong_id_rate" in agg
            assert "duplicate_rate" in agg

        for runner_name, runner_data in report["rag"]["runners"].items():
            assert len(runner_data["per_seed"]) == 5
            agg = runner_data["aggregate"]
            assert "precision@5" in agg
            assert "mrr" in agg

    def test_report_is_reproducible(self, monkeypatch):
        monkeypatch.setenv("EVAL_ALLOW_NETWORK", "0")
        config = _PKG / "eval_config.yaml"
        r1 = run_evaluation(config)
        r2 = run_evaluation(config)
        # Strip timestamp for structural equality
        r1.pop("generated_at")
        r2.pop("generated_at")
        assert r1 == r2

    def test_save_report_roundtrip(self, monkeypatch, tmp_path):
        monkeypatch.setenv("EVAL_ALLOW_NETWORK", "0")
        report = run_evaluation(_PKG / "eval_config.yaml")
        out = tmp_path / "baseline.json"
        save_report(report, out)
        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert loaded["schema_version"] == report["schema_version"]
        assert loaded["runners"]["keyword"]["aggregate"]["mrr"] == report["runners"]["keyword"]["aggregate"]["mrr"]

    def test_compare_reports_emits_metric_deltas(self):
        baseline = {
            "generated_at": "before",
            "discovery": {"runners": {"v1": {"aggregate": {"ndcg@5": 0.4}}}},
            "rag": {"runners": {}},
        }
        current = {
            "generated_at": "after",
            "discovery": {"runners": {"v1": {"aggregate": {"ndcg@5": 0.55}}}},
            "rag": {"runners": {}},
        }
        comparison = compare_reports(baseline, current)
        metric = comparison["suites"]["discovery"]["runners"]["v1"]["ndcg@5"]
        assert metric == {"baseline": 0.4, "current": 0.55, "delta": 0.15}

    def test_frozen_mock_baseline_matches_current_aggregates(self, monkeypatch):
        monkeypatch.setenv("EVAL_ALLOW_NETWORK", "0")
        baseline = json.loads(
            (_PKG / "baselines" / "mock_v1.json").read_text(encoding="utf-8")
        )
        comparison = compare_reports(
            baseline,
            run_evaluation(_PKG / "eval_config.yaml"),
        )
        deltas = [
            metric["delta"]
            for suite in comparison["suites"].values()
            for runner in suite["runners"].values()
            for metric in runner.values()
        ]
        assert deltas
        assert set(deltas) == {0.0}


class TestKeywordRunnerOnMockData:
    def test_resnet_seed_finds_title_overlap(self, monkeypatch):
        monkeypatch.setenv("EVAL_ALLOW_NETWORK", "0")
        from backend.tests.evaluation.deep_research.eval.harness import load_config, load_corpus, load_seeds

        config_path = _PKG / "eval_config.yaml"
        config = load_config(config_path)
        seeds = {s["seed_id"]: s for s in load_seeds(config, config_path)}
        corpus = load_corpus(config, config_path)
        from backend.tests.evaluation.deep_research.eval.runners import KeywordRunner

        result = KeywordRunner().run_seed(seeds["resnet"], corpus, top_k=5)
        assert "oa:he2016resnet" in result.ranked_ids
        assert result.ranked_ids[0] == "oa:he2016resnet"


class TestCLI:
    def test_cli_run_generates_report(self, monkeypatch, tmp_path):
        monkeypatch.setenv("EVAL_ALLOW_NETWORK", "0")
        from backend.tests.evaluation.deep_research.eval.cli import main

        out = tmp_path / "report.json"
        rc = main(["run", "--config", str(_PKG / "eval_config.yaml"), "--output", str(out)])
        assert rc == 0
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert "discovery" in data
        assert "rag" in data

    def test_cli_gate_passes_on_mock(self, monkeypatch, tmp_path):
        monkeypatch.setenv("EVAL_ALLOW_NETWORK", "0")
        from backend.tests.evaluation.deep_research.eval.cli import main

        out = tmp_path / "gate_report.json"
        rc_run = main(["run", "--config", str(_PKG / "eval_config.yaml"), "--output", str(out)])
        assert rc_run == 0
        rc_gate = main([
            "gate",
            "--config",
            str(_PKG / "eval_config.yaml"),
            "--report",
            str(out),
            "--mode",
            "mock",
        ])
        assert rc_gate == 0

    def test_cli_compare_writes_diff(self, tmp_path):
        from backend.tests.evaluation.deep_research.eval.cli import main

        baseline = tmp_path / "baseline.json"
        current = tmp_path / "current.json"
        output = tmp_path / "comparison.json"
        baseline.write_text(
            json.dumps({
                "discovery": {"runners": {"v1": {"aggregate": {"precision@5": 0.4}}}},
                "rag": {"runners": {}},
            }),
            encoding="utf-8",
        )
        current.write_text(
            json.dumps({
                "discovery": {"runners": {"v1": {"aggregate": {"precision@5": 0.5}}}},
                "rag": {"runners": {}},
            }),
            encoding="utf-8",
        )
        assert main([
            "compare",
            str(baseline),
            str(current),
            "--output",
            str(output),
        ]) == 0
        data = json.loads(output.read_text(encoding="utf-8"))
        assert data["suites"]["discovery"]["runners"]["v1"]["precision@5"]["delta"] == 0.1


class TestDiscoveryV2Runner:
    def test_beats_current_expand_on_resnet_recall(self, monkeypatch):
        monkeypatch.setenv("EVAL_ALLOW_NETWORK", "0")
        from backend.tests.evaluation.deep_research.eval.harness import (
            load_config,
            load_corpus,
            load_seeds,
            score_result,
        )
        from backend.tests.evaluation.deep_research.eval.runners import (
            CurrentExpandRunner,
            DiscoveryV2Runner,
        )

        config_path = _PKG / "eval_config.yaml"
        config = load_config(config_path)
        seeds = {s["seed_id"]: s for s in load_seeds(config, config_path)}
        corpus = load_corpus(config, config_path)
        seed = seeds["resnet"]
        k_values = [5, 10]

        v1 = score_result(
            CurrentExpandRunner(_PKG / "fixtures/mock/expand_references.json").run_seed(seed, corpus, top_k=5),
            seed,
            k_values=k_values,
        )
        v2 = score_result(
            DiscoveryV2Runner(_PKG / "fixtures/mock/discovery_v2_pools.json").run_seed(seed, corpus, top_k=10),
            seed,
            k_values=k_values,
        )
        assert v2["metrics"]["recall@5"] > v1["metrics"]["recall@5"]
        assert "oa:he2016resnet" in v2["ranked_ids"]


class TestGateModule:
    def test_gate_detects_regression(self):
        from backend.tests.evaluation.deep_research.eval.gate import check_gate, load_thresholds

        thresholds = load_thresholds(_PKG / "thresholds.yaml")
        bad_report = {
            "discovery": {
                "runners": {
                    "discovery_v2": {"aggregate": {"precision@5": 0.1, "recall@5": 0.1, "mrr": 0.1}},
                    "current_expand": {"aggregate": {"precision@5": 0.5, "recall@5": 0.8, "mrr": 1.0}},
                }
            },
            "rag": {
                "runners": {
                    "rag_vector": {"aggregate": {"precision@5": 0.5, "recall@5": 0.8, "mrr": 0.9}},
                    "rag_hybrid": {"aggregate": {"precision@5": 0.2, "recall@5": 0.2, "mrr": 0.2}},
                }
            },
        }
        result = check_gate(bad_report, thresholds, mode="mock")
        assert not result.passed
        assert result.violations
