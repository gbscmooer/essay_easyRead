"""报告交付契约（v3）回归：草稿归一、引用映射、证据包、分批核验与版本绑定。

冻结基线：backend/tests/fixtures/run1_report_baseline.json 保存了 2026-09-18 run1
（07:33:25Z）的实际失败材料——8 条对象型 findings 被静默丢弃、连续两轮 60s 审计
超时、预算耗尽后页面仍展示前置综合旧摘要。fixture_sha256 锚定材料不被篡改。
"""

import asyncio
import hashlib
import json
import os

import pytest
from sqlmodel import Session

from backend.services.deep_research import evidence_audit
from backend.services.deep_research import report_delivery as rd
from backend.services.deep_research.document_evidence import DocumentEvidenceReader
from backend.services.deep_research.evidence_audit import audit_binding_valid
from backend.services.deep_research.report_delivery import (
    build_evidence_pack,
    canonical_segments,
    frozen_citation_map,
    normalize_draft,
    project_legacy,
)
from backend.services.deep_research.report_evidence import ReportEvidenceStore
from backend.tests.test_report_evidence_loop import PAPER, TEXT, experiment_row

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "run1_report_baseline.json")


def _load_fixture() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _fixture_store_and_refs() -> tuple[ReportEvidenceStore, dict[str, int]]:
    fixture = _load_fixture()
    store = ReportEvidenceStore.from_dict(fixture["evidence_store"])
    refs = {key: i + 1 for i, key in enumerate(fixture["reference_keys"])}
    return store, refs


# ── 冻结基线：run1 的 8 条对象型 findings 完整保存 ───────────────────────────

def test_frozen_fixture_hash_is_pinned():
    fixture = _load_fixture()
    assert fixture["fixture_sha256"].startswith("b2030d8e5e0b8be9")
    assert len(fixture["submit_iteration_14"]["arguments"]["findings"]) == 8
    assert fixture["agent_loop_summary"]["stop_reason"] == "iteration_budget_exhausted"


def test_run1_object_findings_survive_normalization_with_metadata():
    """run1 第 14 轮的实际形状（{content, role, subquestion_id} + 内联 ev_*）：
    8 条全部接收、元数据保留、内联 ev 映射为 [n]——不再被静默丢弃。"""
    fixture = _load_fixture()
    store, refs = _fixture_store_and_refs()
    citation_map, _ = frozen_citation_map(store, refs)
    assert citation_map, "冻结证据库与引用映射必须能覆盖 run1 的内联 ev_*"
    payload = {"findings": fixture["submit_iteration_14"]["arguments"]["findings"]}
    subq_ids = {item["id"] for item in fixture["subquestions"]}
    result = normalize_draft(payload, citation_map=citation_map,
                             reference_count=len(refs), subquestion_ids=subq_ids)
    assert result["counts"]["received"] == 8
    # 旧缺陷复现锚点：v2 的 submit_report 会把这 8 条全部丢成 finding_count=0
    assert result["counts"]["accepted"] == 8
    assert result["counts"]["rejected"] == 0
    entries = result["sections"]["findings"]
    originals = fixture["submit_iteration_14"]["arguments"]["findings"]
    assert [entry["role"] for entry in entries] == [item["role"] for item in originals]
    assert {entry["subquestion_id"] for entry in entries} >= {"effect_comparison", "method_mechanism"}
    # 内联 [ev_…] 被程序映射为论文编号；claims 侧保留具体 evidence_ids
    with_ev = [entry for entry in entries if entry["evidence_ids"]]
    assert with_ev, "run1 的 findings 内联引用了真实 ev_*，应至少解析出一批"
    for entry in with_ev:
        assert "[ev_" not in entry["display_text"]
        assert all(f"[{citation_map[ev]}]" in entry["display_text"]
                   for ev in entry["evidence_ids"])


def test_run1_dropped_findings_regression_against_v2_behavior():
    """与旧 submit_report 行为对照：旧代码 isinstance(v, str) 过滤会把对象条目清零。"""
    fixture = _load_fixture()
    findings = fixture["submit_iteration_14"]["arguments"]["findings"]
    legacy_kept = [str(v).strip() for v in findings
                   if isinstance(v, str) and str(v).strip()]
    assert legacy_kept == [], "冻结材料里的 8 条 findings 全是 dict——旧实现保存 0 条"


# ── 草稿契约归一（纯函数）───────────────────────────────────────────────────

