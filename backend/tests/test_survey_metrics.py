"""Offline evaluation regression tests; no model downloads or external APIs."""

from copy import deepcopy
import json
import math
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from backend.eval import survey_metrics as sm


def test_graded_retrieval_matches_hand_computed_dcg_and_binary_recall():
    result = sm.retrieval_metrics(["b", "a"], {"a": 3, "b": 1, "c": 2}, 2)
    assert result["recall"] == pytest.approx(2 / 3)
    assert result["ndcg"] == pytest.approx((1 + 3 / math.log2(3)) / (3 + 2 / math.log2(3)))
    assert result["status"] == "ok"


def test_short_rankings_do_not_gain_credit_for_unretrieved_documents():
    result = sm.retrieval_metrics(["a"], {"a": 1, "b": 1}, 10)
    assert result["recall"] == 0.5
    assert result["ndcg"] == pytest.approx(1 / (1 + 1 / math.log2(3)))
    empty = sm.retrieval_metrics([], {"a": 1}, 10)
    assert empty["recall"] == 0 and empty["ndcg"] == 0
    perfect = sm.retrieval_metrics(["a"], {"a": 1}, 1)
    assert perfect["recall"] == 1 and perfect["ndcg"] == 1


def test_duplicate_results_cannot_inflate_recall_or_ndcg():
    result = sm.retrieval_metrics(["a", "a", "b"], {"a": 1, "b": 1, "c": 1}, 2)
    assert result["recall"] == pytest.approx(2 / 3)
    assert result["ndcg"] == 1
    assert result["n_duplicates_removed"] == 1


def test_unjudged_retrieval_is_explicit_and_zero_policy_preserves_rank():
    args = (["unknown", "a"], {"a": 1, "unknown": None}, 2)
    skipped = sm.retrieval_metrics(*args)
    assert skipped["status"] == "unjudged_retrieval"
    assert skipped["recall"] is None and skipped["ndcg"] is None
    assert skipped["judgment_coverage_at_k"] == 0.5
    assert skipped["unjudged_at_k"] == ["unknown"]
    zero = sm.retrieval_metrics(*args, unjudged="zero")
    assert zero["recall"] == 1
    assert zero["ndcg"] == pytest.approx(1 / math.log2(3))
    with pytest.raises(ValueError, match="Unjudged retrieved"):
        sm.retrieval_metrics(*args, unjudged="error")
    assert sm.retrieval_metrics(["a", "unknown"], {"a": 1}, 1)["status"] == "ok"


@pytest.mark.parametrize("qrels,status", [({}, "no_judgments"), ({"a": None}, "no_judgments"), ({"a": 0}, "no_relevant_judgments")])
def test_no_reference_positives_are_undefined(qrels, status):
    result = sm.retrieval_metrics([], qrels, 5)
    assert result["status"] == status
    assert result["recall"] is None and result["ndcg"] is None


@pytest.mark.parametrize("grade", [-1, float("nan"), float("inf"), True, "1"])
def test_invalid_relevance_grades_are_rejected(grade):
    with pytest.raises(ValueError, match="Relevance grade"):
        sm.retrieval_metrics(["a"], {"a": grade}, 5)


@pytest.mark.parametrize("k", [0, -1, True, 1.5])
def test_invalid_cutoffs_are_rejected_even_without_queries(k):
    with pytest.raises(ValueError, match="K must"):
        sm.evaluate_dataset({}, ks=(k,))


def test_query_macro_aggregation_reports_skipped_denominator():
    data = {"retrieval": [
        {"id": "one", "ranked_ids": ["a"], "qrels": {"a": 1}},
        {"id": "two", "ranked_ids": [], "qrels": {"a": 1, "b": 1}},
        {"id": "three", "ranked_ids": ["u"], "qrels": {"a": 1}},
    ]}
    report = sm.evaluate_dataset(data, ks=(1,))
    aggregate = report["retrieval"]["at_k"]["1"]
    assert aggregate["n_evaluated"] == 2 and aggregate["n_skipped"] == 1
    assert aggregate["recall"] == 0.5 and aggregate["ndcg"] == 0.5
    assert "incomplete reference set" in report["reference_set_caveat"]
    assert "SurGE2025/2026" in report["provenance"]
    assert "ALCE2023" in report["provenance"]


