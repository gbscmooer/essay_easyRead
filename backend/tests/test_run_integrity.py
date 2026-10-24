"""检索与分析完整性汇总（任务书：检索失败、资料不足、分析不完整须提示）。"""

from backend.services.deep_research.integrity import build_integrity, render_integrity_markdown


def _result(**overrides):
    base = {
        "decompose": {"fallback": False},
        "metrics": {"total": 40},
        "papers": [],
        "sources": {"preflight": [{"name": "openalex", "status": "ok"}],
                    "recall": {"stop_reason": "target_reached", "target": 40}},
        "synthesis": {"verdict": {"status": "done_partial"}, "partial": False, "stats": {}},
        "report_extract": {"diagnostics": {"extract": {"failures": []}}},
        "survey_report": {"coverage": {"selected": 10, "examined": 10, "failed": 0,
                                       "full_text": 6, "abstract_only": 4},
                          "quality_status": "unchecked"},
    }
    base.update(overrides)
    return base


def test_complete_run_has_no_items():
    out = build_integrity(_result())
    assert out == {"level": "complete", "label": "流程完整", "items": []}
    assert "各阶段均已完成" in "\n".join(render_integrity_markdown(out))


def test_source_failure_and_decompose_fallback_are_partial():
    out = build_integrity(_result(
        decompose={"fallback": True},
        sources={"preflight": [{"name": "arxiv", "status": "dead", "detail": "HTTP 429"},
                               {"name": "scopus", "status": "unconfigured"}],
                 "recall": {"stop_reason": "planner_error"}}))
    assert out["level"] == "partial"
    kinds = [item["kind"] for item in out["items"]]
    assert kinds == ["source", "decompose", "recall"]
    assert "arxiv" in out["items"][0]["message"] and "HTTP 429" in out["items"][0]["message"]


def test_narrow_topic_recall_stop_is_listed_without_downgrade():
    out = build_integrity(_result(sources={"preflight": [], "recall": {
        "stop_reason": "round_budget", "target": 200, "effective_rounds": 3}}))
    assert out["level"] == "complete" and "未达到目标 200 篇" in out["items"][0]["message"]


def test_extraction_timeouts_report_real_cause():
    failures = [{"code": "ValueError", "detail": "paper extraction loop incomplete: model_error:TimeoutError"}] * 11
    failures.append({"code": "CONTEXT_SHRUNK_RETRY", "detail": "ValueError: 16077→8000 chars"})
    out = build_integrity(_result(
        report_extract={"diagnostics": {"extract": {"failures": failures}}},
        survey_report={"coverage": {"selected": 13, "examined": 2, "failed": 11, "full_text": 0,
                                    "abstract_only": 2}, "quality_status": "needs_review"}))
    assert out["level"] == "partial"
    assert "模型调用超时×11" in out["items"][0]["message"]
    assert out["items"][1]["kind"] == "report"


def test_all_extraction_failed_is_insufficient():
    out = build_integrity(_result(survey_report={"coverage": {
        "selected": 8, "examined": 0, "failed": 8, "full_text": 0, "abstract_only": 0}}))
    assert out["level"] == "insufficient" and "只能依据标题与摘要" in out["items"][0]["message"]


def test_zero_recall_and_concept_miss_are_retrieval_miss():
    zero = build_integrity(_result(metrics={"total": 0}, synthesis={}, survey_report={}))
    assert zero["level"] == "retrieval_miss" and zero["label"] == "检索未命中"
    # 未形成判定不再重复报“资料不足”
    assert [item["kind"] for item in zero["items"]] == ["recall"]
    miss = build_integrity(_result(synthesis={"verdict": {"status": "unclear"}, "stats": {
        "concept_coverage": {"missing": ["独角兽"], "papers": 6}}}))
    assert miss["level"] == "retrieval_miss" and "独角兽" in miss["items"][0]["message"]


def test_needs_review_names_uncited_statement():
    out = build_integrity(_result(survey_report={
        "coverage": {"selected": 4, "examined": 4, "failed": 0}, "quality_status": "needs_review",
        "delivery": {"error_code": "CITATION_MISSING"},
        "narrative": {"findings": [{"text": "已引用", "citation_status": "ok"},
                                   {"text": "语料统计显示该领域高度活跃", "citation_status": "missing"}]}}))
    assert out["items"][0]["message"] == "终稿有 1 条陈述缺少证据引用（如「语料统计显示该领域高度活跃」），需人工复核。"
    unknown = build_integrity(_result(survey_report={
        "quality_status": "needs_review", "delivery": {"error_code": "UNKNOWN_EVIDENCE_REFERENCE"}}))
    assert unknown["items"][0]["message"].startswith("终稿引用了证据包之外的编号")


def test_derived_concept_miss_is_listed_without_downgrade():
    out = build_integrity(_result(synthesis={"verdict": {"status": "done_partial"}, "stats": {
        "concept_coverage": {"missing": [], "missing_derived": ["神经符号证明智能体"], "papers": 39}}}))
    assert out["level"] == "complete" and "神经符号证明智能体" in out["items"][0]["message"]


def test_verdict_guard_and_report_error_are_listed():
    out = build_integrity(_result(
        synthesis={"verdict": {"status": "emerging"}, "partial": True, "stats": {"verdict_guards": [
            {"rule": "maturity_early_evidence", "from": "done_extensively", "to": "emerging",
             "early_evidence": 0, "cutoff_year": 2024}]}},
        report_error="报告未完成（TimeoutError），可从已有材料重新生成。"))
    assert out["level"] == "partial"
    assert [item["kind"] for item in out["items"]] == ["synthesize", "verdict", "report"]