def _simple_store_and_refs():
    reader = DocumentEvidenceReader(PAPER["key"], TEXT)
    store = ReportEvidenceStore()
    store.add(evidence_type="experimental_comparisons",
              fact={k: experiment_row()[k] for k in (
                  "method", "baseline_method", "task", "dataset", "split", "metric",
                  "value", "baseline_value", "unit", "metric_direction", "hardware",
                  "batch_size", "input_size", "precision", "comparison_limit")},
              paper=PAPER, quote=experiment_row()["quote"],
              source_kind="full_text_excerpt", reader=reader)
    return store, {PAPER["key"]: 1}


def test_legacy_string_findings_keep_full_content():
    store, refs = _simple_store_and_refs()
    citation_map, _ = frozen_citation_map(store, refs)
    long_text = "旧字符串发现保持完整内容，" * 20 + "带编号引用 [1]。"
    result = normalize_draft({"summary": "一句话摘要 [1]。", "findings": [long_text, "   "]},
                             citation_map=citation_map, reference_count=1)
    assert result["counts"] == {"received": 3, "accepted": 2, "rejected": 1}
    assert result["sections"]["findings"][0]["text"] == long_text
    assert result["sections"]["findings"][0]["citation_status"] == "ok"
    assert result["sections"]["summary"][0]["citation_numbers"] == [1]


def test_unknown_object_and_invalid_types_report_field_path():
    store, refs = _simple_store_and_refs()
    citation_map, _ = frozen_citation_map(store, refs)
    result = normalize_draft(
        {"findings": [
            {"subquestion_id": "effect_comparison", "year": 2026},  # 无可识别正文字段
            42,                                                      # 非法类型
            {"content": "   "},                                      # 空 content
        ]},
        citation_map=citation_map, reference_count=1)
    assert result["counts"]["rejected"] == 3
    paths = {error["path"] for error in result["errors"]}
    assert paths == {"findings[0]", "findings[1]", "findings[2]"}
    assert all(error["code"] == "DRAFT_SCHEMA_INVALID" for error in result["errors"])
    assert any("content/text/claim" in error["detail"] for error in result["errors"])


def test_overlong_entry_is_rejected_not_truncated():
    store, refs = _simple_store_and_refs()
    citation_map, _ = frozen_citation_map(store, refs)
    huge = "超长正文" * 1000
    result = normalize_draft({"findings": [huge]}, citation_map=citation_map,
                             reference_count=1)
    assert result["counts"]["rejected"] == 1
    error = result["errors"][0]
    assert error["code"] == "DRAFT_SCHEMA_INVALID"
    assert "不会代为截断" in error["detail"]
    # 已接收条目不被截断：300 字内完整保留
    ok_text = "完整正文" + "细" * 200 + "结尾 [1]"
    kept = normalize_draft({"findings": [ok_text]}, citation_map=citation_map,
                           reference_count=1)
    assert kept["sections"]["findings"][0]["text"] == ok_text


def test_unknown_evidence_and_out_of_range_numbers_fail_explicitly():
    store, refs = _simple_store_and_refs()
    citation_map, _ = frozen_citation_map(store, refs)
    real_id = next(iter(citation_map))
    result = normalize_draft(
        {"findings": [
            {"content": f"valid [{real_id}]", "evidence_ids": [real_id]},
            {"content": "ghost [ev_000000000000000000dead0]"},
            {"content": "out of range [7]"},
        ]},
        citation_map=citation_map, reference_count=1)
    # 未知引用不得删除后冒充合法：条目保留但 citation_status=unknown 且计入错误
    statuses = [entry["citation_status"] for entry in result["sections"]["findings"]]
    assert statuses == ["ok", "unknown", "unknown"]
    assert result["unknown_evidence_ids"] == ["ev_000000000000000000dead0"]
    codes = [error["code"] for error in result["errors"]]
    assert codes.count("UNKNOWN_EVIDENCE_REFERENCE") == 2
    assert "[7]" in result["sections"]["findings"][2]["display_text"]


def test_missing_citation_facts_flagged_but_kept_as_pending():
    store, refs = _simple_store_and_refs()
    citation_map, _ = frozen_citation_map(store, refs)
    result = normalize_draft(
        {"findings": [
            {"text": "无引用的事实断言"},
            {"text": "本次未获取某篇全文", "statement_kind": "process_note"},
        ]},
        citation_map=citation_map, reference_count=1)
    assert result["missing_citation_count"] == 1
    assert result["sections"]["findings"][0]["citation_status"] == "missing"
    assert result["sections"]["findings"][1]["citation_status"] == "not_required"


def test_invalid_statement_kind_rejected():
    store, refs = _simple_store_and_refs()
    citation_map, _ = frozen_citation_map(store, refs)
    result = normalize_draft({"summary": [{"text": "x", "statement_kind": "guess"}]},
                             citation_map=citation_map, reference_count=1)
    assert result["counts"]["rejected"] == 1
    assert "statement_kind" in result["errors"][0]["detail"]


