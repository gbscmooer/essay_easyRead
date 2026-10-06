import asyncio
import hashlib
import json

import pytest
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


def test_delivery_direct_write_stops_after_generated_report(monkeypatch):
    """生成链路在主笔正文规范化后结束，不再核验或定向修订。"""
    from backend.services.deep_research import report_delivery as rd

    async def select(papers, *args, **kwargs):
        return papers, {"warnings": []}

    monkeypatch.setattr(sr, "retrieve_materials", select)

    async def llm(_cfg, system, user, **_kwargs):
        if "文献证据复核器" in system:
            payload = json.loads(user)
            return {"supported_ids": [row["id"] for row in payload["candidates"]], "rejected_ids": []}
        if "单篇证据提取阶段代理" in system:
            return {"experimental_comparisons": [experiment_row()]}
        if "调查报告修订代理" in system:
            payload = json.loads(user)
            # P1：修订输入必须携带证据正文（quote），只有标题+证据 ID 等于盲改
            assert payload["evidence_sources"][0]["evidence"][0]["quote"]
            evidence_id = payload["evidence_sources"][0]["evidence"][0]["evidence_id"]
            return {"revised_entries": [{
                "segment_id": payload["segments_to_revise"][0]["segment_id"],
                "text": f"SF-Mamba-B reports 84.4% under the paper's ImageNet-1K setting. [{evidence_id}]",
                "evidence_ids": [evidence_id],
            }]}
        if "调查报告撰写代理" in system:
            # 从证据包里取真实编号：草稿引用必须可被程序映射
            payload = json.loads(user)
            evidence_id = payload["evidence"][0]["evidence_id"]
            if payload.get("repair_instructions"):
                return {
                    "summary": [{"text": f"SF-Mamba-B reports 84.4% under the paper's ImageNet-1K setting. [{evidence_id}]",
                                 "evidence_ids": [evidence_id], "statement_kind": "fact"}],
                    "findings": [{"text": f"The table reports 3534 img/s. [{evidence_id}]",
                                  "evidence_ids": [evidence_id]}],
                    "limitations": [{"text": f"Cross-paper settings may differ. [{evidence_id}]",
                                     "evidence_ids": [evidence_id]}],
                    "gaps": [{"text": "Precision is not reported in the located evidence.",
                              "statement_kind": "process_note"}],
                    "novelty": [{"text": f"The evidence supports a scoped result. [{evidence_id}]",
                                 "evidence_ids": [evidence_id]}],
                }
            return {
                "summary": [{"text": f"SF-Mamba-B is universally faster. [{evidence_id}]",
                             "evidence_ids": [evidence_id]}],
                "findings": [{"text": f"The table reports 3534 img/s. [{evidence_id}]",
                              "evidence_ids": [evidence_id]}],
                "limitations": [], "gaps": [], "novelty": [],
            }
        raise AssertionError(system[:80])

    async def fake_batch_audit(answer, segments, _sources, _cfg, *, evidence_version=None,
                               citation_map_version=None, sources_for_batch=None, deadline_at=None,
                               batch_seconds=30, concurrency=2, on_batch=None):
        raise AssertionError("报告写完后不应启动真实性核验")

    monkeypatch.setattr(sr, "_llm_json", llm)
    monkeypatch.setattr(rd, "_llm_json", llm)
    monkeypatch.setattr(rd, "audit_report_text", fake_batch_audit)
    report = asyncio.run(sr.build_survey_report(
        "compare efficiency", [dict(PAPER)], {"by_year": {2026: 1}}, None,
        {"api_key": "test", "model": "fixture"}, depth="standard",
    ))
    delivery = report["delivery"]
    assert delivery["narrative_source"] == "final_draft"
    assert delivery["delivery_status"] == "ready"
    assert delivery["citation_map_version"]
    assert report["quality_status"] == "unchecked"
    assert report["method"]["final_audit"]["status"] == "not_requested"
    assert report["summary"].startswith("SF-Mamba-B is universally faster")
    # 正文编号由程序映射为 [n]（单篇论文 → [1]），claims 保留具体 evidence_ids
    assert "[1]" in report["summary"]
    assert report["narrative"]["summary"][0]["evidence_ids"]
    assert report["tables"]["experimental_comparisons"][0]["hardware"] == "NVIDIA A100 GPU"
    attempts = delivery["attempts"]
    assert attempts["write_calls"] == 1 and attempts["revision_calls"] == 0
    assert attempts["audit_batches"] == 0
    assert attempts["repair_calls"] == 0


