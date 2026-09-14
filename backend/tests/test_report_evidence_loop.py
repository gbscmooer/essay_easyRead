import asyncio
import hashlib
import json

from sqlmodel import Session

from backend.models.exploration import ExplorationRun
from backend.services.deep_research import survey_report as sr
from backend.services.deep_research import evidence_audit
from backend.services.deep_research.document_evidence import DocumentEvidenceReader
from backend.services.deep_research.evidence_audit import audit_binding_valid
from backend.services.deep_research.explorer import _persist_legacy_report_revision
from backend.services.deep_research.report_evidence import ReportEvidenceStore


TEXT = """--- Page 1 ---
Introduction

We introduce SF-Mamba for image classification.

--- Page 6 ---
Table 1: ImageNet-1K classification at 224×224 input resolution.

Model Acc. (%) img/s
MambaVision-B 84.2 2974
SF-Mamba-B 84.4 3534

Throughput is measured on a single NVIDIA A100 GPU with a batch size of 128.

--- Page 7 ---
Limitations

Results from different papers may use different software and measurement settings.
"""

PAPER = {
    "key": "arxiv:test", "title": "SF-Mamba", "year": 2026, "arxiv_id": "test",
    "abstract": "SF-Mamba studies image classification.", "source_text": TEXT,
}


def test_report_stage_timeout_never_preempts_provider_deadline():
    assert sr._stage_timeout({"timeout_seconds": 150}, 120) == 155
    assert sr._stage_timeout({"timeout_seconds": 30}, 120) == 120
    assert sr._stage_timeout({"timeout_seconds": 999}, 120) == 605


def experiment_row():
    return {
        "method": "SF-Mamba-B", "baseline_method": "MambaVision-B",
        "task": "image classification", "dataset": "ImageNet-1K", "split": "",
        "metric": "Acc.", "value": "84.4", "baseline_value": "84.2", "unit": "%",
        "metric_direction": "higher", "hardware": "NVIDIA A100 GPU", "batch_size": "128",
        "input_size": "224×224", "precision": "", "comparison_limit": "different measurement settings",
        "quote": (
            "Table 1: ImageNet-1K classification at 224×224 input resolution. Model Acc. (%) img/s "
            "MambaVision-B 84.2 2974 SF-Mamba-B 84.4 3534 Throughput is measured on a single NVIDIA "
            "A100 GPU with a batch size of 128."
        ),
    }


def test_full_text_reader_finds_late_table_and_preserves_location():
    reader = DocumentEvidenceReader(PAPER["key"], TEXT)
    result = reader.search("A100 batch size throughput", limit=2)
    assert result["total"] >= 1
    location = result["items"][0]
    context = reader.read_context(location["location_id"], before=2, after=1)
    assert "Table 1" in context["text"]
    assert context["page"] == 6
    located = reader.locate_quote("SF-Mamba-B 84.4 3534")
    assert located["page"] == 6
    assert located["location_status"] == "resolved"


def test_subquestion_queries_keep_table_signals_focused():
    reader = DocumentEvidenceReader(PAPER["key"], TEXT)
    question = "efficient image classification"
    queries = {item["id"]: item["query"] for item in sr.report_subquestions(question)}
    assert all("efficient image classification" in query for query in queries.values())
    assert "selective scan" not in " ".join(queries.values()).lower()
    effect = reader.search(queries["effect_comparison"], limit=1)["items"][0]
    runtime = reader.search(queries["runtime_efficiency"], limit=1)["items"][0]
    assert effect["page"] == 6
    assert runtime["page"] == 6


def test_subquestion_queries_follow_non_cv_domain():
    queries = sr.report_subquestions("protein folding with diffusion models")
    assert all("protein folding" in item["query"] for item in queries)
    assert all("ImageNet" not in item["query"] for item in queries)


