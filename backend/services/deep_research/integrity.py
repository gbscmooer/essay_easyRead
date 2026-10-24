"""检索与分析完整性（八阶段 workflow）：把散落在各阶段审计里的失败与不足汇总给用户。

纯函数、只读 result：数据源可用性、检索拆解回退、召回停止原因、精读覆盖、
综合与报告阶段失败、判定规则复核。等级由确定性规则给出，不调用模型。
"""

from __future__ import annotations

import re
from collections import Counter

LEVELS = ("complete", "partial", "insufficient", "retrieval_miss")
LEVEL_LABELS = {
    "complete": "流程完整",
    "partial": "部分完成",
    "insufficient": "资料不足",
    "retrieval_miss": "检索未命中",
}
_STAGE_LABELS = {"screen": "筛选", "select": "选材", "extract": "证据提取", "synthesize": "综合", "report": "报告",
                 "recall": "召回", "protocol": "检索规划", "preflight": "数据源预检"}
_MIN_EXAMINED_SHARE = 0.5   # 精读成功篇数低于选中篇数该比例时视为分析不完整
_RETRY_MARKERS = ("CONTEXT_SHRUNK_RETRY", "ONE_SHOT_RETRY")   # 重试记录，不是最终失败


def _failure_cause(failure: dict) -> str:
    """提取循环把模型错误包成 ValueError；展示真正的原因（如 model_error:TimeoutError）。"""
    detail = str(failure.get("detail") or "")
    match = re.search(r"model_error:([A-Za-z_]+)", detail)
    if match:
        return "模型调用超时" if match.group(1) == "TimeoutError" else f"模型错误 {match.group(1)}"
    return str(failure.get("code") or "未知")


_DELIVERY_REASONS = {
    "UNKNOWN_EVIDENCE_REFERENCE": "终稿引用了证据包之外的编号",
    "DRAFT_SECTION_INCOMPLETE": "终稿有章节未生成",
    "DRAFT_SCHEMA_INVALID": "终稿结构不合规",
}


def _review_reason(report: dict) -> str:
    """needs_review 的具体原因：缺引用条目逐条点出，便于用户定位。"""
    code = (report.get("delivery") or {}).get("error_code")
    if code == "CITATION_MISSING":
        uncited = [entry.get("text") or "" for entries in (report.get("narrative") or {}).values()
                   if isinstance(entries, list) for entry in entries
                   if isinstance(entry, dict) and entry.get("citation_status") == "missing"]
        if uncited:
            sample = uncited[0][:40] + ("…" if len(uncited[0]) > 40 else "")
            return f"终稿有 {len(uncited)} 条陈述缺少证据引用（如「{sample}」），需人工复核。"
    reason = _DELIVERY_REASONS.get(code or "")
    return f"{reason}，需人工复核。" if reason else "报告结构或引用契约不完整，需人工复核。"