def test_delivery_unavailable_when_extraction_fails_and_no_factual_evidence(monkeypatch):
    """初提取失败、证据库为空：给出确定性失败说明（unavailable），不再靠补提取
    循环恢复，也不把前置综合冒充终稿（有 synthesis 时明确标 preliminary 预览）。"""
    from backend.services.deep_research import report_delivery as rd

    async def select(papers, *args, **kwargs):
        return papers, {"warnings": []}

    monkeypatch.setattr(sr, "retrieve_materials", select)
    write_calls = []

    async def llm(_cfg, system, user, **_kwargs):
        if "单篇证据提取阶段代理" in system:
            raise ValueError("simulated malformed initial extraction")
        if "调查报告撰写代理" in system:
            write_calls.append(user)
            return {"summary": "must not be used"}
        raise AssertionError(system[:80])

    async def passed_audit(*_args, **_kwargs):
        raise AssertionError("无事实证据时不应启动写作与核验")

    monkeypatch.setattr(sr, "_llm_json", llm)
    monkeypatch.setattr(rd, "_llm_json", llm)
    monkeypatch.setattr(rd, "audit_report_text", passed_audit)

    # 无前置综合 → unavailable + 确定性材料/失败说明，不编造领域结论
    report = asyncio.run(sr.build_survey_report(
        "compare runtime efficiency", [dict(PAPER)], {"by_year": {2026: 1}}, None,
        {"api_key": "test", "model": "fixture"}, depth="standard",
    ))
    assert report["coverage"]["examined"] == 0
    assert report["status"] == "unavailable"
    delivery = report["delivery"]
    assert delivery["narrative_source"] == "none"
    assert delivery["delivery_status"] == "unavailable"
    assert delivery["error_code"] == "NO_FACTUAL_EVIDENCE"
    assert "不是关于领域现状的结论" in report["summary"]
    assert write_calls == []

    # 有前置综合 → 保留为明确标注的 preliminary 预览，不冒充终稿
    synthesis = {"landscape_summary": "preliminary landscape text", "hotspots": ["hotspot a"]}
    report2 = asyncio.run(sr.build_survey_report(
        "compare runtime efficiency", [dict(PAPER)], {"by_year": {2026: 1}}, synthesis,
        {"api_key": "test", "model": "fixture"}, depth="standard",
    ))
    assert report2["delivery"]["narrative_source"] == "preliminary"
    assert report2["delivery"]["delivery_status"] == "partial"
    assert report2["summary"] == "preliminary landscape text"
    assert report2["findings"] == ["hotspot a"]
    assert report2["quality_status"] == "needs_review"
    assert any("preliminary" in w for w in report2["warnings"])
    assert write_calls == []


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


# ── 证据按含义多子问题关联（取消表格类型硬映射） ────────────────────────────

def test_evidence_multi_subquestion_mapping_by_meaning():
    store = ReportEvidenceStore()
    paper = {"key": "doi:10.1/x", "title": "T"}

    def _add(evidence_type, fact, quote):
        record, _ = store.add(evidence_type=evidence_type, fact=fact, paper=paper,
                              quote=quote, source_kind="full_text_excerpt", reader=None)
        return record["subquestion_ids"]

    # taxonomy 谈"下一代候选/未来方向"→ 同时关联趋势查新，不再被表格类型锁死
    ids = _add("taxonomy",
               {"category": "next-generation visual foundation model candidates",
                "description": "candidate architectures as future direction"},
               "next-generation visual foundation model candidates as future direction")
    assert "method_mechanism" in ids and "novelty_scope" in ids

    # 指标行含吞吐词 → 同时算运行效率
    ids = _add("metrics", {"aspect": "throughput", "metric": "img/s",
                           "description": "images per second throughput"},
               "throughput measured in img/s")
    assert "effect_comparison" in ids and "runtime_efficiency" in ids

    # 局限语言 → limitations 有路径（旧规则五张表永远进不了 limitations）
    ids = _add("taxonomy", {"category": "deployment challenge",
                            "description": "open problem in deployment"},
               "deployment challenge remains an open problem")
    assert "limitations" in ids

    # 普通技术分类仍只归方法机制（信号词保守，不过度关联）
    ids = _add("taxonomy", {"category": "patch embedding", "description": "tokenization"},
               "patch embedding splits the image")
    assert ids == ["method_mechanism"]


# ── 阅读预算分账 + 无效证据编号显式拒绝 ─────────────────────────────────────

