"""Read-only run11 diagnostic for report evidence loss and targeted rereading.

This is a fixed diagnostic, not a blind test and not an accuracy benchmark.  It
does not search, call an LLM, mutate the run, or overwrite the saved report.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

from backend.services.deep_research.document_evidence import DocumentEvidenceReader, normalize_text
from backend.services.deep_research.report_evidence import ReportEvidenceStore


CHECKS = [
    {"id": "classification_accuracy", "query": "MambaVision-B SF-Mamba-B Acc ImageNet-1K",
     "expected": ["MambaVision-B", "84.2", "SF-Mamba-B", "84.4"], "subquestion": "effect_comparison"},
    {"id": "throughput_comparison", "query": "MambaVision-B SF-Mamba-B img/s throughput",
     "expected": ["2974", "3534", "img/s"], "subquestion": "runtime_efficiency"},
    {"id": "hardware_and_batch", "query": "throughput NVIDIA A100 batch size 128",
     "expected": ["NVIDIA A100", "batch size of 128"], "subquestion": "runtime_efficiency"},
    {"id": "table_header_context", "query": "Model Mixer Params MACs img/s Acc",
     "expected": ["Model", "img/s", "Acc. (%)"], "subquestion": "effect_comparison"},
    {"id": "comparability_limit", "query": "protocols ensure fair comparisons",
     "expected": ["protocols", "ensure fair comparisons"], "subquestion": "limitations",
     "interpretation": "论文声称沿用既有协议；系统仍不得把跨论文结果默认标成公平对比。"},
    {"id": "missing_throughput_precision", "query": "throughput NVIDIA A100 batch size 128",
     "expected": ["Throughput", "NVIDIA A100", "batch size of 128"],
     "expected_absent_in_context": ["FP16", "throughput precision"],
     "subquestion": "runtime_efficiency",
     "interpretation": "定位段落没有说明吞吐量测量精度；其他训练段落出现 AMP 不能自动补到该测量。"},
]


def _load(db_path: Path, run_id: int) -> tuple[dict, int]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = connection.execute("SELECT id, result FROM exploration_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            rows = connection.execute("SELECT id, result FROM exploration_runs ORDER BY id").fetchall()
            if len(rows) != 1:
                raise SystemExit(f"run {run_id} not found")
            row = rows[0]
        return json.loads(row[1] or "{}"), int(row[0])
    finally:
        connection.close()


def diagnose(db_path: Path, run_id: int) -> dict:
    started = time.monotonic()
    result, actual_run_id = _load(db_path, run_id)
    report = result.get("survey_report") or {}
    paper = next((item for item in result.get("papers") or []
                  if item.get("key") == "arxiv:2603.16423"), None)
    if paper is None or not paper.get("source_text"):
        raise SystemExit("fixed SF-Mamba full-text material is unavailable")
    reader = DocumentEvidenceReader(paper["key"], paper["source_text"])
    old_report_text = normalize_text(json.dumps(
        {"summary": report.get("summary"), "findings": report.get("findings"),
         "gaps": report.get("gaps"), "tables": report.get("tables")},
        ensure_ascii=False,
    )).casefold()
    store = ReportEvidenceStore()
    checks = []
    for check in CHECKS:
        found = reader.search(check["query"], limit=3)
        contexts = [reader.read_context(item["location_id"], before=1, after=1)
                    for item in found["items"]]
        joined = normalize_text(" ".join(item["text"] for item in contexts))
        expected_found = {value: value.casefold() in joined.casefold() for value in check["expected"]}
        expected_absent = {
            value: value.casefold() not in joined.casefold()
            for value in check.get("expected_absent_in_context", [])
        }
        evidence_ids = []
        for context in contexts:
            record, created = store.add_context(
                paper=paper, context=context, subquestion_id=check["subquestion"],
                query=check["query"], reader=reader,
            )
            if created:
                evidence_ids.append(record["evidence_id"])
        checks.append({
            **check,
            "old_report_contains_all_expected": all(value.casefold() in old_report_text for value in check["expected"]),
            "targeted_reader_contains_all_expected": all(expected_found.values()),
            "expected_found": expected_found,
            "expected_absent_confirmed": expected_absent,
            "locations": [{key: item.get(key) for key in ("location_id", "page", "section", "table", "location_status")}
                          for item in found["items"]],
            "new_evidence_ids": evidence_ids,
        })
    return {
        "diagnostic": "run11_fixed_material_report_evidence",
        "requested_run_id": run_id,
        "actual_local_run_id": actual_run_id,
        "paper_key": paper["key"],
        "paper_title": paper.get("title"),
        "source_chars": len(paper["source_text"]),
        "old_report_version": report.get("version", 1),
        "old_experimental_comparison_rows": len((report.get("tables") or {}).get("experimental_comparisons") or []),
        "checks": checks,
        "shared_store": {"count": store.to_dict()["count"],
                         "evidence_version": store.evidence_version},
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "limitations": [
            "固定诊断，不是独立盲测。",
            "只验证本地全文定位、稳定保存和旧报告遗漏；不调用模型，不测语义提取准确率。",
            "缺失字段必须按具体实验范围解释；本文其他章节出现设置不代表适用于吞吐量测量。",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=int, default=11)
    parser.add_argument("--db", type=Path, default=Path("data/essay_read.db"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = diagnose(args.db, args.run_id)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
