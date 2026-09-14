"""Generate a standalone report from saved papers without recall or DB mutation."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from pathlib import Path

from backend.services.ai_config import get_analysis_config
from backend.services.deep_research.survey_report import build_survey_report, render_html
from backend.services.llm import start_token_ledger


def _load(db_path: Path, run_id: int) -> tuple[dict, dict, int]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT id, question, depth, result FROM exploration_runs WHERE id=?", (run_id,)
        ).fetchone()
        if row is None:
            rows = connection.execute(
                "SELECT id, question, depth, result FROM exploration_runs ORDER BY id"
            ).fetchall()
            if len(rows) != 1:
                raise SystemExit(f"run {run_id} not found")
            row = rows[0]
        return {"question": row[1], "depth": row[2]}, json.loads(row[3] or "{}"), int(row[0])
    finally:
        connection.close()


async def generate(db_path: Path, run_id: int, paper_keys: list[str]) -> tuple[dict, int]:
    run, result, actual_id = _load(db_path, run_id)
    selected = [paper for paper in result.get("papers") or [] if paper.get("key") in paper_keys]
    if not selected:
        raise SystemExit("none of the requested saved papers has material")
    start_token_ledger()
    report = await build_survey_report(
        run["question"], selected, result.get("metrics") or {}, result.get("synthesis"),
        get_analysis_config(), depth="quick", fetch_full_text=False,
        search_context={"decompose": result.get("decompose") or {},
                        "provider": result.get("provider") or "saved_run",
                        "preflight": [], "recall": {}, "agent_loops": {}},
        resume_state={"selected_keys": [paper["key"] for paper in selected], "extractions": {}},
    )
    report["diagnostic_source"] = {
        "requested_run_id": run_id, "actual_local_run_id": actual_id,
        "paper_keys": [paper["key"] for paper in selected],
        "online_recall": False, "database_mutated": False,
    }
    return report, actual_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=int, default=11)
    parser.add_argument("--paper-key", action="append", default=["arxiv:2603.16423"])
    parser.add_argument("--db", type=Path, default=Path("data/essay_read.db"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report, _ = asyncio.run(generate(args.db, args.run_id, list(dict.fromkeys(args.paper_key))))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".html").write_text(render_html(report), encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"), "quality_status": report.get("quality_status"),
        "experimental_rows": len((report.get("tables") or {}).get("experimental_comparisons") or []),
        "evidence_count": (report.get("evidence_store") or {}).get("count"),
        "final_audit": ((report.get("method") or {}).get("final_audit") or {}).get("status"),
        "token_usage": (report.get("method") or {}).get("token_usage"),
        "diagnostics": (report.get("method") or {}).get("diagnostics"),
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