def test_delivery_repairs_object_findings_and_unknown_evidence(monkeypatch):
    """run1 实际错误形状回归：对象型 findings 不再被静默丢弃；未知 ev_*/越界编号
    显式报 UNKNOWN_EVIDENCE_REFERENCE；一次定向修复后交付。"""
    from backend.services.deep_research import report_delivery as rd

    async def select(papers, *args, **kwargs):
        return papers, {"warnings": []}

    monkeypatch.setattr(sr, "retrieve_materials", select)
    repair_seen = {}

    async def llm(_cfg, system, user, **_kwargs):
        if "文献证据复核器" in system:
            payload = json.loads(user)
            return {"supported_ids": [row["id"] for row in payload["candidates"]], "rejected_ids": []}
        if "单篇证据提取阶段代理" in system:
            return {"experimental_comparisons": [experiment_row()]}
        if "调查报告撰写代理" in system:
            payload = json.loads(user)
            evidence_id = payload["evidence"][0]["evidence_id"]
            if payload.get("repair_instructions"):
                repair_seen["feedback"] = payload["repair_instructions"]
                return {
                    "summary": [{"text": f"Scoped summary with one citation. [{evidence_id}]",
                                 "evidence_ids": [evidence_id]}],
                    "findings": [
                        {"content": f"The table reports 3534 img/s. [{evidence_id}]",
                         "subquestion_id": "runtime_efficiency", "role": "mainline"},
                        "Plain legacy string finding with citation [1].",
                    ],
                    "limitations": [], "gaps": [], "novelty": [],
                }
            return {
                "summary": [{"text": "Summary without any citation.",
                             "statement_kind": "fact"}],
                "findings": [
                    # run1 对象形状（content + role + subquestion_id，内联 ev 引用）
                    {"content": f"Known evidence works. [{evidence_id}]",
                     "subquestion_id": "runtime_efficiency", "role": "mainline"},
                    # 幽灵 ev 编号 + 越界数字引用：必须显式报错，不能静默吞掉
                    {"content": "Ghost reference here [ev_000000000000000000dead0] and [99999]",
                     "subquestion_id": "runtime_efficiency", "role": "mainline"},
                    # 未知对象字段：缺少可识别正文字段
                    {"subquestion_id": "runtime_efficiency", "year": 2026},
                ],
                "limitations": [], "gaps": [], "novelty": [],
            }
        raise AssertionError(system[:80])

    async def passed_batch_audit(answer, segments, _sources, _cfg, *, evidence_version=None,
                                 citation_map_version=None, sources_for_batch=None, deadline_at=None,
                                 batch_seconds=30, concurrency=2, on_batch=None):
        if on_batch is not None:
            for i in range(max(1, len(segments))):
                await on_batch(i, {"status": "completed"})
        return {
            "status": "completed", "unsupported_count": 0, "claim_count": 1,
            "supported_count": 1, "contradicted_count": 0, "insufficient_count": 0,
            "support_rate": 1.0, "claims": [],
            "coverage": [{"segment_id": segment["id"], "status": "audited", "claim_count": 1}
                         for segment in segments],
            "binding": {"text_hash": hashlib.sha256(answer.encode()).hexdigest(),
                        "evidence_version": evidence_version,
                        "citation_map_version": citation_map_version},
        }

    monkeypatch.setattr(sr, "_llm_json", llm)
    monkeypatch.setattr(rd, "_llm_json", llm)
    monkeypatch.setattr(rd, "audit_report_text", passed_batch_audit)
    report = asyncio.run(sr.build_survey_report(
        "compare efficiency", [dict(PAPER)], {"by_year": {2026: 1}}, None,
        {"api_key": "test", "model": "fixture"}, depth="quick",
    ))
    trace = json.dumps(report["method"]["agent_loop"]["trace"], ensure_ascii=False)
    # 首轮归一回执显式上报 received/accepted/rejected，不允许输入 3 条、保存 0 条只报成功
    assert "UNKNOWN_EVIDENCE_REFERENCE" in trace
    assert "DRAFT_SCHEMA_INVALID" in trace
    assert "received" in trace
    # 修复只发生一次；结构与事实共享额度（修复后不再有修订调用）
    attempts = report["delivery"]["attempts"]
    assert attempts["write_calls"] == 1 and attempts["repair_calls"] == 1
    assert attempts["revision_calls"] == 0
    assert "UNKNOWN_EVIDENCE_REFERENCE" in repair_seen["feedback"]
    assert "DRAFT_SCHEMA_INVALID" in repair_seen["feedback"]
    # 修复后的对象条目与旧字符串条目都完整保存，元数据保留
    findings = report["findings"]
    assert len(findings) == 2
    assert "3534 img/s" in findings[0]
    assert findings[1] == "Plain legacy string finding with citation [1]."
    assert report["narrative"]["findings"][0]["role"] == "mainline"
    assert report["narrative"]["findings"][0]["subquestion_id"] == "runtime_efficiency"
    assert report["delivery"]["delivery_status"] == "ready"
    assert report["quality_status"] == "unchecked"
    assert report["method"]["final_audit"]["status"] == "not_requested"