def test_citation_map_is_frozen_and_versioned():
    store, refs = _simple_store_and_refs()
    first, version1 = frozen_citation_map(store, refs)
    again, version2 = frozen_citation_map(store, refs)
    assert first == again and version1 == version2
    # 同一新增论文的证据进入映射 → 引用映射版本变化（旧核验不再视为该版本通过）
    store.add(evidence_type="metrics", fact={"aspect": "a", "metric": "m"},
              paper=PAPER, quote="a is measured by m here.",
              source_kind="abstract", reader=None)
    _, version3 = frozen_citation_map(store, refs)
    assert version3 != version1


# ── 证据包（有界、去重、分片、未读计数）─────────────────────────────────────

def test_evidence_pack_is_bounded_and_records_omitted():
    store = ReportEvidenceStore()
    papers = {}
    for index in range(40):
        key = f"p{index}"
        papers[key] = {"key": key, "title": f"Paper {index}"}
        store.add(evidence_type="metrics",
                  fact={"aspect": "accuracy", "metric": f"metric-{index}",
                        "description": "细节说明" * 30},
                  paper=papers[key], quote=f"metric-{index} is reported for accuracy.",
                  source_kind="abstract", reader=None)
    subquestions = [{"id": "effect_comparison", "label": "效果", "query": "accuracy"}]
    pack = build_evidence_pack("question", subquestions, store, papers,
                               budget_chars=6000)
    assert pack["has_factual_evidence"]
    assert pack["evidence_count"] < 40
    assert pack["omitted_count"] == 40 - pack["evidence_count"]
    assert pack["total_evidence_count"] == 40


def test_evidence_pack_splits_long_quotes_with_continuation_marker():
    store = ReportEvidenceStore()
    long_quote = ("很长的原文摘录。" * 400).strip()
    store.add(evidence_type="targeted_context", fact={"focus": "长证据"},
              paper=PAPER, quote=long_quote, source_kind="full_text_excerpt", reader=None,
              subquestion_ids=["effect_comparison"])
    pack = build_evidence_pack("q", [{"id": "effect_comparison", "label": "l", "query": "q"}],
                               store, {PAPER["key"]: PAPER})
    quotes = pack["evidence"][0]["quotes"]
    assert len(quotes) > 1
    assert any("片段 1/" in part for part in quotes)


def test_evidence_pack_recovers_relevant_late_record_and_balances_subquestions():
    store = ReportEvidenceStore()
    for i in range(15):
        store.add(evidence_type="targeted_context", fact={"focus": f"unrelated {i}"},
                  paper=PAPER, quote=f"Unrelated background statement {i} with detail.",
                  source_kind="abstract", reader=None,
                  subquestion_ids=["frontier"])
    key_record, _ = store.add(evidence_type="targeted_context",
                       fact={"focus": "frontier anomaly detection direction"},
                       paper=PAPER,
                       quote="Frontier anomaly detection direction is discussed here.",
                       source_kind="abstract", reader=None,
                       subquestion_ids=["frontier"])
    other_record, _ = store.add(evidence_type="targeted_context",
                         fact={"focus": "benchmark"}, paper=PAPER,
                         quote="Benchmark comparison is described in this paragraph.",
                         source_kind="abstract", reader=None,
                         subquestion_ids=["benchmark"])
    pack = build_evidence_pack(
        "anomaly detection frontier", [
            {"id": "frontier", "query": "frontier anomaly detection direction"},
            {"id": "benchmark", "query": "benchmark"}],
        store, {PAPER["key"]: PAPER}, per_subquestion=2, budget_chars=6000)
    ids = {row["evidence_id"] for row in pack["evidence"]}
    assert key_record["evidence_id"] in ids and other_record["evidence_id"] in ids


def test_fixed_statistics_are_programmatic_and_scoped_to_run_materials():
    store, _ = _simple_store_and_refs()
    pack = build_evidence_pack(
        "q", [{"id": "effect_comparison", "label": "效果", "query": "q"}],
        store, {PAPER["key"]: PAPER},
    )
    stats = rd.build_fixed_statistics(
        pack,
        [PAPER, {"key": "p2", "title": "Second", "year": 2024}],
        {"metrics": [{"id": 1}], "taxonomy": [{"id": 1}, {"id": 2}]},
    )
    assert stats["corpus"]["paper_count"] == 2
    assert stats["corpus"]["year_distribution"] == {str(PAPER["year"]): 1, "2024": 1}
    assert stats["evidence"]["record_count"] == 1
    assert stats["evidence"]["paper_count"] == 1
    assert stats["tables"] == {"metrics": 1, "taxonomy": 2}
    assert "不外推" in stats["interpretation_rule"]