def test_nominal_classification_accounts_for_missing_gold_and_predictions():
    report = sm.nominal_report(
        ["supported", "contradicted", "insufficient", None],
        ["supported", None, "supported", "supported"], sm.SUPPORT_LABELS,
    )
    assert report["n_judged"] == 3 and report["n_unjudged"] == 1
    assert report["n_missing_predictions"] == 1
    assert report["accuracy"] == pytest.approx(1 / 3)
    assert report["report"]["macro avg"]["precision"] == pytest.approx(1 / 6)
    assert report["report"]["macro avg"]["recall"] == pytest.approx(1 / 3)
    assert report["report"]["macro avg"]["f1-score"] == pytest.approx(2 / 9)


def test_citation_existence_is_separate_from_support_and_claim_joint_judgment():
    claims = [
        {"id": "c1", "support": "contradicted", "prediction": "supported", "citations": [
            {"evidence_id": "p1", "exists": True, "support": "contradicted"},
            {"evidence_id": "p2", "exists": True, "support": "supported"},
        ]},
        {"id": "c2", "support": "insufficient", "prediction": "insufficient", "citations": [
            {"evidence_id": "p3", "exists": False, "support": "insufficient"},
            {"evidence_id": "p4"},
        ]},
        {"id": "c3"},
    ]
    report = sm.claim_evidence_metrics(claims)
    existence = report["citation_existence"]
    assert existence["exists_fraction_among_judged"] == pytest.approx(2 / 3)
    assert existence["n_unjudged"] == 1
    support = report["citation_support"]
    assert support["supported_fraction_among_judged"] == pytest.approx(1 / 3)
    assert support["n_judged"] == 3 and support["n_unjudged"] == 1
    assert report["claim_support"]["supported_fraction_among_judged"] == 0
    assert report["claim_support"]["prediction_classification"]["accuracy"] == 0.5
    assert report["n_claims_without_citations"] == 1


def test_existing_citations_do_not_fabricate_support_judgments():
    result = sm.claim_evidence_metrics([{"id": "c", "citations": [{"evidence_id": "p", "exists": True}]}])
    assert result["citation_existence"]["exists_fraction_among_judged"] == 1
    for key in ("citation_support", "claim_support"):
        assert result[key]["supported_fraction_among_judged"] is None
        assert result[key]["counts"]["insufficient"] == 0
        assert result[key]["prediction_classification"] is None


def test_nli_predictions_are_not_gold_and_evidence_is_the_premise():
    calls = []

    class StubNLI:
        model = "stub"

        def predict(self, evidence, claim):
            calls.append((evidence, claim))
            return "supported"

    claims = [{"id": "c", "text": "claim", "citations": [
        {"evidence_id": "a", "text": "evidence a", "support": "contradicted"},
        {"evidence_id": "b", "text": "evidence b"},
        {"evidence_id": "c", "text": "not real", "exists": False},
        {"evidence_id": "d", "exists": True},
    ]}]
    before = deepcopy(claims)
    result = sm.claim_evidence_metrics(claims, nli=StubNLI())
    assert calls == [("evidence a", "claim"), ("evidence b", "claim")]
    assert result["citation_support"]["n_judged"] == 1
    assert result["citation_support"]["prediction_classification"]["accuracy"] == 0
    assert result["claim_support"]["n_judged"] == 0
    assert result["nli"]["model"] == "stub"
    assert claims == before


def test_missing_predictions_are_not_silently_dropped_from_accuracy():
    claims = [{"id": "c", "citations": [
        {"evidence_id": "a", "support": "supported", "prediction": "supported"},
        {"evidence_id": "b", "support": "contradicted"},
    ]}]
    result = sm.claim_evidence_metrics(claims)["citation_support"]
    assert result["prediction_coverage"] == 0.5
    assert result["prediction_classification"]["accuracy"] == 0.5
    assert result["prediction_classification"]["n_missing_predictions"] == 1