def test_verify_relations_duplicate_reuses_last_result(monkeypatch):
    """复核通过后模型原样再调 verify_relations（run3 实测单篇最多连调 3 次）：
    第二次直接复用上次结果，不再触发嵌套核验 LLM 调用。"""
    verify_llm_calls: list[dict] = []

    async def llm(_cfg, system, user, **_kwargs):
        if "文献证据复核器" in system:
            verify_llm_calls.append(json.loads(user))
            payload = json.loads(user)
            return {"supported_ids": [c["id"] for c in payload["candidates"]],
                    "rejected_ids": []}
        if "单篇证据提取阶段代理" in system:
            return actions.pop(0)
        raise AssertionError(system[:60])

    actions = [
        {"action": "inspect_material", "arguments": {},
         "rationale_summary": "先看材料"},
        {"action": "submit_extraction",
         "arguments": {"experimental_comparisons": [experiment_row()]},
         "rationale_summary": "提交提取"},
        {"action": "verify_relations", "arguments": {},
         "rationale_summary": "复核"},
        {"action": "verify_relations", "arguments": {},
         "rationale_summary": "模型重复复核同一草稿"},
        {"action": "finish", "finish": True, "rationale_summary": "完成"},
    ]

    monkeypatch.setattr(sr, "_llm_json", llm)
    rows, rejected, audit = asyncio.run(sr.extract_paper_with_agent(
        "compare efficiency", dict(PAPER), TEXT, "full_text",
        {"api_key": "test", "model": "stub"}, "standard",
    ))

    assert len(verify_llm_calls) == 1  # 重复复核没有再打核验器
    assert audit["iterations"] == 5
    assert audit["relation_verification"]["calls"] == 1
    dup = audit["trace"][3]["observation"]
    assert dup["duplicate"] is True and dup["verified"] is True
    assert rows["experimental_comparisons"]
    assert rejected == 0


def test_extract_payload_orders_stable_prefix_first(monkeypatch):
    """逐篇提取的每轮载荷：全 run 恒定项（问题/子问题）在最前、论文恒定项次之、
    逐轮变化的 view 殿后——跨论文与跨轮次共享最长网关前缀缓存。"""
    seen: list[dict] = []

    async def llm(_cfg, system, user, **_kwargs):
        if "文献证据复核器" in system:
            payload = json.loads(user)
            return {"supported_ids": [c["id"] for c in payload["candidates"]],
                    "rejected_ids": []}
        if "单篇证据提取阶段代理" in system:
            seen.append(json.loads(user))
            return actions.pop(0)
        raise AssertionError(system[:60])

    actions = [
        {"action": "inspect_material", "arguments": {}},
        {"action": "submit_extraction",
         "arguments": {"experimental_comparisons": [experiment_row()]}},
        {"action": "verify_relations", "arguments": {}},
        {"action": "finish", "finish": True},
    ]

    monkeypatch.setattr(sr, "_llm_json", llm)
    asyncio.run(sr.extract_paper_with_agent(
        "compare efficiency", dict(PAPER), TEXT, "full_text",
        {"api_key": "test", "model": "stub"}, "standard",
        subquestions=[{"id": "runtime_efficiency", "question": "how fast?"}],
    ))

    keys = list(seen[0])
    # 旧顺序 paper_title 排第二，跨论文共享前缀只有问题一句；新顺序子问题前置
    assert keys.index("research_question") < keys.index("subquestions") \
        < keys.index("paper_title") < keys.index("initial_material")
    assert keys[-2:] == ["iteration", "max_iterations"]
    state_keys = list(seen[0]["state"])
    assert state_keys[:2] == ["budget", "subquestions"]      # 恒定项最前
    assert state_keys[-2:] == ["draft", "rows"]              # 整体替换项殿后


def test_delivery_supports_sync_pause_callback_through_build(monkeypatch):
    """run3 事故回归（2026-09-18 用户审查）：explorer 编排传入的 should_pause
    是同步函数（返回布尔），deliver_report 旧实现无条件 await → 组装证据包前
    TypeError，终稿写作 0 次。必须走 build_survey_report 真实入口验证。"""
    from backend.services.deep_research import report_delivery as rd

    async def select(papers, *args, **kwargs):
        return papers, {"warnings": []}

    monkeypatch.setattr(sr, "retrieve_materials", select)
    write_calls: list[int] = []

    async def llm(_cfg, system, user, **_kwargs):
        if "文献证据复核器" in system:
            payload = json.loads(user)
            return {"supported_ids": [c["id"] for c in payload["candidates"]],
                    "rejected_ids": []}
        if "单篇证据提取阶段代理" in system:
            return {"experimental_comparisons": [experiment_row()]}
        if "调查报告撰写代理" in system:
            write_calls.append(1)
            payload = json.loads(user)
            evidence_id = payload["evidence"][0]["evidence_id"]
            return {
                "summary": [{"text": f"SF-Mamba-B reports 84.4% under the paper's setting. [{evidence_id}]",
                             "evidence_ids": [evidence_id], "statement_kind": "fact"}],
                "findings": [], "limitations": [], "gaps": [], "novelty": [],
            }
        raise AssertionError(system[:60])

    async def ok_audit(answer, segments, _sources, _cfg, *, evidence_version=None,
                       citation_map_version=None, sources_for_batch=None, deadline_at=None,
                       batch_seconds=30, concurrency=2, on_batch=None):
        if on_batch is not None:
            for i in range(max(1, len(segments))):
                await on_batch(i, {"status": "completed"})
        return {
            "status": "completed", "unsupported_count": 0, "claim_count": 0,
            "supported_count": 0, "contradicted_count": 0, "insufficient_count": 0,
            "support_rate": None, "claims": [],
            "coverage": [{"segment_id": s["id"], "status": "audited", "claim_count": 0}
                         for s in segments],
            "binding": {"text_hash": hashlib.sha256(answer.encode()).hexdigest(),
                        "evidence_version": evidence_version,
                        "citation_map_version": citation_map_version},
        }

    monkeypatch.setattr(sr, "_llm_json", llm)
    monkeypatch.setattr(rd, "_llm_json", llm)
    monkeypatch.setattr(rd, "audit_report_text", ok_audit)

    report = asyncio.run(sr.build_survey_report(
        "tumor detection with ai", [dict(PAPER)], {"by_year": {2026: 1}}, None,
        {"api_key": "test", "model": "fixture"}, depth="standard",
        should_pause=lambda: False,  # 与 explorer._paused 相同的同步形态
    ))
    assert write_calls, "同步暂停回调在写作前就崩溃，终稿 0 次"
    assert report["delivery"]["narrative_source"] == "final_draft"
    assert report["delivery"]["delivery_status"] in ("ready", "partial")
    assert report["summary"]


