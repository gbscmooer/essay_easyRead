"""AutoScholarQuery 式的召回评测（PaSa 的评测口径，零人工标注）。

build：从本地 arXiv 镜像抽 CS 论文，读 arXiv HTML 的 Related Work 类小节，让模型把一节改写成一条
       细粒度检索请求（“Find papers that …”），该节引用、且在镜像里能按题名找到的论文作为标准答案。
run：  对每条请求只跑 ResearchLoop（检索 + 判定 + 展开，不作答），分环节记录：
         crawler_recall  标准答案进入候选池的比例（Crawler 的上限）
         recall          标准答案被 Selector 选中的比例；recall@20 / @50 按排序分截断
         precision       选中论文里属于标准答案的比例（标准答案不完整，只是下界）
         llm_calls / tokens / wall_s   每题成本
       出处论文本身与晚于它发表的论文一律排除（防止直接搜到出题来源）。

用法：
  python -m backend.eval.autoscholar build --n 40 --years 2023 2025
  python -m backend.eval.autoscholar run data/eval/autoscholar_items_<stamp>.json --depth lite
  python -m backend.eval.autoscholar run <items> --variant noexp='{"expand_layers": 0}'   # 同时段对照
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import statistics
import time
from datetime import datetime
from pathlib import Path

from backend.eval import ledger

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data/eval"
SECTION_HINT = re.compile(r"related|background|prior|literature|preliminar", re.I)
MIN_GOLD = 3

_QUERY_SYSTEM = "你是文献检索专家。只输出一个 JSON 对象。"


def _query_prompt(section: str, text: str, titles: list[str]) -> str:
    return "\n".join([
        f"## 某篇论文的相关工作小节：{section}", text[:3500], "",
        "## 该节引用的部分论文", *[f"- {t}" for t in titles[:12]], "",
        "## 任务",
        "把这一节讨论的主题改写成一条英文文献检索请求，以 “Find papers that” 开头，要具体到方法、任务或条件，"
        "让上面这些被引论文正好是它的答案；不要提到写这一节的论文本身，不要照抄某篇被引论文的题名。",
        '输出：{"query": "Find papers that ..."}',
    ])


def _norm_arxiv(x: str | None) -> str:
    return re.sub(r"v\d+$", "", str(x or "").strip().lower())


async def build(args) -> Path:
    from backend.eval.public_benchmarks import _llm_cfg
    from backend.services.deep_research.arxiv_mirror import default_mirror
    from backend.services.deep_research.loop.llm import json_caller
    from backend.services.deep_research.loop.papers import title_key
    from backend.services.deep_research.loop.sections import fetch_sections
    from backend.services.deep_research.loop.sources import Sources

    mirror = default_mirror()
    if mirror is None:
        raise SystemExit("本地 arXiv 镜像未建好（见 arxiv_mirror.py），无法造题")
    lo, hi = args.years
    rows = mirror.conn.execute(
        "SELECT arxiv_id, title, year FROM papers WHERE categories LIKE 'cs.%' AND year BETWEEN ? AND ?",
        (lo, hi)).fetchall()
    random.Random(args.seed).shuffle(rows)
    ask = json_caller(_llm_cfg(args.thinking_effort, args.model), max_tokens=300)
    sources = Sources(enabled=("arxiv",), online=True)
    items: list[dict] = []
    tried = 0
    for arxiv_id, title, year in rows:
        if len(items) >= args.n or tried >= args.n * args.max_tries:
            break
        tried += 1
        parsed = await fetch_sections(arxiv_id)
        if parsed is None:
            continue
        names = [n for n in parsed.names() if SECTION_HINT.search(n)]
        for name in names[:2]:
            refs = parsed.cited([name], 40)
            found = await sources.resolve_titles([r.title for r in refs if r.title])
            gold = [{"arxiv_id": p.get("arxiv_id"), "title": p.get("title"), "year": p.get("year")}
                    for p in found.values()
                    if _norm_arxiv(p.get("arxiv_id")) != _norm_arxiv(arxiv_id)
                    and (not p.get("year") or not year or p["year"] <= year)]
            if len(gold) < MIN_GOLD:
                continue
            try:
                data = await ask(_QUERY_SYSTEM, _query_prompt(name, parsed.texts.get(name, ""), [g["title"] for g in gold]))
            except Exception as exc:  # noqa: BLE001
                print(f"  造题失败 {arxiv_id}: {exc}", flush=True)
                continue
            query = str(data.get("query") or "").strip()
            if not query or any(title_key(g["title"]) and title_key(g["title"]) in title_key(query) for g in gold):
                continue
            items.append({"id": f"{arxiv_id}#{len(items)}", "source_arxiv": arxiv_id, "source_title": title,
                          "year": year, "section": name, "query": query, "gold": gold})
            print(f"[{len(items)}/{args.n}] {query[:100]}（标准答案 {len(gold)} 篇）", flush=True)
            break
    await sources.aclose()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / f"autoscholar_items_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"meta": {"n": len(items), "seed": args.seed, "years": [lo, hi], "tried": tried},
                               "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"完成 {len(items)} 题（尝试 {tried} 篇）→ {out}")
    return out


def _score(item: dict, chosen: list[dict], pool: list[dict]) -> dict:
    from backend.services.deep_research.loop.papers import title_key

    def ids(p: dict) -> set[str]:
        out = {"t:" + title_key(p.get("title"))}
        if p.get("arxiv_id"):
            out.add("a:" + _norm_arxiv(p["arxiv_id"]))
        return out

    gold = [ids(g) for g in item["gold"]]
    hit = lambda rows: sum(1 for g in gold if any(g & ids(p) for p in rows))  # noqa: E731
    n = max(1, len(gold))
    in_selected = hit(chosen)
    return {"gold": len(gold), "candidates": len(pool), "selected": len(chosen),
            "crawler_recall": round(hit(pool) / n, 4), "recall": round(in_selected / n, 4),
            "recall@20": round(hit(chosen[:20]) / n, 4), "recall@50": round(hit(chosen[:50]) / n, 4),
            "precision": round(in_selected / max(1, len(chosen)), 4)}


async def run(args) -> Path:
    from backend.eval.public_benchmarks import QuotaWatch, _llm_cfg
    from backend.services.deep_research.loop.config import config_for
    from backend.services.deep_research.loop.runner import ResearchLoop
    from backend.services.deep_research.reranker import rerank_scores
    from backend.services.llm import start_token_ledger, token_ledger_report

    data = json.loads(Path(args.items).read_text(encoding="utf-8"))
    items = data["items"][:args.first] if args.first else data["items"]
    cfg_llm = _llm_cfg(args.thinking_effort, args.model)
    base = json.loads(args.loop) if args.loop else {}
    variants = {"base": base, **{k: {**base, **json.loads(v)} for k, v in (x.split("=", 1) for x in args.variant)}}
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / f"autoscholar_{stamp}.json"
    log = ledger.open_log(out)
    meta = {"items": args.items, "n": len(items), "depth": args.depth, "variants": variants, "label": args.label,
            "system_model": cfg_llm.get("model"), "provenance": ledger.provenance(out)}
    QuotaWatch.install()
    gate = asyncio.Semaphore(max(1, args.concurrency))
    records: list[dict] = []

    async def one(item: dict, overrides: dict) -> dict:
        year = item.get("year")
        source = _norm_arxiv(item["source_arxiv"])

        def admit(p: dict) -> bool:   # 出处论文与晚于它的论文不进候选池
            if _norm_arxiv(p.get("arxiv_id")) == source:
                return False
            y = p.get("year")
            return not (isinstance(y, int) and isinstance(year, int) and y > year)

        cfg = config_for(args.depth, {"sources": ["arxiv"], **overrides})
        loop = ResearchLoop(item["query"], cfg, llm_cfg=cfg_llm, rerank=rerank_scores, paper_filter=admit)
        tokens, t0 = start_token_ledger(), time.monotonic()
        try:
            chosen = await loop.crawl()
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"[:300]}
        crawl = loop.crawl_report()
        return {**_score(item, chosen, list(loop.state.papers.values())),
                "llm_calls": crawl["selector"]["llm_calls"] + crawl["crawler"]["llm_calls"],
                "expanded": crawl["expanded"], "by_origin": crawl["by_origin"],
                "tokens": token_ledger_report(tokens), "wall_s": round(time.monotonic() - t0, 1)}

    async def worker(item: dict) -> None:
        async with gate:
            res = await asyncio.gather(*(one(item, kw) for kw in variants.values()))
        rec = {"id": item["id"], "query": item["query"], "variants": dict(zip(variants, res))}
        records.append(rec)
        print(f"[{len(records)}/{len(items)}] {item['query'][:80]}\n    → " + "  ".join(
            f"{n}: 候选召回 {v.get('crawler_recall')} 选中召回 {v.get('recall')} 精度 {v.get('precision')}"
            f"（{v.get('llm_calls')} 次调用）" if "error" not in v else f"{n}: {v['error'][:80]}"
            for n, v in rec["variants"].items()), flush=True)

    await asyncio.gather(*(worker(x) for x in items))
    summary = {}
    for name in variants:
        rows = [r["variants"][name] for r in records if "error" not in r["variants"][name]]

        def mean(key: str) -> dict:
            vals = [float(r[key]) for r in rows]
            if not vals:
                return {"mean": None, "se": None}
            se = statistics.stdev(vals) / len(vals) ** 0.5 if len(vals) > 1 else None
            return {"mean": round(statistics.fmean(vals), 4), "se": round(se, 4) if se is not None else None}

        summary[name] = {"questions": len(rows), **{k: mean(k) for k in (
            "crawler_recall", "recall", "recall@20", "recall@50", "precision", "selected", "candidates",
            "llm_calls", "wall_s")},
            "tokens_total": sum(((r.get("tokens") or {}).get("total") or {}).get("total_tokens", 0) for r in rows)}
    report = {"meta": meta, "summary": summary, "records": records}
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1), flush=True)
    ledger.record_metrics(out, report, benchmark="autoscholar", lines=[
        f"- {n}：{v['questions']} 题，候选召回 {v['crawler_recall']['mean']}，选中召回 {v['recall']['mean']}"
        f"（SE {v['recall']['se']}），R@20 {v['recall@20']['mean']}，精度 {v['precision']['mean']}，"
        f"每题模型调用 {v['llm_calls']['mean']}，token 合计 {v['tokens_total']}" for n, v in summary.items()])
    log.close()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="从本地 arXiv 镜像造题")
    b.add_argument("--n", type=int, default=40)
    b.add_argument("--seed", type=int, default=0)
    b.add_argument("--years", type=int, nargs=2, default=[2023, 2025])
    b.add_argument("--max-tries", type=int, default=6, help="每题最多尝试几篇论文（很多论文没有 HTML 或引用解析不出来）")
    r = sub.add_parser("run", help="只跑检索 + 判定 + 展开并分环节打分")
    r.add_argument("items")
    r.add_argument("--depth", default="lite")
    r.add_argument("--loop", default="", help="覆盖循环参数（JSON），见 loop/config.py")
    r.add_argument("--variant", action="append", default=[], help='同时段对照，如 noexp=\'{"expand_layers": 0}\'')
    r.add_argument("--first", type=int, default=0)
    r.add_argument("--concurrency", type=int, default=2)
    r.add_argument("--label", default="")
    for p in (b, r):
        p.add_argument("--thinking-effort", default="off")
        p.add_argument("--model", default="")
    args = parser.parse_args()
    asyncio.run(build(args) if args.cmd == "build" else run(args))


if __name__ == "__main__":
    main()
