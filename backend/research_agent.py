"""CLI entry for the research agent harness.

Examples:
    python -m backend.research_agent workflows
    python -m backend.research_agent run --workflow literature_brief --workspace-id 1 --goal "RAG evidence"
    python -m backend.research_agent run --workflow survey_path --workspace-id 1 --goal "survey draft" --out /tmp/out.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def _print_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


async def _cmd_workflows() -> int:
    from backend.services.research_harness import list_research_workflows

    _print_json({"workflows": list_research_workflows()})
    return 0


async def _cmd_run(args: argparse.Namespace) -> int:
    from backend.services.research_harness import run_research_workflow

    result = await run_research_workflow(
        workflow=args.workflow,
        goal=args.goal,
        workspace_id=args.workspace_id,
    )
    if args.out:
        out_path = Path(args.out)
        markdown = (result.get("artifacts") or {}).get("markdown") or ""
        out_path.write_text(markdown, encoding="utf-8")
        print(f"Wrote markdown to {out_path}", file=sys.stderr)
    if args.journal_out:
        Path(args.journal_out).write_text(
            json.dumps(result.get("journal") or [], ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"Wrote journal to {args.journal_out}", file=sys.stderr)
    _print_json(result)
    return 0 if result.get("status") == "completed" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Essay Read research agent harness")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("workflows", help="List available research workflows")

    run = sub.add_parser("run", help="Run a research workflow with a goal")
    run.add_argument("--workflow", required=True, choices=["literature_brief", "survey_path"])
    run.add_argument("--workspace-id", required=True, type=int)
    run.add_argument("--goal", required=True)
    run.add_argument("--out", help="Write markdown artifact to this path")
    run.add_argument("--journal-out", help="Write journal JSON to this path")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "workflows":
        return asyncio.run(_cmd_workflows())
    if args.command == "run":
        return asyncio.run(_cmd_run(args))
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