def test_lite_extraction_is_one_shot_with_single_verify(monkeypatch):
    """lite 档一次化提取（2026-09-18 用户审查）：程序直接组装材料与验收，
    模型一次结构化提取 + 非空行时一次关系复核，不再走多轮 ReAct。"""
    calls = {"extract": 0, "verify": 0}

    async def llm(_cfg, system, user, **_kwargs):
        if "文献证据复核器" in system:
            calls["verify"] += 1
            payload = json.loads(user)
            return {"supported_ids": [c["id"] for c in payload["candidates"]],
                    "rejected_ids": []}
        if "单篇证据提取器（轻量档" in system:
            calls["extract"] += 1
            payload = json.loads(user)
            assert payload["initial_material"]  # 程序已组装材料
            assert "iteration" not in payload   # 单次输出，无 ReAct view
            return {"experimental_comparisons": [experiment_row()]}
        raise AssertionError(system[:60])

    monkeypatch.setattr(sr, "_llm_json", llm)
    rows, rejected, audit = asyncio.run(sr.extract_paper_with_agent(
        "tumor detection with ai", dict(PAPER), TEXT, "full_text",
        {"api_key": "test", "model": "stub"}, "lite",
    ))
    assert calls == {"extract": 1, "verify": 1}
    assert rows["experimental_comparisons"]
    assert audit["status"] == "completed" and audit["stop_reason"] == "one_shot"
    assert audit["iterations"] == 1 and audit["model_calls"] == 1
    assert audit["relation_verification"]["calls"] == 1


def test_lite_extraction_skips_verify_when_no_rows(monkeypatch):
    """全空表不浪费判定调用：提取结果无任何非空行时复核为 0 次。"""
    calls = {"extract": 0, "verify": 0}

    async def llm(_cfg, system, user, **_kwargs):
        if "文献证据复核器" in system:
            calls["verify"] += 1
            return {"supported_ids": [], "rejected_ids": []}
        if "单篇证据提取器（轻量档" in system:
            calls["extract"] += 1
            return {"taxonomy": [], "metrics": []}
        raise AssertionError(system[:60])

    monkeypatch.setattr(sr, "_llm_json", llm)
    rows, rejected, audit = asyncio.run(sr.extract_paper_with_agent(
        "tumor detection with ai", dict(PAPER), TEXT, "full_text",
        {"api_key": "test", "model": "stub"}, "lite",
    ))
    assert calls == {"extract": 1, "verify": 0}
    assert all(not rows[name] for name in rows)
    assert audit["relation_verification"]["calls"] == 0
    assert audit["status"] == "completed"


# ── P0 交付正确性（document.md §2.4；重构验收见 docs/workflow_refactor/VALIDATION.md）──────────


def _delivery_store():
    reader = DocumentEvidenceReader(PAPER["key"], TEXT)
    store = ReportEvidenceStore()
    row = experiment_row()
    store.add(
        evidence_type="experimental_comparisons",
        fact={k: row[k] for k in sr.TABLE_FIELDS["experimental_comparisons"]},
        paper=PAPER, quote=row["quote"], source_kind="full_text_excerpt", reader=reader,
    )
    return store


def _delivery_tables():
    row = experiment_row()
    return {"experimental_comparisons": [
        {**row, "evidence": [{"paper_key": PAPER["key"]}]}]}