def test_pretrained_adapter_is_local_by_default_and_mapping_is_explicit(monkeypatch):
    loads = []
    predictions = []

    def load_model(name, **kwargs):
        loads.append((name, kwargs))
        return SimpleNamespace(config=SimpleNamespace(id2label={0: "C", 1: "N", 2: "E"}))

    def classify(inputs, **kwargs):
        predictions.append((inputs, kwargs))
        return [{"label": "E", "score": 0.9}]

    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(
        AutoTokenizer=SimpleNamespace(from_pretrained=load_model),
        AutoModelForSequenceClassification=SimpleNamespace(from_pretrained=load_model),
        pipeline=lambda *args, **kwargs: classify,
    ))
    mapping = {"C": "contradicted", "N": "insufficient", "E": "supported"}
    adapter = sm.TransformersNLIAdapter("local-model", mapping)
    assert all(kwargs["local_files_only"] for _, kwargs in loads)
    assert adapter.predict("premise", "hypothesis") == "supported"
    assert predictions == [({"text": "premise", "text_pair": "hypothesis"}, {"truncation": False})]
    with pytest.raises(ValueError, match="id2label"):
        sm.TransformersNLIAdapter("local-model", {"0": "contradicted", "1": "insufficient", "2": "supported"})


@pytest.mark.parametrize("claims", [
    [{"id": "a"}, {"id": "a"}],
    [{"id": "a", "support": "true"}],
    [{"id": "a", "citations": [{"evidence_id": "b", "exists": "true"}]}],
    [{"id": "a", "citations": [{"evidence_id": "b", "exists": False, "support": "supported"}]}],
    [{"id": "a", "citations": [{"evidence_id": "b"}, {"evidence_id": "b"}]}],
])
def test_invalid_or_ambiguous_evidence_records_fail(claims):
    with pytest.raises(ValueError):
        sm.claim_evidence_metrics(claims)


def test_standalone_cli_json_output_and_no_network(tmp_path):
    root = Path(__file__).resolve().parents[2]
    command = [sys.executable, "-m", "backend.eval.survey_metrics"]
    example = subprocess.run(command + ["--example"], cwd=root, capture_output=True, text=True, check=True)
    data = json.loads(example.stdout)
    data["claims"][0]["citations"][0].pop("support")
    input_path = tmp_path / "judgments.json"
    input_path.write_text(json.dumps(data), encoding="utf-8")
    output_path = tmp_path / "report.json"
    result = subprocess.run(command + [str(input_path), "--output", str(output_path), "--k", "1", "5"],
                            cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    report = json.loads(output_path.read_text())
    assert report["retrieval"]["at_k"]["1"]["recall"] == 0
    assert report["evidence"]["citation_support"]["supported_fraction_among_judged"] is None
    assert report["evidence"]["nli"]["enabled"] is False
    stdin_result = subprocess.run(command + ["-"], input=example.stdout, cwd=root, capture_output=True, text=True)
    assert stdin_result.returncode == 0, stdin_result.stderr
    assert json.loads(stdin_result.stdout)["schema_version"] == 1


def test_cli_invalid_json_and_unjudged_error_are_nonzero(tmp_path, capsys):
    input_path = tmp_path / "bad.json"
    input_path.write_text("{invalid", encoding="utf-8")
    assert sm.main([str(input_path)]) == 2
    assert "Evaluation failed" in capsys.readouterr().err
    input_path.write_text(json.dumps({"retrieval": [{"id": "q", "ranked_ids": ["a"]}]}), encoding="utf-8")
    assert sm.main([str(input_path), "--unjudged", "error"]) == 2
    assert "Unjudged retrieved" in capsys.readouterr().err


def test_empty_dataset_is_unassessed():
    result = sm.evaluate_dataset({})
    assert result["retrieval"]["at_k"]["5"]["recall"] is None
    assert result["evidence"]["citation_existence"]["exists_fraction_among_judged"] is None
    assert result["evidence"]["claim_support"]["supported_fraction_among_judged"] is None