# ── 规范正文与投影 ──────────────────────────────────────────────────────────

def test_canonical_segments_cover_narrative_and_experiment_table():
    store, refs = _simple_store_and_refs()
    citation_map, _ = frozen_citation_map(store, refs)
    real_id = next(iter(citation_map))
    sections = normalize_draft(
        {"summary": [{"text": f"summary [{real_id}]", "evidence_ids": [real_id]}],
         "findings": [{"text": f"finding [{real_id}]", "evidence_ids": [real_id]}]},
        citation_map=citation_map, reference_count=1)["sections"]
    tables = {"experimental_comparisons": [{
        **{k: experiment_row()[k] for k in (
            "method", "baseline_method", "task", "dataset", "split", "metric", "value",
            "baseline_value", "unit", "metric_direction", "hardware", "batch_size",
            "input_size", "precision", "comparison_limit")},
        "evidence": [{"paper_key": PAPER["key"], "quote": "q"}]}]}
    full_text, segments = canonical_segments(sections, tables, refs)
    ids = [segment["id"] for segment in segments]
    assert ids == ["summary-1", "findings-1", "exp-table-1"]
    assert "[1]" in segments[0]["text"]
    assert "SF-Mamba-B" in segments[2]["text"]
    assert full_text == "\n\n".join(segment["text"] for segment in segments)
    # 投影兼容旧渲染器：summary 是字符串、findings 是字符串数组
    legacy = project_legacy(sections)
    assert legacy["summary"] == "summary [1]"
    assert legacy["findings"] == ["finding [1]"]


# ── 分批核验包装 ────────────────────────────────────────────────────────────

def _sources():
    return [{"id": "P1", "title": "SF-Mamba", "abstract": "",
             "text": "SF-Mamba-B 84.4 3534 NVIDIA A100",
             "evidence_spans": [{"evidence_id": "ev_fixed", "quote": "SF-Mamba-B 84.4 3534"}]}]


def _completed_audit(answer, claims=1):
    return {
        "status": "completed", "unsupported_count": 0, "claim_count": claims,
        "supported_count": claims, "contradicted_count": 0, "insufficient_count": 0,
        "support_rate": 1.0,
        "claims": [{"claim": f"claim-{i}", "label": "supported"} for i in range(claims)],
        "limitations": [],
        "binding": {"text_hash": hashlib.sha256(answer.encode()).hexdigest(),
                    "evidence_version": "es_x"},
    }


@pytest.mark.asyncio
async def test_batch_audit_timeout_keeps_completed_batches_and_null_support_rate(monkeypatch):
    """超时批次不原样重跑全文：草稿和成功部分保留，support_rate=None，needs_review 语义。"""
    calls = []

    async def audit_answer(text, sources, cfg, *, evidence_version=None, **_kwargs):
        calls.append(text[:10])
        if "SLOW" in text:
            await asyncio.sleep(0.3)
        return _completed_audit(text)

    monkeypatch.setattr(evidence_audit, "audit_answer", audit_answer)
    segments = [
        {"id": "a", "text": "第一段是快批次断言。"},
        {"id": "b", "text": "SLOW 第二段是慢批次断言。"},
        {"id": "c", "text": "第三段也是快批次断言。"},
    ]
    merged = await evidence_audit.audit_report_text(
        "\n\n".join(s["text"] for s in segments), segments, _sources(),
        {"api_key": "test", "model": "fixture"}, evidence_version="es_x",
        citation_map_version="cm_x", batch_seconds=0.05, concurrency=2)
    assert merged["status"] == "partial"
    assert merged["support_rate"] is None
    assert merged["error"]["code"] == "AUDIT_INCOMPLETE"
    statuses = {row["segment_id"]: row["status"] for row in merged["coverage"]}
    # 三段在同一批：整批超时后只对半重试一次——[a,b] 仍超时、[c] 成功保留
    assert statuses == {"a": "failed", "b": "failed", "c": "audited"}
    assert any(claim["label"] == "supported" for claim in merged["claims"]), \
        "已完成批次的结果必须保留"
    slow_calls = [c for c in calls if "SLOW" in c]
    assert len(slow_calls) <= 2  # 整批一次 + 重试一次，不原样重跑全文