def _stale_audit_fake(calls, *, second_round_exp_table_fails):
    """首轮：summary/findings/limitations 已核验，exp-table 批次超时（旧缺陷来源）。"""

    async def fake_audit(answer, segments, _sources, _cfg, *, evidence_version=None,
                         citation_map_version=None, sources_for_batch=None, deadline_at=None, batch_seconds=30,
                         concurrency=2, on_batch=None):
        calls.append({"answer": answer, "segment_ids": [s["id"] for s in segments]})
        if len(calls) == 1:
            coverage = [
                {"segment_id": s["id"], "status": "failed", "claim_count": 0,
                 "error": "AUDIT_TIMEOUT"}
                if s["id"].startswith("exp-table") else
                {"segment_id": s["id"], "status": "audited", "claim_count": 1}
                for s in segments
            ]
            return {"status": "partial", "unsupported_count": 1, "claim_count": 1,
                    "supported_count": 0, "contradicted_count": 0, "insufficient_count": 1,
                    "support_rate": None,
                    "claims": [{"claim": "universally faster", "label": "insufficient",
                                "answer_quote": "SF-Mamba-B is universally faster.",
                                "repair_direction": "限定范围", "evidence_ids": []}],
                    "coverage": coverage,
                    "binding": {"text_hash": hashlib.sha256(answer.encode()).hexdigest(),
                                "evidence_version": evidence_version,
                                "citation_map_version": citation_map_version},
                    "error": {"code": "AUDIT_INCOMPLETE", "detail": "batch timeout"}}
        coverage = [
            {"segment_id": s["id"], "status": "failed", "claim_count": 0,
             "error": "AUDIT_TIMEOUT"}
            if second_round_exp_table_fails and s["id"].startswith("exp-table") else
            {"segment_id": s["id"], "status": "audited", "claim_count": 1}
            for s in segments
        ]
        complete = not second_round_exp_table_fails
        return {"status": "completed" if complete else "partial",
                "unsupported_count": 0, "claim_count": 1,
                "supported_count": 1, "contradicted_count": 0, "insufficient_count": 0,
                "support_rate": 1.0 if complete else None,
                "claims": [{"claim": "scoped result", "label": "supported",
                            "answer_quote": "SF-Mamba-B reports 84.4% under the paper's ImageNet-1K setting.",
                            "evidence_ids": []}],
                "coverage": coverage,
                "binding": {"text_hash": hashlib.sha256(answer.encode()).hexdigest(),
                            "evidence_version": evidence_version,
                            "citation_map_version": citation_map_version}}

    return fake_audit


def _stale_llm(calls):
    async def llm(_cfg, system, user, **_kwargs):
        payload = json.loads(user)
        if "调查报告修订代理" in system:
            # v3.1 定向段级修订：只输出受影响段落的替换条目
            calls["model"] = calls.get("model", 0) + 1
            evidence_id = payload["evidence_sources"][0]["evidence"][0]["evidence_id"]
            return {"revised_entries": [{
                "segment_id": payload["segments_to_revise"][0]["segment_id"],
                "text": f"SF-Mamba-B reports 84.4% under the paper's ImageNet-1K setting. [{evidence_id}]",
                "evidence_ids": [evidence_id],
            }]}
        assert "调查报告撰写代理" in system
        evidence_id = payload["evidence"][0]["evidence_id"]
        calls["model"] = calls.get("model", 0) + 1
        if payload.get("repair_instructions"):
            return {
                "summary": [{"text": f"SF-Mamba-B reports 84.4% under the paper's ImageNet-1K setting. [{evidence_id}]",
                             "evidence_ids": [evidence_id]}],
                "findings": [{"text": f"The table reports 3534 img/s. [{evidence_id}]",
                              "evidence_ids": [evidence_id]}],
                "limitations": [{"text": f"Cross-paper settings may differ. [{evidence_id}]",
                                 "evidence_ids": [evidence_id]}],
                "gaps": [{"text": "Precision is not reported in the located evidence.",
                          "statement_kind": "process_note"}],
                "novelty": [{"text": f"The evidence supports a scoped result. [{evidence_id}]",
                             "evidence_ids": [evidence_id]}],
            }
        return {
            "summary": [{"text": f"SF-Mamba-B is universally faster. [{evidence_id}]",
                         "evidence_ids": [evidence_id]}],
            "findings": [{"text": f"The table reports 3534 img/s. [{evidence_id}]",
                          "evidence_ids": [evidence_id]}],
            "limitations": [{"text": f"Cross-paper settings may differ. [{evidence_id}]",
                             "evidence_ids": [evidence_id]}],
            "gaps": [], "novelty": [],
        }

    return llm


def test_delivery_does_not_enter_legacy_audit_or_revision(monkeypatch):
    """生产入口在主笔写完后停止，旧核验/修订实现不可达。"""
    from backend.services.deep_research import report_delivery as rd

    store = _delivery_store()
    audit_calls: list[dict] = []
    monkeypatch.setattr(rd, "_llm_json", _stale_llm({}))
    monkeypatch.setattr(rd, "audit_report_text",
                        _stale_audit_fake(audit_calls, second_round_exp_table_fails=False))
    result = asyncio.run(rd.deliver_report(
        question="compare efficiency", subquestions=sr.report_subquestions("compare efficiency"),
        evidence_store=store, papers=[PAPER], refs={PAPER["key"]: 1},
        tables=_delivery_tables(), synthesis=None,
        cfg={"api_key": "test", "model": "fixture"},
    ))
    assert audit_calls == []
    assert result["audit"]["status"] == "not_requested"
    assert result["audit"]["coverage"] == []
    assert result["delivery"]["delivery_status"] == "ready"
    assert result["quality_status"] == "unchecked"
    attempts = result["delivery"]["attempts"]
    assert attempts["write_calls"] == 1 and attempts["revision_calls"] == 0
    assert attempts["repair_calls"] == 0 and attempts["repair_quota_used"] is False
    assert attempts["revision_quota_used"] is False


