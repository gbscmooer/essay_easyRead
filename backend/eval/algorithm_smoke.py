"""Exercise new algorithms on an existing exploration without modifying its data.

This measures integration and runtime, not retrieval accuracy or scientific truth.
Usage: python -m backend.eval.algorithm_smoke --run-id 18 --limit 60
"""

import argparse
import asyncio
import copy
import json
import time
from pathlib import Path

from sqlmodel import Session

from backend.models.exploration import ExplorationRun
from backend.services.database import get_engine
from backend.services.deep_research.explorer import cluster_papers, compute_metrics
from backend.services.deep_research.literature_retrieval import paper_similarity, retrieve_materials


async def run(run_id: int, limit: int, audit: bool = False) -> dict:
    with Session(get_engine()) as session:
        source = session.get(ExplorationRun, run_id)
        if source is None:
            raise ValueError("exploration not found")
        question = source.question
        papers = copy.deepcopy((source.result or {}).get("papers", []))[:limit]
    if not papers:
        raise ValueError("exploration has no papers")
    from datetime import datetime, timezone
    started = time.monotonic()
    metrics = compute_metrics(papers, now_year=datetime.now(timezone.utc).year)
    similarity, representation = await paper_similarity(papers)
    groups, edges, modularity = cluster_papers(papers, similarities=similarity)
    materials, retrieval = await retrieve_materials(papers, question, limit=5)
    result = {
        "kind": "integration_smoke_not_accuracy_benchmark", "run_id": run_id,
        "question": question, "papers": len(papers), "seconds": round(time.monotonic() - started, 2),
        "representation": representation, "retrieval": retrieval,
        "top_titles": [p["title"] for p in materials],
        "graph": {"algorithm": "mutual_knn_leiden", "clusters": len(set(groups.values()) - {-1}),
                  "edges": len(edges), "modularity": modularity},
        "citation_method": metrics["citation_method"],
        "citation_tested": sum(p["citation_metrics"]["p_value"] is not None for p in papers),
        "citation_rising": len(metrics["rising"]),
        "limitation": "No independent relevance labels; no accuracy gain is inferred.",
    }
    if audit:
        from backend.services.ai_config import get_analysis_config
        from backend.services.deep_research.evidence_audit import audit_answer
        paper = next((p for p in materials if p.get("abstract")), None)
        if paper:
            sentence = paper["abstract"].split(". ")[0] + ". [P1]"
            checked = await audit_answer(sentence, [{"id": "P1", "title": paper["title"],
                "abstract": paper["abstract"]}], get_analysis_config())
            result["audit_smoke"] = {k: checked[k] for k in (
                "status", "method", "model", "claim_count", "supported_count", "support_rate", "error")}
            result["audit_smoke"]["case"] = "single_source_sentence_smoke_not_general_accuracy"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 240:
        parser.error("limit must be between 1 and 240")
    result = asyncio.run(run(args.run_id, args.limit, args.audit))
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