@pytest.mark.asyncio
async def test_batch_audit_narrowed_retry_recovers_single_failure(monkeypatch):
    """失败的批对半拆分重试一次；重试成功则整批结果保留。"""
    attempt = {"count": 0}

    async def audit_answer(text, sources, cfg, *, evidence_version=None, **_kwargs):
        attempt["count"] += 1
        if attempt["count"] == 1:
            raise RuntimeError("transient judge failure")
        return _completed_audit(text)

    monkeypatch.setattr(evidence_audit, "audit_answer", audit_answer)
    segments = [{"id": "a", "text": "断言一。"}, {"id": "b", "text": "断言二。"}]
    merged = await evidence_audit.audit_report_text(
        "断言一。\n\n断言二。", segments, _sources(), {"api_key": "test", "model": "f"},
        evidence_version="es_x", citation_map_version="cm_x")
    assert attempt["count"] == 3  # 首批 1 次 + 对半重试 2 次
    assert merged["status"] == "completed"
    assert merged["support_rate"] == 1.0


@pytest.mark.asyncio
async def test_batch_audit_binding_includes_citation_map_version(monkeypatch):
    async def audit_answer(text, sources, cfg, *, evidence_version=None, **_kwargs):
        return _completed_audit(text)

    monkeypatch.setattr(evidence_audit, "audit_answer", audit_answer)
    segments = [{"id": "a", "text": "断言。"}]
    merged = await evidence_audit.audit_report_text(
        "断言。", segments, _sources(), {"api_key": "test", "model": "f"},
        evidence_version="es_1", citation_map_version="cm_1")
    binding = merged["binding"]
    assert binding["citation_map_version"] == "cm_1"
    assert binding["audit_config"]["version"] == evidence_audit.BATCH_AUDIT_VERSION
    assert binding["scope"] == ["a"]
    answer = "断言。"
    assert audit_binding_valid(merged, answer, "es_1", "cm_1")
    # 引用映射变化 → 旧核验不再视为该版本通过
    assert not audit_binding_valid(merged, answer, "es_1", "cm_2")
    assert not audit_binding_valid(merged, answer + "改", "es_1", "cm_1")


@pytest.mark.asyncio
async def test_batch_audit_empty_segments_return_no_claims():
    merged = await evidence_audit.audit_report_text(
        "", [], _sources(), {"api_key": "test", "model": "f"}, evidence_version="es_1")
    assert merged["status"] == "no_claims"
    assert merged["support_rate"] is None


@pytest.mark.asyncio
async def test_batch_audit_deadline_uses_real_batch_count(monkeypatch):
    """全局核验预算按真实分批数计算：长段落让分批器产出远多于 len(segments)/3
    的批次时，估算口径会让 deadline 提前耗尽、后段白判 AUDIT_DEADLINE。"""
    calls = {"n": 0}

    async def audit_answer(text, sources, cfg, *, evidence_version=None, **_kwargs):
        calls["n"] += 1
        await asyncio.sleep(0.2)
        return _completed_audit(text)

    monkeypatch.setattr(evidence_audit, "audit_answer", audit_answer)
    # 12 个长段（各超 _BATCH_CHAR_BUDGET）→ 分批器每段一批；估算口径只有 4 批。
    # 估算 deadline=2 波×0.3×1.5=0.9s < 实际 6 波 ≈1.2s → 后段撞 AUDIT_DEADLINE；
    # 真实批数 deadline=6 波×0.3×1.5=2.7s → 全部完成
    segments = [{"id": f"s{i}", "text": f"第 {i} 段长断言。" + "证据正文 " * 800}
                for i in range(12)]
    monkeypatch.setattr(evidence_audit, "REPORT_AUDIT_TOTAL_SECONDS", 0.0)
    merged = await evidence_audit.audit_report_text(
        "\n\n".join(s["text"] for s in segments), segments, _sources(),
        {"api_key": "test", "model": "f"}, evidence_version="es_x",
        citation_map_version="cm_x", batch_seconds=0.3, concurrency=2)
    assert calls["n"] == 12  # 估算口径下后半批次会撞 AUDIT_DEADLINE 不发起评审
    assert merged["status"] == "completed"
    assert merged["support_rate"] == 1.0


# ── 交付编排：审计超时 → partial/needs_review，草稿保留 ─────────────────────