def test_delivery_ignores_legacy_failing_audit_hook(monkeypatch):
    """旧核验 hook 即使配置为失败，也不再影响已经生成的报告。"""
    from backend.services.deep_research import report_delivery as rd

    store = _delivery_store()
    audit_calls: list[dict] = []
    monkeypatch.setattr(rd, "_llm_json", _stale_llm({}))
    monkeypatch.setattr(rd, "audit_report_text",
                        _stale_audit_fake(audit_calls, second_round_exp_table_fails=True))
    result = asyncio.run(rd.deliver_report(
        question="compare efficiency", subquestions=sr.report_subquestions("compare efficiency"),
        evidence_store=store, papers=[PAPER], refs={PAPER["key"]: 1},
        tables=_delivery_tables(), synthesis=None,
        cfg={"api_key": "test", "model": "fixture"},
    ))
    assert audit_calls == []
    assert result["audit"]["status"] == "not_requested"
    assert result["audit"]["support_rate"] is None
    assert result["delivery"]["delivery_status"] == "ready"
    assert result["quality_status"] == "unchecked"
    assert result["narrative"]["summary"]


def test_delivery_repair_timeout_does_not_trigger_verification_revision(monkeypatch):
    """结构恢复超时后保留草稿，不再追加核验反馈修订。"""
    from backend.services.deep_research import report_delivery as rd

    store = _delivery_store()
    model_calls = {"n": 0}

    async def llm(_cfg, system, user, **_kwargs):
        model_calls["n"] += 1
        payload = json.loads(user)
        if system == rd._STATISTICS_WRITER_SYSTEM:
            return {"statistics_summary": []}
        if system == rd._EVIDENCE_OVERVIEW_SYSTEM:
            evidence_id = payload["evidence"][0]["evidence_id"]
            return {"overview": [{"dimension": "methods", "text": "fixed overview",
                                   "evidence_ids": [evidence_id]}], "gaps": []}
        if "调查报告修订代理" in system:
            # 修订尝试无有效条目：保留原稿，额度仍算已用
            return {"revised_entries": []}
        evidence_id = payload["evidence"][0]["evidence_id"]
        if payload.get("repair_instructions"):
            raise asyncio.TimeoutError("repair timed out")
        return {
            # 一条有效 + 一条未知引用 → 触发修复分支
            "summary": [{"text": f"The table reports 3534 img/s. [{evidence_id}]",
                         "evidence_ids": [evidence_id]}],
            "findings": [{"text": "A bold claim. [ev_deadbeef11]", "evidence_ids": ["ev_deadbeef11"]}],
            "limitations": [], "gaps": [], "novelty": [],
        }

    async def fake_audit(answer, segments, _sources, _cfg, *, evidence_version=None,
                         citation_map_version=None, sources_for_batch=None, deadline_at=None, batch_seconds=30,
                         concurrency=2, on_batch=None):
        return {"status": "completed", "unsupported_count": 1, "claim_count": 1,
                "supported_count": 0, "contradicted_count": 0, "insufficient_count": 1,
                "support_rate": None,
                "claims": [{"claim": "bold", "label": "insufficient",
                            "answer_quote": "A bold claim.",
                            "repair_direction": "删除", "evidence_ids": []}],
                "coverage": [{"segment_id": s["id"], "status": "audited", "claim_count": 1}
                             for s in segments],
                "binding": {"text_hash": hashlib.sha256(answer.encode()).hexdigest(),
                            "evidence_version": evidence_version,
                            "citation_map_version": citation_map_version}}

    monkeypatch.setattr(rd, "_llm_json", llm)
    monkeypatch.setattr(rd, "audit_report_text", fake_audit)
    result = asyncio.run(rd.deliver_report(
        question="compare efficiency", subquestions=sr.report_subquestions("compare efficiency"),
        evidence_store=store, papers=[PAPER], refs={PAPER["key"]: 1},
        tables=_delivery_tables(), synthesis=None,
        cfg={"api_key": "test", "model": "fixture"},
    ))
    # 两个固定角色 + 初稿 1 + 一次结构恢复尝试；不再调用事实修订代理。
    assert model_calls["n"] == 4
    attempts = result["delivery"]["attempts"]
    assert attempts["write_calls"] == 1
    assert attempts["repair_calls"] == 0 and attempts["revision_calls"] == 0
    assert attempts["repair_quota_used"] is True
    assert attempts["revision_quota_used"] is False
    assert result["delivery"]["delivery_status"] == "partial"
    assert result["delivery"]["error_code"] == "UNKNOWN_EVIDENCE_REFERENCE"


