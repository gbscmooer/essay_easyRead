"""领域探索评测 harness 单测（纯逻辑 + 桩，零网络）。

真评测（打真 LLM + Scopus）不在 CI 跑——见 backend/eval/explore_eval.py 的使用说明。
"""

import asyncio
import json
from pathlib import Path

import pytest

from backend.eval import explore_eval as ee


# ── 题目集与计分 ─────────────────────────────────────────────────────────────

def test_bundled_questions_load_and_validate():
    qs = ee.load_questions()
    assert len(qs) >= 8
    ids = [q["id"] for q in qs]
    assert len(ids) == len(set(ids))
    # 五类验收场景都要覆盖：充分实现/部分/新兴/gap 构造/检索未命中
    categories = {q["category"] for q in qs}
    assert {"established", "emerging", "gap_construct", "retrieval_miss"} <= categories
    assert any(q["expected"] == "no_recall" for q in qs)


def test_load_questions_rejects_bad_expected(tmp_path):
    bad = {"questions": [{"id": "X", "question": "q", "expected": "totally_done"}]}
    p = tmp_path / "q.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        ee.load_questions(p)


def test_score_verdict_nominal_exact_miss_and_no_recall():
    assert ee.score_verdict("done_extensively", "done_extensively") == "exact"
    assert ee.score_verdict("done_extensively", "done_partial") == "miss"
    assert ee.score_verdict("done_extensively", "gap") == "miss"
    assert ee.score_verdict("emerging", None) == "no_verdict"
    # no_recall 题：如实报未命中才算对；基线随便给 verdict 都是 miss
    assert ee.score_verdict("no_recall", "no_recall") == "correct"
    assert ee.score_verdict("no_recall", "gap") == "miss"
    assert ee.score_verdict("no_recall", None) == "miss"
    # 普通题拿到 no_recall（系统侧网络/检索失败导致零召回）→ miss，不得 KeyError
    assert ee.score_verdict("done_extensively", "no_recall") == "miss"
    assert ee.score_verdict("unclear", "gap") == "miss"
    assert ee.score_verdict("emerging", "gap") == "miss"
    assert ee.score_verdict("unclear", "invented_label") == "miss"
    with pytest.raises(ValueError, match="reference verdict"):
        ee.score_verdict("invented_label", "unclear")


@pytest.mark.parametrize("expected", ee.VERDICT_LABELS)
@pytest.mark.parametrize("actual", ee.VERDICT_LABELS)
def test_all_verdict_labels_are_nominal(expected, actual):
    assert ee.score_verdict(expected, actual) == ("exact" if expected == actual else "miss")


def test_median_year_filters_garbage():
    assert ee.median_year([2020, 2024, None, "x", 1899]) == 2022.0
    assert ee.median_year([]) is None


# ── 报告与 Markdown ──────────────────────────────────────────────────────────

def _rec(qid, expected, sys_v, base_v, *, sys_dois=None, base_dois=None):
    return {
        "id": qid, "question": f"q{qid}", "expected": expected, "category": "test", "note": "",
        "system": {
            "verdict": sys_v, "confidence": 0.9, "total": 120, "provider": "scopus",
            "median_year": 2025.0, "evidence_n": 3,
            "evidence_dois": sys_dois or [], "evidence_doi_resolved": len(sys_dois or []),
            "score": ee.score_verdict(expected, sys_v),
        },
        "baseline": {
            "verdict": base_v, "confidence": 0.8, "cited_n": 4, "cited_dois": base_dois or [],
            "doi_resolved": min(1, len(base_dois or [])), "median_cited_year": 2021.0,
            "score": ee.score_verdict(expected, base_v),
        },
    }


def test_build_report_counts_and_rates():
    records = [
        _rec("A", "done_extensively", "done_extensively", "done_partial",
             sys_dois=["10.1/a", "10.1/b"], base_dois=["10.2/x", "10.2/y", "10.2/z"]),
        _rec("B", "emerging", "done_partial", "gap", sys_dois=["10.1/c"], base_dois=["10.2/w"]),
        _rec("C", "no_recall", "no_recall", "gap"),
    ]
    rep = ee.build_report({"date": "t", "questions_version": "v", "model": "m"}, records)
    s, b = rep["summary"]["system"], rep["summary"]["baseline"]
    assert s["exact"] == 1 and s["miss"] == 1 and s["no_recall_correct"] == 1
    assert s["strict_accuracy"] == round(2 / 3, 3)
    assert "within1_accuracy" not in s and "adjacent" not in s
    assert s["classification"]["report"]["macro avg"]["f1-score"] == pytest.approx(2 / 6)
    assert s["classification"]["report"]["emerging"]["recall"] == 0
    assert s["evidence_doi_resolved"] == "3/3"
    # 所有不同名义标签均为 miss，无“相邻”部分正确。
    assert b["exact"] == 0 and b["miss"] == 3
    assert b["strict_accuracy"] == 0
    assert b["classification"]["report"]["macro avg"]["f1-score"] == 0
    # 时效性：系统中位年份 2025 vs 基线 2021
    assert s["median_corpus_year_avg"] == 2025.0 and b["median_cited_year_avg"] == 2021.0