def build_integrity(result: dict) -> dict:
    items: list[dict] = []
    level = "complete"

    def add(kind: str, severity: str, message: str) -> None:
        nonlocal level
        items.append({"kind": kind, "severity": severity, "message": message})
        if LEVELS.index(severity) > LEVELS.index(level):
            level = severity

    sources = result.get("sources") or {}
    for row in sources.get("preflight") or []:
        status = row.get("status")
        if status not in (None, "ok", "unconfigured"):
            add("source", "partial",
                f"数据源 {row.get('name') or '未知'} 不可用（{row.get('detail') or status}），本次检索未覆盖该来源。")

    decompose = result.get("decompose") or {}
    if decompose.get("fallback"):
        add("decompose", "partial", "检索式拆解未完成，使用原句回退检索，查全率可能明显偏低。")

    recall = sources.get("recall") or {}
    stop = recall.get("stop_reason")
    if stop in ("planner_unavailable", "planner_error"):
        add("recall", "partial", f"召回规划模型调用失败（{stop}），后续轮次未执行。")
    elif stop in ("attempt_budget", "round_budget", "planner_stop"):
        # 窄方向达不到目标篇数是常态：列出但不降低完整性等级
        add("recall", "complete",
            f"召回在 {recall.get('effective_rounds', '?')} 轮后停止（{stop}），"
            f"未达到目标 {recall.get('target', '未知')} 篇。")

    reuse = recall.get("reuse") or {}
    if reuse.get("parent_run_id"):
        if reuse.get("skipped"):
            add("reuse", "partial", f"未能复用研究 #{reuse['parent_run_id']} 的材料（未配置模型，无法按新问题重新筛选）。")
        else:
            add("reuse", "complete",
                f"复用研究 #{reuse['parent_run_id']} 的 {reuse.get('reused_in', 0)} 篇材料，"
                f"按修正后的问题重新筛选保留 {reuse.get('kept', 0)} 篇。")

    for row in result.get("interventions") or []:
        parts = []
        if row.get("excluded"):
            parts.append(f"剔除 {len(row['excluded'])} 篇文献")
        if row.get("queries"):
            parts.append("追加检索式「" + "」「".join(row["queries"]) + "」")
        if parts:
            stage = _STAGE_LABELS.get(row.get("stage") or "", row.get("stage") or "暂停")
            redo = "、".join(_STAGE_LABELS.get(x, x) for x in row.get("invalidated_stages") or [])
            add("intervention", "complete", f"用户在「{stage}」阶段暂停后" + "、".join(parts)
                + (f"，并重新执行{redo}。" if redo else "。"))
    for row in recall.get("user_supplements") or []:
        add("intervention", "complete",
            f"追加检索式补搜命中 {row.get('hits', 0)} 篇，去重后新增 {row.get('added', 0)} 篇。")

    total = int(((result.get("metrics") or {}).get("total")) or len(result.get("papers") or []))
    synthesis = result.get("synthesis") or {}
    stats = synthesis.get("stats") or {}
    coverage = stats.get("concept_coverage") or {}
    if total == 0:
        add("recall", "retrieval_miss", "正式召回 0 篇，没有可引用材料；这不能证明该方向不存在相关工作。")
    elif coverage.get("missing"):
        add("recall", "retrieval_miss",
            f"召回 {total} 篇，但没有任何论文涉及「{'、'.join(coverage['missing'])}」，"
            "材料与研究对象不对应，结论按检索未命中处理。")
    if total and coverage.get("missing_derived"):
        add("recall", "complete",
            f"拆解延伸出的子方向「{'、'.join(coverage['missing_derived'])}」在材料中没有命中；"
            "它不是问题原有的研究对象，不影响判定，但相关内容未被覆盖。")

    report = result.get("survey_report") or {}
    extract_cov = report.get("coverage") or (result.get("report_extract") or {}).get("coverage") or {}
    selected = int(extract_cov.get("selected") or 0)
    examined = int(extract_cov.get("examined") or 0)
    failed = int(extract_cov.get("failed") or 0)
    if selected:
        failures = ((((result.get("report_extract") or {}).get("diagnostics") or {})
                     .get("extract") or {}).get("failures") or [])
        codes = Counter(_failure_cause(f) for f in failures if f.get("code") not in _RETRY_MARKERS)
        reason = "；主要原因：" + "、".join(f"{c}×{n}" for c, n in codes.most_common(3)) if codes else ""
        msg = (f"精读选中 {selected} 篇，成功提取 {examined} 篇（全文 {extract_cov.get('full_text', 0)}、"
               f"仅摘要 {extract_cov.get('abstract_only', 0)}），失败 {failed} 篇{reason}。")
        if examined == 0:
            add("extract", "insufficient", msg + "结论只能依据标题与摘要。")
        elif examined < selected * _MIN_EXAMINED_SHARE:
            add("extract", "partial", msg + "表格与结论覆盖不完整。")
        elif failed:
            add("extract", "partial", msg)
    if int(extract_cov.get("skipped_frozen") or 0):
        add("extract", "partial", f"时间预算耗尽，{extract_cov['skipped_frozen']} 篇已选材料未做证据提取。")

    verdict = synthesis.get("verdict")
    if total and not verdict and level != "retrieval_miss":
        add("synthesize", "insufficient", "未形成查新判定（材料不足或综合阶段未完成）。")
    elif synthesis.get("partial"):
        add("synthesize", "partial", "综合阶段未完全完成，部分分析内容可能缺失。")
    for guard in stats.get("verdict_guards") or []:
        if guard.get("rule") == "maturity_early_evidence":
            add("verdict", "partial",
                f"程序复核把判定从「{guard['from']}」调整为「{guard['to']}」：引用证据中早于 "
                f"{guard['cutoff_year']} 年的仅 {guard['early_evidence']} 篇。")

    if result.get("report_error"):
        add("report", "partial", str(result["report_error"]))
    elif report.get("quality_status") == "needs_review":
        add("report", "partial", _review_reason(report))

    return {"level": level, "label": LEVEL_LABELS[level], "items": items}


def render_integrity_markdown(integrity: dict | None) -> list[str]:
    if not integrity:
        return []
    lines = ["## 检索与分析完整性", "", f"状态：**{integrity.get('label') or integrity.get('level')}**", ""]
    items = integrity.get("items") or []
    lines += [f"- {item['message']}" for item in items] or ["- 各阶段均已完成，未发现数据源失败或提取缺失。"]
    return lines + [""]