def test_evidence_store_is_stable_deduplicated_paged_and_fail_closed():
    reader = DocumentEvidenceReader(PAPER["key"], TEXT)
    store = ReportEvidenceStore()
    row = experiment_row()
    record1, created1 = store.add(
        evidence_type="experimental_comparisons", fact={k: row[k] for k in sr.TABLE_FIELDS["experimental_comparisons"]},
        paper=PAPER, quote=row["quote"], source_kind="full_text_excerpt", reader=reader,
    )
    record2, created2 = store.add(
        evidence_type="experimental_comparisons", fact={k: row[k] for k in sr.TABLE_FIELDS["experimental_comparisons"]},
        paper=PAPER, quote=row["quote"], source_kind="full_text_excerpt", reader=reader,
        subquestion_ids=["runtime_efficiency", "limitations"],
    )
    assert created1 is True and created2 is False
    assert record1["evidence_id"] == record2["evidence_id"]
    assert set(record2["subquestion_ids"]) >= {"effect_comparison", "runtime_efficiency", "limitations"}
    assert record1["spans"][0]["page"] == 6
    assert "precision" in record1["missing_fields"] and "split" in record1["missing_fields"]
    assert record1["comparability"]["cross_paper_fairness_assumed"] is False
    page = store.index(subquestion_id="runtime_efficiency", cursor=0, limit=1)
    assert page["total"] == 1 and page["items"][0]["evidence_id"] == record1["evidence_id"]
    assert store.details([record1["evidence_id"], record1["evidence_id"]]) == [record1]


def test_evidence_after_old_24_row_cutoff_remains_pageable():
    store = ReportEvidenceStore()
    for index in range(30):
        store.add(
            evidence_type="metrics", fact={"aspect": "accuracy", "metric": f"metric-{index}"},
            paper={"key": f"p{index}", "title": f"Paper {index}"},
            quote=f"metric-{index} is reported for accuracy.", source_kind="abstract", reader=None,
        )
    first = store.index(subquestion_id="effect_comparison", cursor=0, limit=12)
    last = store.index(subquestion_id="effect_comparison", cursor=24, limit=12)
    assert first["total"] == 30 and first["next_cursor"] == 12
    assert len(last["items"]) == 6 and last["next_cursor"] is None
    assert store.details([last["items"][-1]["evidence_id"]])[0]["fact"]["metric"].startswith("metric-")


def test_experimental_comparison_requires_literal_method_metric_and_value():
    good = experiment_row()
    bad = {**good, "value": "99.9"}
    rows, rejected = sr.normalize_extraction(
        {"experimental_comparisons": [good, bad]}, PAPER, TEXT, "full_text_excerpt",
    )
    assert rejected == 1
    assert rows["experimental_comparisons"][0]["precision"] == ""
    assert rows["experimental_comparisons"][0]["baseline_value"] == "84.2"


def test_audit_binding_changes_with_text_or_evidence_version():
    answer = "SF-Mamba-B reports 84.4%. [P1]"
    report = {"binding": {"text_hash": hashlib.sha256(answer.encode()).hexdigest(),
                          "evidence_version": "es_1"}}
    assert audit_binding_valid(report, answer, "es_1")
    assert not audit_binding_valid(report, answer + " changed", "es_1")
    assert not audit_binding_valid(report, answer, "es_2")


def test_audit_feedback_returns_stable_evidence_id_and_repair(monkeypatch):
    answer = "SF-Mamba-B is universally faster. [P1]"
    quote = "SF-Mamba-B 84.4 3534"

    async def judge(*_args, **_kwargs):
        return {
            "claims": [{"claim": "universally faster", "answer_quote": answer,
                        "source_ids": ["P1"], "label": "insufficient",
                        "evidence": [{"source_id": "P1", "quote": quote}],
                        "reason": "只支持给定表格设置", "repair_direction": "限定到 ImageNet-1K 设置"}],
            "non_factual_spans": [], "total_claims": 1,
            "coverage_complete": True, "truncated": False,
        }

    monkeypatch.setattr(evidence_audit, "_llm_json", judge)
    report = asyncio.run(evidence_audit.audit_answer(
        answer,
        [{"id": "P1", "title": "SF-Mamba", "abstract": "", "text": quote,
          "evidence_spans": [{"evidence_id": "ev_fixed", "quote": quote}]}],
        {"api_key": "test", "model": "fixture"}, evidence_version="es_fixed",
    ))
    assert report["claims"][0]["evidence_ids"] == ["ev_fixed"]
    assert report["claims"][0]["repair_direction"] == "限定到 ImageNet-1K 设置"
    assert audit_binding_valid(report, answer, "es_fixed")