@pytest.mark.asyncio
async def test_draft_generation_uses_one_attempt_per_logical_call_and_no_thinking(monkeypatch):
    """run5 回归：整稿解析失败后分五节恢复，底层仍不得暗重试。

    额度按材料规模计算并关闭思考，避免 reasoning 吃完正文空间。
    """
    store, refs = _simple_store_and_refs()
    citation_map, _ = frozen_citation_map(store, refs)
    evidence_id = next(iter(citation_map))
    calls: list[dict] = []

    async def llm(_cfg, system, _user, **kwargs):
        calls.append({"system": system, **kwargs})
        if system == rd._STATISTICS_WRITER_SYSTEM:
            return {"statistics_summary": [{"text": "1 篇证据论文。", "scope": "evidence"}]}
        if system == rd._EVIDENCE_OVERVIEW_SYSTEM:
            return {"overview": [], "gaps": []}
        if system == rd._DRAFT_SYSTEM:
            raise json.JSONDecodeError("truncated", '"summary": ["unfinished', 12)
        return {
            "summary": [{"text": f"Recovered final draft. [{evidence_id}]",
                         "evidence_ids": [evidence_id]}],
            "findings": [], "limitations": [], "gaps": [], "novelty": [],
        }

    async def timeout_audit(*_args, **_kwargs):
        return {
            "status": "partial", "unsupported_count": 0, "claim_count": 0,
            "supported_count": 0, "contradicted_count": 0,
            "insufficient_count": 0, "support_rate": None, "claims": [],
            "coverage": [{"segment_id": "summary-1", "status": "failed",
                          "claim_count": 0}],
            "error": {"code": "AUDIT_TIMEOUT", "detail": "timeout"},
            "binding": None,
        }

    monkeypatch.setattr(rd, "_llm_json", llm)
    monkeypatch.setattr(rd, "audit_report_text", timeout_audit)
    from backend.services.deep_research.document_evidence import report_subquestions
    result = await rd.deliver_report(
        question="compare efficiency",
        subquestions=report_subquestions("compare efficiency"),
        evidence_store=store, papers=[PAPER], refs=refs, tables={},
        synthesis=None, cfg={"api_key": "test", "model": "deepseek-flash",
                             "thinking_effort": "medium"},
    )

    assert len(calls) == 8  # 两个固定角色 + 一次整稿 + 五个独立章节
    assert rd.DRAFT_MIN_TOKENS <= calls[2]["max_tokens"] <= rd.DRAFT_HARD_MAX_TOKENS
    assert all(call["max_retries"] == 0 for call in calls)
    assert all(call["thinking_effort"] == "off" for call in calls)
    assert result["delivery"]["narrative_source"] == "final_draft"
    assert result["delivery"]["attempts"]["repair_quota_used"] is True
    assert result["delivery"]["attempts"]["repair_calls"] == 1
    assert result["delivery"]["attempts"]["section_calls"] == 5


def test_adaptive_draft_plan_scales_with_evidence_and_honors_config_cap():
    compact = rd.adaptive_draft_plan({"evidence_count": 8}, {"max_tokens": 16384})
    standard = rd.adaptive_draft_plan({"evidence_count": 40}, {"max_tokens": 16384})
    extended = rd.adaptive_draft_plan({"evidence_count": 100}, {"max_tokens": 16384})

    assert compact["mode"] == "compact"
    assert standard["mode"] == "standard"
    assert extended["mode"] == "extended"
    assert compact["initial_max_tokens"] < standard["initial_max_tokens"] < extended["initial_max_tokens"]
    assert extended["sections"]["findings"]["max_entries"] == 16
    capped = rd.adaptive_draft_plan({"evidence_count": 100}, {"max_tokens": 5000})
    assert capped["initial_max_tokens"] == 5000
    assert all(value <= 5000 for value in capped["section_max_tokens"].values())


@pytest.mark.asyncio
async def test_valid_but_length_stopped_json_recovers_only_missing_sections(monkeypatch):
    store, refs = _simple_store_and_refs()
    citation_map, _ = frozen_citation_map(store, refs)
    evidence_id = next(iter(citation_map))
    requested: list[str] = []

    async def llm(_cfg, _system, user, **kwargs):
        payload = json.loads(user)
        if _system == rd._STATISTICS_WRITER_SYSTEM:
            return {"statistics_summary": []}
        if _system == rd._EVIDENCE_OVERVIEW_SYSTEM:
            return {"overview": [], "gaps": []}
        section = payload.get("requested_section")
        if section:
            requested.append(section)
            return {"section": section, "entries": []}
        kwargs["metadata_out"].update({"finish_reason": "length", "usage": {}})
        # JSON 本身闭合，但后续四个章节因长度上限未生成。
        return {"summary": [{"text": f"Kept summary. [{evidence_id}]",
                              "evidence_ids": [evidence_id]}]}

    async def timeout_audit(*_args, **_kwargs):
        return {"status": "partial", "unsupported_count": 0, "claim_count": 0,
                "supported_count": 0, "contradicted_count": 0,
                "insufficient_count": 0, "support_rate": None, "claims": [],
                "coverage": [], "binding": None,
                "error": {"code": "AUDIT_TIMEOUT", "detail": "timeout"}}

    monkeypatch.setattr(rd, "_llm_json", llm)
    monkeypatch.setattr(rd, "audit_report_text", timeout_audit)
    from backend.services.deep_research.document_evidence import report_subquestions
    result = await rd.deliver_report(
        question="q", subquestions=report_subquestions("q"),
        evidence_store=store, papers=[PAPER], refs=refs, tables={},
        synthesis=None, cfg={"api_key": "test", "model": "fixture", "max_tokens": 9000})

    assert requested == ["findings", "limitations", "gaps", "novelty"]
    assert result["delivery"]["attempts"]["section_calls"] == 4
    assert result["delivery"]["narrative_source"] == "final_draft"


