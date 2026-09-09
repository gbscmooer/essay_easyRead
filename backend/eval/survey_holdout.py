"""Offline comparison against RAG Survey v5 tables; never supplied to the explorer.

Usage: python -m backend.eval.survey_holdout --pdf /path/2312.10997v5.pdf
       --runs 23 24 --output data/eval/rag-holdout
Entity occurrence measures coverage, not correctness of table relationships.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import unicodedata

import fitz
import httpx

from ..services.research_exclusions import title_key, normalize_arxiv_id, excluded

QUESTION = "检索增强生成（RAG）在大语言模型中的主要技术路线、评估框架、常用指标与数据集有哪些？"
HOLDOUT = {"exclusions": {"arxiv_ids": ["2312.10997"], "dois": ["10.48550/arxiv.2312.10997"],
                           "titles": ["Retrieval-Augmented Generation for Large Language Models: A Survey"]}}


def norm(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", value))


def contains(value, alias):
    return f" {norm(alias)} " in f" {norm(value)} "


def reference_tables(pdf: Path):
    with fitz.open(pdf) as doc:
        page = doc[5].get_text()
        methods = re.findall(r"(?m)^([^\n]+) \[(\d+)\]$", page)
        assert len(methods) == 71, "Expected Table I in 2312.10997v5, page 6"
        ref_text = "\n".join(p.get_text() for p in list(doc)[16:])
        refs = dict(re.findall(r"(?ms)^\[(\d+)\]\s+(.*?)(?=^\[\d+\]|\Z)", ref_text))
        assert len(refs) == 182
        core = []
        for name, number in methods:
            raw = refs[number]
            title = re.search("“(.*?)”", raw, re.S)
            arxiv = re.search(r"arXiv:(\d{4}\.\d{4,5})", raw)
            assert title, number
            core.append({"method": name, "reference": int(number), "title": title.group(1),
                         "arxiv_id": arxiv.group(1) if arxiv else None})
        dataset_names = re.findall(r"(?m)^([^\[\n]+)\s*\[\d+\]", doc[12].get_text())
        dataset_names += ["MMCU Medical", "ZsRE"]  # Same-line cells on page 13.
        assert len(dataset_names) == 48
        datasets = {}
        for name in dataset_names:
            name = name.strip()
            base = name.split("(")[0].strip()
            aliases = [base]
            short = re.search(r"\(([^)]+)\)", name)
            if short and "," not in short.group(1):
                aliases.append(short.group(1))
            datasets[base] = aliases
        datasets["Natural Qustion"] += ["Natural Questions"]
        datasets["Web Questions"] += ["WebQuestions"]
        datasets["WikiEvent"] += ["WikiEvents"]
        metrics = {"Accuracy": ["accuracy"], "EM": ["EM", "exact match"], "Recall": ["recall"],
                   "Precision": ["precision"], "R-Rate": ["R-Rate", "reappearance rate"],
                   "Cosine Similarity": ["cosine similarity"], "Hit Rate": ["hit rate"],
                   "MRR": ["MRR", "mean reciprocal rank"], "NDCG": ["NDCG"], "BLEU": ["BLEU"],
                   "ROUGE/ROUGE-L": ["ROUGE", "ROUGE-L"]}
        frameworks = {name: [name] for name in ("RGB", "RECALL", "RAGAS", "ARES", "TruLens", "CRUD")}
    return {"pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
            "cutoff": "2024-03-27", "core": core, "datasets": datasets,
            "metrics": metrics, "frameworks": frameworks,
            "pages": {"core": 6, "datasets": 13, "metrics": 14, "frameworks": 15}}


def compare(run, reference):
    result = run.get("result") or {}
    papers = result.get("papers") or []
    report = result.get("survey_report") or {}
    tables = report.get("tables") or {}
    titles = {title_key(p.get("title")) for p in papers}
    arxivs = {normalize_arxiv_id(p.get("arxiv_id") or p.get("doi")) for p in papers} - {""}
    core_hit = [p for p in reference["core"] if title_key(p["title"]) in titles
                or p.get("arxiv_id") and p["arxiv_id"] in arxivs]
    out = {"run_id": run["id"], "coverage": report.get("coverage"), "corpus": len(papers),
           "leaks": [p["key"] for p in papers if excluded(p, HOLDOUT)],
           "core": {"hits": core_hit, "total": len(reference["core"])},
           "post_2024": sum(isinstance(p.get("year"), int) and p["year"] > 2024 for p in papers),
           "extraction_status": report.get("status"), "method": report.get("method")}
    for group, table, field in (("datasets", "tasks", "dataset"), ("metrics", "metrics", "metric"),
                                 ("frameworks", "frameworks", "name")):
        values = [r.get(field, "") for r in tables.get(table, []) if r.get("evidence")]
        hits = [name for name, aliases in reference[group].items()
                if any(contains(v, a) for v in values for a in aliases)]
        out[group] = {"hits": hits, "total": len(reference[group]),
                      "missing": [n for n in reference[group] if n not in hits]}
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--runs", nargs="+", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    reference = reference_tables(args.pdf)
    (args.output / "reference.json").write_text(json.dumps(reference, ensure_ascii=False, indent=2))
    compared = []
    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        for run_id in args.runs:
            response = client.get(f"/api/exploration/runs/{run_id}")
            response.raise_for_status()
            run = response.json()
            (args.output / f"run-{run_id}.json").write_text(json.dumps(run, ensure_ascii=False, indent=2))
            for fmt in ("html", "markdown"):
                exported = client.get(f"/api/exploration/runs/{run_id}/report", params={"format": fmt})
                exported.raise_for_status()
                (args.output / f"report-{run_id}.{fmt if fmt == 'html' else 'md'}").write_text(exported.text)
            compared.append(compare(run, reference))
    (args.output / "comparison.json").write_text(json.dumps(compared, ensure_ascii=False, indent=2))
    print(json.dumps([{k: ({"hits": len(v["hits"]), "total": v["total"]} if isinstance(v, dict) and "hits" in v else v)
                       for k, v in row.items() if k != "method"} for row in compared], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