def test_report_loop_reads_index_and_reaudits_rewritten_claim(monkeypatch):
    async def select(papers, *args, **kwargs):
        return papers, {"warnings": []}

    monkeypatch.setattr(sr, "retrieve_materials", select)
    report_actions = iter([
        {"action": "inspect_evidence_index", "arguments": {"subquestion_id": "runtime_efficiency", "cursor": 0, "limit": 12}},
        {"action": "read_evidence_details", "arguments": {"evidence_ids": []}},
        {"action": "request_targeted_read", "arguments": {
            "paper_key": "arxiv:test", "subquestion_id": "runtime_efficiency",
            "query": "throughput NVIDIA A100 batch size 128",
        }},
        {"action": "submit_targeted_extraction", "arguments": {
            "paper_key": "arxiv:test", "subquestion_id": "runtime_efficiency",
            "extraction": {"experimental_comparisons": [{
                **experiment_row(), "metric": "Throughput", "value": "3534",
                "baseline_value": "2974", "unit": "img/s",
            }]},
        }},
        {"action": "submit_report", "arguments": {
            "summary": "SF-Mamba-B reports 84.4% and is universally faster. [1]",
            "findings": ["The table reports 3534 img/s. [1]"],
            "limitations": ["Cross-paper settings may differ. [1]"],
            "gaps": ["Precision is not reported in the located evidence. [1]"],
            "novelty": "The evidence supports a scoped result, not universal replacement. [1]",
        }},
        {"action": "audit_report", "arguments": {}},
        {"action": "submit_report", "arguments": {
            "summary": "SF-Mamba-B reports 84.4% under the paper's ImageNet-1K setting. [1]",
            "findings": ["The table reports 3534 img/s. [1]"],
            "limitations": ["Cross-paper settings may differ. [1]"],
            "gaps": ["Precision is not reported in the located evidence. [1]"],
            "novelty": "The evidence supports a scoped result, not universal replacement. [1]",
        }},
        {"action": "audit_report", "arguments": {}},
        {"action": "finish", "finish": True},
    ])

    async def llm(_cfg, system, user, **_kwargs):
        if "文献证据复核器" in system:
            payload = json.loads(user)
            return {"supported_ids": [row["id"] for row in payload["candidates"]], "rejected_ids": []}
        if "单篇证据提取阶段代理" in system:
            return {"experimental_comparisons": [experiment_row()]}
        if "调查报告综合与修订代理" in system:
            action = next(report_actions)
            if action["action"] == "read_evidence_details":
                view = json.loads(user)
                ids = [item["evidence_id"] for item in view["last_observation"].get("items", [])]
                action["arguments"]["evidence_ids"] = ids
            return action
        raise AssertionError(system[:80])

    audits = []

    async def fake_audit(answer, sources, _cfg, *, evidence_version=None):
        audits.append(answer)
        unsupported = len(audits) == 1
        return {
            "status": "completed", "unsupported_count": 1 if unsupported else 0,
            "claim_count": 1, "binding": {
                "text_hash": hashlib.sha256(answer.encode()).hexdigest(),
                "evidence_version": evidence_version,
            },
            "claims": ([{"claim": "universally faster", "label": "insufficient",
                         "reason": "证据只覆盖给定设置", "evidence_ids": [],
                         "repair_direction": "限定到论文报告的设置"}] if unsupported else
                       [{"claim": "scoped result", "label": "supported", "evidence_ids": []}]),
        }

    monkeypatch.setattr(sr, "_llm_json", llm)
    monkeypatch.setattr(sr, "audit_answer", fake_audit)
    report = asyncio.run(sr.build_survey_report(
        "compare efficiency", [dict(PAPER)], {"by_year": {2026: 1}}, None,
        {"api_key": "test", "model": "fixture"}, depth="standard",
    ))
    assert len(audits) == 2
    assert "universally faster" in audits[0] and "universally faster" not in audits[1]
    assert report["method"]["final_audit"]["status"] == "completed"
    assert report["method"]["agent_loop"]["status"] == "completed"
    assert report["method"]["narrative_source"] == "verified_shared_evidence_store"
    assert report["tables"]["experimental_comparisons"][0]["hardware"] == "NVIDIA A100 GPU"
    assert any(row["metric"] == "Throughput" for row in report["tables"]["experimental_comparisons"])
    evidence = report["evidence_store"]["records"][0]
    assert evidence["spans"][0]["page"] == 6
    assert report["summary"].startswith("SF-Mamba-B reports 84.4% under")