@pytest.mark.asyncio
async def test_deliver_report_stops_after_writer_without_running_audit(monkeypatch):
    store, refs = _simple_store_and_refs()
    citation_map, _ = frozen_citation_map(store, refs)
    real_id = next(iter(citation_map))

    async def llm(_cfg, system, user, **_kwargs):
        payload = json.loads(user)
        if system == rd._STATISTICS_WRITER_SYSTEM:
            assert payload["fixed_statistics"]["corpus"]["paper_count"] == 1
            return {"statistics_summary": [{"text": "本次材料 1 篇。", "scope": "corpus"}]}
        if system == rd._EVIDENCE_OVERVIEW_SYSTEM:
            assert payload["evidence"], "固定概论必须读取证据包"
            return {"overview": [{"dimension": "methods", "text": "方法证据概论。",
                                   "evidence_ids": [real_id]}], "gaps": []}
        assert payload["evidence"], "证据包必须随写作输入提供"
        assert payload["statistics_summary"]
        assert payload["evidence_overview"][0]["evidence_ids"] == [real_id]
        return {
            "summary": [{"text": f"Scoped summary. [{real_id}]", "evidence_ids": [real_id]}],
            "findings": [{"content": f"Finding kept after timeout. [{real_id}]",
                          "subquestion_id": "effect_comparison", "role": "mainline"}],
            "limitations": [], "gaps": [], "novelty": [],
        }

    async def timeout_audit(*_args, **_kwargs):
        raise AssertionError("主笔完成后不应启动真实性核验")

    monkeypatch.setattr(rd, "_llm_json", llm)
    monkeypatch.setattr(rd, "audit_report_text", timeout_audit)
    from backend.services.deep_research.document_evidence import report_subquestions
    subquestions = report_subquestions("compare efficiency")
    result = await rd.deliver_report(
        question="compare efficiency", subquestions=subquestions,
        evidence_store=store, papers=[PAPER], refs=refs, tables={},
        synthesis=None, cfg={"api_key": "test", "model": "fixture"})
    # 主笔正文直接冻结，不受已断开的审计函数影响。
    assert result["delivery"]["narrative_source"] == "final_draft"
    assert result["delivery"]["delivery_status"] == "ready"
    assert result["quality_status"] == "unchecked"
    assert result["audit"]["status"] == "not_requested"
    assert "Finding kept after timeout." in result["projections"]["findings"][0]
    assert result["delivery"]["error_code"] is None
    assert result["delivery"]["attempts"]["audit_batches"] == 0
    assert result["delivery"]["attempts"]["revision_calls"] == 0
    assert result["role_artifacts"]["pipeline_version"] == "writer_roles_v1"
    assert result["role_artifacts"]["evidence_overview"][0]["dimension"] == "methods"


# ── explorer：重生成失败保留旧成功版本 ──────────────────────────────────────

