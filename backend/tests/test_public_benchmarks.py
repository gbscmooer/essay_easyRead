"""公开数据集评测（SYNERGY 筛选、LitSearch 召回）的采样、计分与匹配。"""

import pytest

from backend.eval import public_benchmarks as pb


def test_sample_keeps_all_positives_and_is_seeded():
    rows = [{"record_id": str(i), "label": "1" if i < 3 else "0"} for i in range(50)]
    a = pb.sample_records(rows, "label", 10, seed=1)
    assert sum(r["label"] == "1" for r in a) == 3 and len(a) == 13
    assert a == pb.sample_records(rows, "label", 10, seed=1)
    assert pb.sample_records(rows, "label", None) == rows


def test_score_screening_strict_and_lenient():
    verdicts = {"a": "confirmed", "b": "unknown", "c": "rejected", "d": "confirmed", "e": "rejected"}
    gold = {"a": True, "b": True, "c": True, "d": False, "e": False}
    out = pb.score_screening(verdicts, gold)
    strict, lenient = out["strict"], out["lenient"]
    assert (strict["tp"], strict["fp"], strict["fn"], strict["tn"]) == (1, 1, 2, 1)
    assert strict["recall"] == pytest.approx(0.333, abs=1e-3) and strict["precision"] == 0.5
    # 待定算通过：b 变为命中
    assert (lenient["tp"], lenient["fn"]) == (2, 1) and lenient["recall"] == pytest.approx(0.667, abs=1e-3)
    assert strict["screened_out"] == 0.6
    assert out["verdict_counts"] == {"confirmed": 2, "title_confirmed": 0, "unknown": 1, "rejected": 2}


def test_score_without_positives_has_no_fabricated_recall():
    out = pb.score_screening({"a": "rejected"}, {"a": False})
    assert out["strict"]["recall"] is None and out["strict"]["f1"] is None


def test_match_targets_by_doi_arxiv_title_and_filter_rejection():
    corpus = [
        {"key": "doi:10.18653/v1/x", "doi": "10.18653/V1/X", "title": "Alpha", "relevance_status": "confirmed"},
        {"key": "doi:10.48550/arxiv.2401.00001", "doi": "10.48550/arXiv.2401.00001", "title": "Beta",
         "relevance_status": "unknown"},
        {"key": "title:gamma", "title": "Gamma: A Study!", "relevance_status": "confirmed"},
    ]
    targets = [
        {"corpusid": 1, "title": "alpha", "externalIds": {"DOI": "https://doi.org/10.18653/v1/X"}},
        {"corpusid": 2, "title": "beta", "externalIds": {"ArXiv": "2401.00001v2"}},
        {"corpusid": 3, "title": "Gamma - a study", "externalIds": {}},
        {"corpusid": 4, "title": "Delta", "externalIds": {"ArXiv": "2402.00002"}},
        {"corpusid": 5, "title": "Epsilon", "externalIds": {"DOI": "10.1/eps"}},
    ]
    rows = pb.match_targets(targets, corpus, rejected_keys={"doi:10.1/eps"})
    assert [r["found"] for r in rows] == [True, True, True, False, False]
    assert [r["kept"] for r in rows] == [True, False, True, False, False]
    assert [r["rejected_by_filter"] for r in rows] == [False, False, False, False, True]
    summary = pb.summarize_litsearch([
        {"targets": rows[:3], "corpus_size": 40, "wall_s": 10},
        {"targets": rows[3:], "corpus_size": 20, "wall_s": 30},
        {"targets": [], "error": "TimeoutError"},
    ])
    assert summary["completed"] == 2 and summary["errors"] == 1 and summary["targets"] == 5
    assert summary["target_recall_corpus"] == 0.6 and summary["target_recall_confirmed"] == 0.4
    assert summary["target_rejected_by_filter"] == 1 and summary["query_any_found"] == 0.5