def test_delivery_never_calls_legacy_audit_hook(monkeypatch):
    """主笔成功后直接保存；旧核验 hook 的异常不影响生成结果。"""
    from backend.services.deep_research import report_delivery as rd

    store = _delivery_store()
    model_calls = {"n": 0}

    async def llm(_cfg, system, user, **_kwargs):
        model_calls["n"] += 1
        payload = json.loads(user)
        evidence_id = payload["evidence"][0]["evidence_id"]
        return {
            "summary": [{"text": f"The table reports 3534 img/s. [{evidence_id}]",
                         "evidence_ids": [evidence_id]}],
            "findings": [], "limitations": [], "gaps": [], "novelty": [],
        }

    async def broken_audit(*_args, **_kwargs):
        raise RuntimeError("audit orchestration crashed")

    monkeypatch.setattr(rd, "_llm_json", llm)
    monkeypatch.setattr(rd, "audit_report_text", broken_audit)
    diagnostics: dict = {}
    result = asyncio.run(rd.deliver_report(
        question="compare efficiency", subquestions=sr.report_subquestions("compare efficiency"),
        evidence_store=store, papers=[PAPER], refs={PAPER["key"]: 1},
        tables=_delivery_tables(), synthesis=None,
        cfg={"api_key": "test", "model": "fixture"}, diagnostics=diagnostics,
    ))
    assert result["delivery"]["narrative_source"] == "final_draft"
    assert result["delivery"]["delivery_status"] == "ready"
    assert result["quality_status"] == "unchecked"
    assert result["narrative"]["summary"]
    # 初稿接受留痕；核验与核验后重写都没有调用记录。
    assert diagnostics["delivery"]["draft_accepted"]["accepted"] == 1
    stages = {row["stage"] for row in result["delivery"]["attempts"]["failures"]}
    assert "audit" not in stages and "revision_audit" not in stages
    assert result["delivery"]["attempts"]["revision_calls"] == 0
    assert result["delivery"]["attempts"]["audit_batches"] == 0
    assert result["audit"]["status"] == "not_requested"


def test_audit_report_text_notifies_each_batch_immediately(monkeypatch):
    """批次完成即回调：快批的 on_batch 必须先于慢批评审结束（不等全部 gather）。"""
    events: list[tuple[str, str]] = []

    async def fake_audit_answer(answer, _sources, _cfg, *, evidence_version=None, **_kwargs):
        if "SLOW" in answer:
            await asyncio.sleep(0.08)
            events.append(("answer_done", "slow"))
        else:
            events.append(("answer_done", "fast"))
        return {"status": "completed",
                "claims": [{"claim": "c", "label": "supported", "answer_quote": answer}],
                "supported_count": 1, "contradicted_count": 0, "insufficient_count": 0,
                "unsupported_count": 0, "claim_count": 1, "coverage_complete": True}

    monkeypatch.setattr(evidence_audit, "audit_answer", fake_audit_answer)

    async def on_batch(index, report):
        events.append(("batch", str(index)))

    segments = [{"id": "s0", "text": "SLOW " * 900}, {"id": "s1", "text": "fast fact."}]
    merged = asyncio.run(evidence_audit.audit_report_text(
        "ignored", segments, [{"id": "P1", "abstract": "a", "text": ""}],
        {"api_key": "k", "model": "m"}, on_batch=on_batch))
    assert events.index(("batch", "1")) < events.index(("answer_done", "slow"))
    assert merged["status"] == "completed"


def test_delivery_sync_pause_true_raises_before_any_model_call(monkeypatch):
    """P0 退出标准：同步 true 必须在组装证据包前安全点抛 StagePausedError，
    且不发起任何写作/核验模型调用（异步 true 形态由 resume 套件覆盖）。"""
    from backend.services.deep_research import report_delivery as rd
    from backend.services.deep_research.stage_agent import StagePausedError

    async def llm(*_args, **_kwargs):
        raise AssertionError("paused run must not call the model")

    async def audit(*_args, **_kwargs):
        raise AssertionError("paused run must not audit")

    monkeypatch.setattr(rd, "_llm_json", llm)
    monkeypatch.setattr(rd, "audit_report_text", audit)
    store = _delivery_store()
    with pytest.raises(StagePausedError):
        asyncio.run(rd.deliver_report(
            question="compare efficiency",
            subquestions=sr.report_subquestions("compare efficiency"),
            evidence_store=store, papers=[PAPER], refs={PAPER["key"]: 1},
            tables=_delivery_tables(), synthesis=None,
            cfg={"api_key": "test", "model": "fixture"},
            should_pause=lambda: True,
        ))
