import asyncio
import json

import pytest

from backend.services.deep_research import survey_report as sr
from backend.services.deep_research.survey_report import verify_extraction as verify_rows


PAPER = {"key": "doi:10.1/a", "title": "A study", "year": 2024,
         "doi": "10.1/a", "abstract": "ARES evaluates answer faithfulness using Accuracy. QA on HotpotQA uses RAG."}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    async def select(papers, *args, **kwargs):
        return papers[:kwargs["limit"]], {"warnings": []}
    monkeypatch.setattr(sr, "retrieve_materials", select)
    async def verify(rows, *args):
        return rows, 0
    monkeypatch.setattr(sr, "verify_extraction", verify)


def extracted():
    return {
        "frameworks": [{"name": "ARES", "kind": "tool", "target": "answer", "aspects": ["faithfulness"], "metrics": ["Accuracy"], "quote": "ARES evaluates answer faithfulness using Accuracy."}],
        "metrics": [{"aspect": "faithfulness", "metric": "Accuracy", "quote": "ARES evaluates answer faithfulness using Accuracy."}],
        "tasks": [{"task": "QA", "dataset": "HotpotQA", "method": "RAG", "quote": "QA on HotpotQA uses RAG."}],
    }


def test_quotes_and_required_names_are_checked():
    data = extracted()
    data["metrics"].append({"aspect": "other", "metric": "Invented", "quote": PAPER["abstract"]})
    data["tasks"].append({"task": "QA", "dataset": "Imaginary", "quote": "This sentence is not in the source."})
    result, rejected = sr.normalize_extraction(data, PAPER, PAPER["abstract"], "abstract")
    assert rejected == 2
    assert len(result["metrics"]) == len(result["tasks"]) == 1
    assert result["metrics"][0]["evidence"][0]["paper_key"] == PAPER["key"]


def test_matrix_counts_unique_papers_and_does_not_create_unobserved_pairs():
    rows = [
        {"aspect": "Faithfulness", "metric": "Accuracy", "evidence": [{"paper_key": "a"}, {"paper_key": "a"}]},
        {"aspect": "faithfulness", "metric": "accuracy", "evidence": [{"paper_key": "b"}]},
        {"aspect": "Relevance", "metric": "Recall", "evidence": [{"paper_key": "a"}]},
    ]
    matrix = sr.coverage_matrix(rows, "aspect", "metric")
    assert len(matrix["rows"]) == len(matrix["columns"]) == 2
    assert len(matrix["cells"]) == 2
    assert sorted(c["count"] for c in matrix["cells"]) == [1, 2]
    assert matrix["missing"] == "not_reported_in_examined_materials"


def test_methods_and_framework_metrics_require_their_own_quoted_evidence():
    data = extracted()
    data["frameworks"][0]["metrics"] = ["NDCG"]
    data["tasks"][0]["method"] = "UnmentionedMethod"
    result, rejected = sr.normalize_extraction(data, PAPER, PAPER["abstract"], "abstract")
    assert result["frameworks"][0]["metrics"] == []
    assert result["tasks"][0]["method"] == ""
    assert rejected == 0


def test_no_model_no_fabricated_tables():
    report = asyncio.run(sr.build_survey_report("question", [PAPER], {}, None, {}))
    assert report["coverage"]["examined"] == 0
    assert not any(report["tables"].values())
    assert report["status"] == "unavailable"
    assert "Downstream Tasks and Datasets" in report["markdown"]


def test_no_results_still_produces_search_audit_report():
    report = asyncio.run(sr.build_survey_report(
        "有没有某个 idea",
        [],
        {"by_year": {}, "year_span": []},
        None,
        {"api_key": "test", "model": "fixture"},
        search_context={
            "decompose": {"en_queries": ["novel idea retrieval"]},
            "provider": "openalex",
            "preflight": [{"name": "openalex", "role": "primary", "status": "ok", "detail": "正常"}],
            "recall": {"channels": [
                {"query": "novel idea retrieval", "order": "relevance", "limit": 40, "hits": 0},
                {"query": '"novel idea retrieval"', "order": "cited", "limit": 20, "hits": 0},
            ]},
            "agent_loops": {"decompose": {
                "stop_reason": "validator_passed",
                "preview_rounds": [{"round": 1, "queries": ["novel idea retrieval"],
                                    "provider": "openalex", "hits": 0, "audit": {"channels": []}}],
                "trace": [],
            }},
        },
    ))
    assert report["status"] == "no_results"
    assert report["coverage"]["corpus"] == 0
    assert report["references"] == []
    assert len(report["search_summary"]["attempts"]) == 3
    assert "不能证明全领域不存在" in report["search_summary"]["interpretation"]
    assert "已执行的搜索" in report["markdown"]
    assert "novel idea retrieval" in report["markdown"]
    assert "建议补搜方向" in report["markdown"]
    assert "已实现" not in " ".join(report["findings"])
    assert "已执行的搜索" in sr.render_html(report)