def test_rebuild_failure_keeps_previous_selected_revision(client, engine, monkeypatch):
    from backend.models.exploration import ExplorationRun
    from backend.services.deep_research import explorer
    from backend.services.database import get_engine as _get_engine

    old_report = {
        "title": "old-final", "markdown": "# old", "coverage": {},
        "quality_status": "internally_checked",
        "delivery": {"delivery_status": "ready", "narrative_source": "final_draft"},
        "method": {"final_audit": {"status": "completed", "unsupported_count": 0}},
    }
    with Session(_get_engine()) as session:
        run = ExplorationRun(question="q", status="done",
                             result={"survey_report": old_report, "papers": []})
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id
    explorer._persist_legacy_report_revision(run_id, old_report)

    failed_report = {
        "title": "new-failed", "markdown": "# new", "coverage": {},
        "quality_status": "unchecked",
        "delivery": {"delivery_status": "unavailable", "narrative_source": "none",
                     "error_code": "DRAFT_WRITE_FAILED"},
        "method": {"final_audit": {"status": "not_started"}},
    }

    async def fake_build(*_args, **_kwargs):
        return failed_report

    monkeypatch.setattr(explorer, "build_survey_report", fake_build)
    asyncio.run(explorer.rebuild_survey_report(run_id))

    with Session(_get_engine()) as session:
        run = session.get(ExplorationRun, run_id)
        # 旧版保持选定；页面与导出继续读取旧报告；新尝试清楚展示失败
        assert run.result["survey_report"]["title"] == "old-final"
        assert "保留上一版本" in run.result["report_error"]
    revisions = client.get(f"/api/exploration/runs/{run_id}/revisions").json()["items"]
    selected = [row for row in revisions if row["is_selected"]]
    assert len(selected) == 1 and selected[0]["narrative_source"] == "final_draft"
    unselected = [row for row in revisions if not row["is_selected"]]
    assert any(row["narrative_source"] == "none" for row in unselected)


def test_rebuild_success_replaces_previous_revision(client, engine, monkeypatch):
    from backend.models.exploration import ExplorationRun
    from backend.services.deep_research import explorer
    from backend.services.database import get_engine as _get_engine

    old_report = {
        "title": "old-final", "markdown": "# old", "coverage": {},
        "quality_status": "internally_checked",
        "delivery": {"delivery_status": "ready", "narrative_source": "final_draft"},
        "method": {"final_audit": {"status": "completed", "unsupported_count": 0}},
    }
    with Session(_get_engine()) as session:
        run = ExplorationRun(question="q", status="done",
                             result={"survey_report": old_report, "papers": []})
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id
    explorer._persist_legacy_report_revision(run_id, old_report)

    new_report = {
        "title": "new-final", "markdown": "# new", "coverage": {},
        "quality_status": "internally_checked",
        "delivery": {"delivery_status": "ready", "narrative_source": "final_draft"},
        "method": {"final_audit": {"status": "completed", "unsupported_count": 0}},
    }

    async def fake_build(*_args, **_kwargs):
        return new_report

    monkeypatch.setattr(explorer, "build_survey_report", fake_build)
    asyncio.run(explorer.rebuild_survey_report(run_id))
    with Session(_get_engine()) as session:
        run = session.get(ExplorationRun, run_id)
        assert run.result["survey_report"]["title"] == "new-final"
        assert "report_error" not in run.result
    revisions = client.get(f"/api/exploration/runs/{run_id}/revisions").json()["items"]
    assert sum(row["is_selected"] for row in revisions) == 1
    assert revisions[0]["revision_id"]


def test_rebuild_with_frozen_evidence_skips_selection_and_extraction(engine, monkeypatch):
    """完成态 checkpoint 已清理时，重新生成只重做写作/核验。"""
    from backend.models.exploration import ExplorationRun
    from backend.services.deep_research import explorer
    from backend.services.database import get_engine as _get_engine

    preliminary = {
        "title": "q", "markdown": "# preliminary", "coverage": {},
        "tables": {}, "subquestions": [],
        "evidence_store": {"records": [{"evidence_id": "ev_saved"}]},
        "delivery": {"delivery_status": "partial", "narrative_source": "preliminary",
                     "error_code": "DRAFT_WRITE_FAILED"},
        "method": {"final_audit": {"status": "not_started"}},
    }
    with Session(_get_engine()) as session:
        run = ExplorationRun(
            question="q", status="done", depth="lite",
            result={"survey_report": preliminary, "papers": [], "metrics": {}},
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    final_report = {
        **preliminary, "markdown": "# final", "quality_status": "internally_checked",
        "delivery": {"delivery_status": "ready", "narrative_source": "final_draft"},
        "method": {"final_audit": {"status": "completed", "unsupported_count": 0}},
    }
    seen = {"redeliver": 0}

    async def fake_redeliver(*_args, **_kwargs):
        seen["redeliver"] += 1
        return final_report

    async def forbidden_full_build(*_args, **_kwargs):
        raise AssertionError("frozen evidence must not rerun report extraction")

    monkeypatch.setattr(explorer, "redeliver_survey_report", fake_redeliver)
    monkeypatch.setattr(explorer, "build_survey_report", forbidden_full_build)
    asyncio.run(explorer.rebuild_survey_report(run_id))

    assert seen["redeliver"] == 1
    with Session(_get_engine()) as session:
        run = session.get(ExplorationRun, run_id)
        assert run.result["survey_report"]["delivery"]["narrative_source"] == "final_draft"
