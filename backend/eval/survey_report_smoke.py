"""Generate an isolated report artifact from a PDF or existing workspace papers.

Uses configured models; does not create or overwrite business records.
"""

import argparse
import asyncio
import json
from pathlib import Path

from sqlmodel import Session

from ..services.ai_config import get_analysis_config
from ..services.database import get_engine
from ..services.pdf_parser import extract_pdf_content
from ..services.research_sources import load_seeds
from ..services.deep_research.explorer import compute_metrics, synthesize_exploration
from ..services.deep_research.survey_report import build_survey_report, render_html


async def main(args):
    if args.pdf:
        content = extract_pdf_content(args.pdf)
        papers = [{"key": "provided:pdf", "title": content.metadata.get("title") or Path(args.pdf).stem,
                   "abstract": content.full_text[:3000], "source_text": content.full_text,
                   "is_seed": True, "year": None, "citation_count": 0, "cluster": -1}]
    else:
        with Session(get_engine()) as session:
            papers, _ = load_seeds(session, {"seed_paper_ids": args.paper_ids})
    cfg = get_analysis_config()
    metrics = compute_metrics(papers, now_year=2026)
    synthesis = await synthesize_exploration(args.question,
        {"intent": "landscape", "en_queries": [args.question], "focus_terms": []},
        papers, metrics, cfg, max_papers=12)
    report = await build_survey_report(args.question, papers, metrics,
        synthesis.model_dump() if synthesis else None, cfg, depth="quick")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output.with_suffix(".html").write_text(render_html(report), encoding="utf-8")
    print(json.dumps({"coverage": report["coverage"], "tables": {k: len(v) for k, v in report["tables"].items()},
                      "status": report["status"], "html": str(output.with_suffix('.html').resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--pdf")
    inputs.add_argument("--paper-ids", type=int, nargs="+")
    parser.add_argument("--question", required=True)
    parser.add_argument("--output", required=True)
    asyncio.run(main(parser.parse_args()))