def test_report_loop_can_recover_evidence_when_initial_extraction_fails(monkeypatch):
    async def select(papers, *args, **kwargs):
        return papers, {"warnings": []}

    monkeypatch.setattr(sr, "retrieve_materials", select)
    actions = iter([
        {"action": "inspect_evidence_index", "arguments": {
            "subquestion_id": "runtime_efficiency", "cursor": 0, "limit": 12,
        }},
        {"action": "request_targeted_read", "arguments": {
            "paper_key": "arxiv:test", "subquestion_id": "runtime_efficiency",
            "query": "throughput NVIDIA A100 batch size 128",
        }},
        {"action": "submit_targeted_extraction", "arguments": {
            "paper_key": "arxiv:test", "subquestion_id": "runtime_efficiency",
            "extraction": {"experimental_comparisons": [{
                **experiment_row(), "metric": "Throughput", "value": "3534",
                "baseline_value": "2974", "unit": "img/s",
            }]},
        }},
        {"action": "submit_report", "arguments": {
            "summary": "The saved paper reports 3534 img/s under its stated setup. [1]",
            "findings": ["The compared baseline reports 2974 img/s. [1]"],
            "limitations": ["The located evidence does not establish cross-paper fairness. [1]"],
            "gaps": ["Measurement precision requires manual confirmation. [1]"],
            "novelty": "This single-paper result cannot establish a field-wide replacement trend. [1]",
        }},
        {"action": "audit_report", "arguments": {}},
        {"action": "finish", "finish": True},
    ])

    async def llm(_cfg, system, user, **_kwargs):
        if "单篇证据提取阶段代理" in system:
            raise ValueError("simulated malformed initial extraction")
        if "文献证据复核器" in system:
            payload = json.loads(user)
            return {"supported_ids": [row["id"] for row in payload["candidates"]], "rejected_ids": []}
        if "调查报告综合与修订代理" in system:
            payload = json.loads(user)
            assert payload["available_materials"][0]["paper_key"] == "arxiv:test"
            return next(actions)
        raise AssertionError(system[:80])

    async def passed_audit(answer, _sources, _cfg, *, evidence_version=None):
        return {
            "status": "completed", "unsupported_count": 0, "claim_count": 5,
            "claims": [], "binding": {
                "text_hash": hashlib.sha256(answer.encode()).hexdigest(),
                "evidence_version": evidence_version,
            },
        }

    monkeypatch.setattr(sr, "_llm_json", llm)
    monkeypatch.setattr(sr, "audit_answer", passed_audit)
    report = asyncio.run(sr.build_survey_report(
        "compare runtime efficiency", [dict(PAPER)], {"by_year": {2026: 1}}, None,
        {"api_key": "test", "model": "fixture"}, depth="standard",
    ))
    assert report["coverage"]["examined"] == 0
    assert report["status"] == "partial"
    assert report["quality_status"] == "internally_checked"
    assert report["tables"]["experimental_comparisons"][0]["value"] == "3534"
    assert report["evidence_store"]["count"] >= 1
    assert report["method"]["final_audit"]["status"] == "completed"


def test_legacy_evidence_index_detail_and_report_revisions(client, engine):
    reader = DocumentEvidenceReader(PAPER["key"], TEXT)
    store = ReportEvidenceStore()
    row = experiment_row()
    record, _ = store.add(
        evidence_type="experimental_comparisons",
        fact={key: row[key] for key in sr.TABLE_FIELDS["experimental_comparisons"]},
        paper=PAPER, quote=row["quote"], source_kind="full_text_excerpt", reader=reader,
    )
    old_report = {"title": "old", "markdown": "# old", "coverage": {},
                  "evidence_store": store.to_dict(), "method": {"final_audit": {"status": "not_started"}}}
    new_report = {"title": "new", "markdown": "# new", "coverage": {},
                  "evidence_store": store.to_dict(), "method": {"final_audit": {"status": "not_started"}}}
    with Session(engine) as session:
        run = ExplorationRun(question="q", status="done", result={"survey_report": old_report})
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id
    _persist_legacy_report_revision(run_id, new_report, previous_report=old_report)
    with Session(engine) as session:
        run = session.get(ExplorationRun, run_id)
        run.result = {"survey_report": new_report}
        session.add(run)
        session.commit()

    base = f"/api/exploration/runs/{run_id}"
    index = client.get(f"{base}/evidence?subquestion_id=runtime_efficiency&limit=1")
    assert index.status_code == 200
    assert index.json()["items"][0]["evidence_id"] == record["evidence_id"]
    detail = client.get(f"{base}/evidence/{record['evidence_id']}")
    assert detail.status_code == 200
    assert detail.json()["spans"][0]["page"] == 6
    assert detail.json()["missing_fields"]
    from backend.config import settings
    preflight = client.options(f"{base}/evidence", headers={
        "Origin": settings.frontend_origin,
        "Access-Control-Request-Method": "GET",
    })
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == settings.frontend_origin

    revisions = client.get(f"{base}/revisions").json()["items"]
    assert len(revisions) == 2
    old_id = revisions[-1]["revision_id"]
    assert client.get(f"{base}/revisions/{old_id}/report?format=json").json()["title"] == "old"
    assert client.get(f"{base}/report?format=json").json()["title"] == "new"