def test_render_markdown_contains_key_columns():
    records = [_rec("A", "done_extensively", "done_extensively", "gap",
                    sys_dois=["10.1/a"], base_dois=["10.2/x"])]
    rep = ee.build_report({"date": "t", "questions_version": "v", "model": "demo"}, records)
    md = ee.render_markdown(rep)
    assert "| 指标 |" in md and "DOI 解析成功" in md
    assert "±1" not in md and "宏平均 Precision / Recall / F1" in md
    assert "证据支持尚未评测" in md
    assert "done_extensively" in md and "检索未命中" not in md  # A 题不是 no_recall
    assert "demo" in md


def test_report_recomputes_scores_and_excludes_skipped_arms():
    record = _rec("A", "emerging", "done_partial", None)
    record["system"]["score"] = "adjacent"  # Historical cached scores are not reused.
    record["baseline"]["skipped"] = True
    report = ee.build_report({}, [record])
    assert report["summary"]["system"]["miss"] == 1
    assert report["summary"]["system"]["strict_accuracy"] == 0
    assert report["summary"]["baseline"]["strict_accuracy"] is None
    assert report["summary"]["baseline"]["classification"]["report"] is None
    assert report["summary"]["baseline"]["n_skipped"] == 1


def test_report_missing_verdict_is_error_but_unknown_doi_check_is_not_false():
    record = _rec("A", "unclear", None, "no_recall", sys_dois=["10.1/a"])
    record["system"]["evidence_doi_resolved"] = None
    report = ee.build_report({}, [record])
    system = report["summary"]["system"]
    assert system["classification"]["accuracy"] == 0
    assert system["classification"]["n_missing_predictions"] == 1
    assert system["evidence_doi_resolve_rate"] is None
    assert system["evidence_doi_unchecked"] == 1
    assert report["summary"]["baseline"]["miss"] == 1


def test_empty_report_has_no_fabricated_accuracy():
    report = ee.build_report({}, [])
    assert report["summary"]["system"]["strict_accuracy"] is None
    assert report["summary"]["baseline"]["strict_accuracy"] is None


def test_invalid_baseline_verdict_does_not_become_correct_unclear(monkeypatch):
    async def fake_llm(*args, **kwargs):
        return {"verdict": "invalid"}

    monkeypatch.setattr("backend.services.deep_research.domain_synthesizer._llm_json", fake_llm)
    result = asyncio.run(ee.ask_baseline("q", {}))
    assert result["verdict"] is None
    assert ee.score_verdict("unclear", result["verdict"]) == "no_verdict"


# ── evaluate 编排（桩掉两臂与 DOI 核验）────────────────────────────────────

def test_evaluate_scoring_and_nonfatal_errors(monkeypatch):
    async def fake_system(question, depth="standard"):
        if "炸" in question:
            raise RuntimeError("boom")
        return {"verdict": "done_extensively", "confidence": 0.9, "total": 100,
                "provider": "stub", "median_year": 2025.0, "evidence_n": 2,
                "evidence_dois": ["10.1/a", "10.1/b"], "partial": False,
                "fallback_decompose": False, "wall_s": 1.0, "error": None}

    async def fake_baseline(question, cfg):
        return {"verdict": "gap", "confidence": 0.7, "explanation": "x", "cited_n": 1,
                "cited_dois": ["10.2/z"], "median_cited_year": 2020.0,
                "wall_s": 0.5, "error": None}

    async def fake_verify(dois):
        # 基线 DOI 解析失败（不等于已证明幻觉），系统证据全过。
        return (len(dois) if all(d.startswith("10.1/") for d in dois) else 0), len(dois)

    monkeypatch.setattr(ee, "run_system_arm", fake_system)
    monkeypatch.setattr(ee, "ask_baseline", fake_baseline)
    monkeypatch.setattr(ee, "verify_dois", fake_verify)
    monkeypatch.setattr(
        "backend.services.ai_config.get_analysis_config",
        lambda: {"api_key": "k", "model": "stub"},
    )

    qs = [
        {"id": "A", "question": "正常题", "expected": "done_extensively", "depth": "quick"},
        {"id": "B", "question": "炸掉的题", "expected": "done_extensively", "depth": "quick"},
    ]
    records = asyncio.run(ee.evaluate(qs))
    assert records[0]["system"]["score"] == "exact"
    assert records[0]["system"]["evidence_doi_resolved"] == 2
    assert records[0]["baseline"]["score"] == "miss"
    assert records[0]["baseline"]["doi_resolved"] == 0
    # 单题系统臂抛异常不中断评测，记为 error + no_verdict 计分
    assert "error" in records[1]["system"]
    assert records[1]["system"]["score"] == "no_verdict"


def test_evaluate_without_llm_key_skips_baseline(monkeypatch):
    monkeypatch.setattr(
        "backend.services.ai_config.get_analysis_config",
        lambda: {"api_key": "", "model": ""},
    )
    async def fake_system(question, depth="standard"):
        return {"verdict": "emerging", "confidence": 0.5, "total": 5, "provider": "stub",
                "median_year": 2025.0, "evidence_n": 0, "evidence_dois": [],
                "partial": False, "fallback_decompose": False, "wall_s": 1.0, "error": None}
    monkeypatch.setattr(ee, "run_system_arm", fake_system)
    records = asyncio.run(ee.evaluate([{"id": "A", "question": "q", "expected": "emerging"}]))
    assert "未配置 LLM Key" in records[0]["baseline"]["error"]
    assert records[0]["baseline"]["score"] == "no_verdict"
    assert records[0]["baseline"]["skipped"] is True