def test_structured_report_and_safe_html_export(monkeypatch):
    async def llm(*args, **kwargs):
        return extracted()
    monkeypatch.setattr(sr, "_llm_json", llm)
    report = asyncio.run(sr.build_survey_report("<script>alert(1)</script>", [PAPER],
                         {"by_year": {2024: 1}}, {"landscape_summary": "Summary", "hotspots": ["Finding"]}, {"api_key": "test", "model": "fixture"}))
    assert report["coverage"]["examined"] == 1
    assert report["extractions"][0]["agent_loop"]["status"] == "completed"
    assert report["extractions"][0]["agent_loop"]["iterations"] == 3
    assert report["coverage"]["abstract_only"] == 1
    assert report["matrices"]["task_dataset"]["cells"][0]["count"] == 1
    html = sr.render_html(report)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "https://doi.org/10.1/a" in html
    assert "评估维度 × 指标" in html
    assert "QA on HotpotQA" in report["markdown"]
    assert "Finding" in html and "Finding" in report["markdown"]
    assert "材料发表年份分布" in html
    assert "test" not in str(report["method"])


def test_one_failed_extraction_preserves_other_material(monkeypatch):
    async def llm(cfg, system, user, **kwargs):
        if '"paper_title": "broken"' in user:
            raise RuntimeError("test")
        return extracted()
    monkeypatch.setattr(sr, "_llm_json", llm)
    report = asyncio.run(sr.build_survey_report("q", [PAPER, {**PAPER, "key": "b", "title": "broken"}], {}, None, {"api_key": "x"}))
    assert report["status"] == "partial"
    assert report["coverage"]["failed"] == 1
    assert len(report["tables"]["frameworks"]) == 1


def test_fulltext_excerpt_selects_late_evaluation_passage():
    paper = {**PAPER, "source_text": "irrelevant background " * 1200 + "\nEvaluation metrics downstream dataset Accuracy HotpotQA " * 80}
    text, kind = sr._source_excerpt(paper, "evaluation")
    assert kind == "full_text_excerpt"
    assert "HotpotQA" in text
    assert len(text) < 13000


def test_pdf_layout_normalization_retains_verifiable_quotes():
    source = "ARES evaluates faith-\nfulness using Accuracy and ﬁdelity."
    data = {"metrics": [{"aspect": "faithfulness", "metric": "Accuracy",
                         "quote": "ARES evaluates faithfulness using Accuracy and fidelity."}]}
    rows, rejected = sr.normalize_extraction(data, PAPER, source, "full_text_excerpt")
    assert rejected == 0
    assert len(rows["metrics"]) == 1


def test_semantic_check_discards_shared_mention_without_supported_relation(monkeypatch):
    async def judge(cfg, system, payload, **kwargs):
        assert json.loads(payload)["source"] == PAPER["abstract"]
        assert json.loads(payload)["candidates"][0]["row"]["evidence"]
        return {"supported_ids": ["metrics:0", "fake:0"], "rejected_ids": ["frameworks:0"]}
    monkeypatch.setattr(sr, "_llm_json", judge)
    rows, _ = sr.normalize_extraction(extracted(), PAPER, PAPER["abstract"], "abstract")
    result, rejected = asyncio.run(verify_rows(rows, PAPER["abstract"], {"api_key": "test"}))
    assert result["frameworks"] == []
    assert len(result["metrics"]) == 1
    assert rejected == 2


def test_failed_pdf_download_falls_back_to_abstract(monkeypatch):
    async def download(*args, **kwargs):
        raise TimeoutError("unavailable")
    async def llm(*args, **kwargs):
        return extracted()
    monkeypatch.setattr("backend.services.deep_research.pdf_downloader.download_pdf", download)
    monkeypatch.setattr(sr, "_llm_json", llm)
    paper = {**PAPER, "pdf_url": "https://example.org/paper.pdf"}
    report = asyncio.run(sr.build_survey_report("q", [paper], {}, None, {"api_key": "test"}, fetch_full_text=True))
    assert report["coverage"]["abstract_only"] == 1
    assert report["coverage"]["failed"] == 0
    assert paper["full_text_status"] == "unavailable"


def test_search_summary_projects_engine_audit():
    """F7：新引擎审计投影进搜索摘要——正式轮次/经典层/筛选行 + engine stop_reason。"""
    context = {
        "provider": "multi:arxiv+scopus+openalex",
        "decompose": {"en_queries": ["mamba vision"]},
        "preflight": [{"name": "scopus", "role": "primary", "status": "ok"}],
        "agent_loops": {"decompose": {"preview_rounds": [], "trace": []}},
        "recall": {
            "engine": "target_recall_v1", "stop_reason": "planner_stop",
            "rounds": [{"round": 1, "stats": [{
                "keyword": "mamba vision", "raw_hits": 12,
                "sources": {"arxiv": 4, "scopus": 4, "openalex": 4},
                "source_errors": {"scopus": "ConnectError"},
            }]}],
            "classic": [{"round": 1, "keyword": "mamba vision",
                         "sources": {"scopus": 3, "openalex": 3}, "source_errors": {}}],
            "filter": {"skipped": False, "kept": 100, "rejected": 20, "unknown": 5},
        },
    }
    summary = sr._search_summary(context)
    formal = [a for a in summary["attempts"] if a["phase"] == "formal"]
    assert len(formal) == 5  # 3 源 ×1 关键词 + 2 源经典层
    scopus_row = next(a for a in formal if a["provider"] == "scopus" and a["order"] == "relevance")
    assert scopus_row["error"] == "ConnectError" and scopus_row["hits"] == 4
    classic_row = next(a for a in formal if a["order"] == "cited")
    assert "被引经典层" in classic_row["query"]
    filter_rows = [a for a in summary["attempts"] if a["phase"] == "filter"]
    assert filter_rows and filter_rows[0]["rejected"] == 20
    assert summary["engine"] == "target_recall_v1"
    assert summary["stop_reason"] == "engine:planner_stop"
