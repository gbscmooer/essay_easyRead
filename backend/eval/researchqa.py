"""ResearchQA（宾大，TACL 2026）：从 75 个领域的综述提炼出的问题与细则，测开放问答在非 CS 领域的能力。

数据：data/eval/public/researchqa_test.json（Hugging Face realliyifei/ResearchQA 的 test.json，MIT 许可）。
打分与官方 compute_coverage.py 一致：每 8 条细则一批，评委对每条只答 Not at all / Barely / Moderately /
Mostly / Completely，映射 1~5 后归一化到 0~1 取平均（细则覆盖率）。官方评委为 gpt-4.1-mini，这里默认用同档
小模型 glm-5.3-flash；分数只在同一评委下比较本系统各组，不与论文公布的成绩直接比较。

抽题：非 CS 题按学科大类分层（每题来自不同领域，大类内领域不够时才重复），另抽少量 CS 题作对照，
用来比较“工具带来的提升”在模型熟悉与不熟悉的领域是否不同。

各组（同一道题的各组同时提交，避免服务时段差异）：
  bare      裸模型凭记忆作答（不检索）
  agent     指针式作答单独运行（只用本地 arXiv 镜像；镜像约 98% 为 cs 论文）
  pipeline  八阶段流程的开放问答模式（Scopus + arXiv 召回 → 指针式作答；评测不用 OpenAlex）
  pipeline_ft  同 pipeline，补全文时依次试 arXiv → Europe PMC 开放获取 → ScienceDirect 订阅全文（校园网），
               补全文的 6 个名额由重排模型按与问题的相关度分配
  pipeline_web 同 pipeline_ft，另加在线 Scopus 与镜像一起提名论文、按题名查找点名论文
  pipeline_all 同 pipeline_web，另让选材阶段先走官方全文接口（Europe PMC / ScienceDirect）
  pipeline_ctx 同 pipeline_all，另让起草前先读召回论文中最相关的 15 篇（题名与摘要）
  agent_web    指针式作答单独运行（不走召回），镜像 + Scopus 提名，多来源全文

用法：
  python -m backend.eval.researchqa run --subset 10 [--arms bare agent pipeline]   # 先跑分层子集
  python -m backend.eval.researchqa run --n-noncs 40 --n-cs 10 --seed 0             # 子集效果好了再跑全部
  python -m backend.eval.researchqa run --resume data/eval/researchqa_<stamp>.json        # 只补跑缺失或出错的组
  python -m backend.eval.researchqa judge data/eval/researchqa_<stamp>.json --judge-model glm-5.3   # 换评委重打
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import statistics
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from backend.eval import ledger
from backend.services.llm import llm_stage

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/eval/public/researchqa_test.json"
OUT_DIR = ROOT / "data/eval"
JUDGE_MODEL = "glm-5.3-flash"
BATCH = 8
FT_SOURCES = ["arxiv", "pmc", "elsevier"]
_FT = {"full_text_sources": FT_SOURCES, "fetch_order": "rerank"}
# 组名 → （作答方式, 指针式作答的额外选项）；bare 不检索
ARM_SPECS = {
    "bare": ("bare", {}),
    "agent": ("agent", {}),
    "agent_web": ("agent", {**_FT, "web_search": "scopus"}),
    "pipeline": ("pipeline", {}),
    "pipeline_ft": ("pipeline", _FT),
    "pipeline_web": ("pipeline", {**_FT, "web_search": "scopus"}),
    # 另加：选材阶段先走官方全文接口（Europe PMC / ScienceDirect），再走网页 PDF
    "pipeline_all": ("pipeline", {**_FT, "web_search": "scopus"}, {"api_full_text": True}),
    # 另加：起草前先读召回论文中最相关的 15 篇的题名与摘要
    "pipeline_ctx": ("pipeline", {**_FT, "web_search": "scopus", "draft_context": 15}, {"api_full_text": True}),
}
ARMS = ("bare", "agent", "pipeline", "pipeline_ft")   # 默认组

# 与 CS 直接相关的领域：非 CS 组整体排除，CS 对照组只从这里抽
CS_FIELDS = frozenset({"Artificial Intelligence", "Natural Language Processing",
                       "Computer Vision & Pattern Recognition", "Computing Systems", "Signal Processing"})
# 非 CS 组按学科大类分配题数（合计 40，大致按测试集占比、保证小类至少 2 题）
QUOTA = {"Health Sciences & Medicine": 12, "Life & Earth Sciences": 8, "Physical & Theoretical Sciences": 6,
         "Engineering": 6, "Social Sciences": 3, "Humanities & Arts": 3, "Business & Economics": 2}

# 官方 compute_coverage.py 的提示词，逐字一致
_JUDGE_PROMPT = (
    "Please judge the following questions based on the response below.\n"
    "For each question, select one of the following ratings to indicate the extent to which the response addresses the question:\n"
    "Not at all, Barely, Moderately, Mostly, Completely\n\n"
    "Definitions:\n"
    "- Not at all: *totally uninferable*\n"
    "- Barely: *unmentioned but inferrable*\n"
    "- Moderately: *mentioned but misses important details*\n"
    "- Mostly: *mentioned but misses some details*\n"
    "- Completely: *mentioned with sufficient details*\n\n"
    "Only output one of the five phrases for each question, separated by newlines, and nothing else.\n\n"
    "Response: {response}\n"
    "Questions:\n{questions}\n\nOutput:")
LABELS = {"not at all": 1, "barely": 2, "moderately": 3, "mostly": 4, "completely": 5}


def load_items(path: Path = DATA) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _types(rubric_item: dict) -> list[str]:
    t = rubric_item.get("type") or []
    return [str(x) for x in (t if isinstance(t, list) else [t])]


def pick_items(items: list[dict], *, seed: int, n_noncs: int = 40, n_cs: int = 10) -> list[dict]:
    """分层抽题：非 CS 组按 QUOTA 比例分到各大类，大类内轮流取不同领域；CS 对照组在 CS 领域间轮流取。"""
    rng = random.Random(seed)

    def take(pool: list[dict], k: int, group: str) -> list[dict]:
        by_field: dict[str, list[dict]] = defaultdict(list)
        for x in sorted(pool, key=lambda x: x["id"]):
            by_field[x["field"]].append(x)
        fields = sorted(by_field)
        rng.shuffle(fields)
        for f in fields:
            rng.shuffle(by_field[f])
        out = []
        for j in range(k):
            f = fields[j % len(fields)]
            if by_field[f]:
                out.append({**by_field[f].pop(), "group": group})
        return out

    total = sum(QUOTA.values())
    picked: list[dict] = []
    for dom, k in QUOTA.items():
        pool = [x for x in items if x["general_domain"] == dom and x["field"] not in CS_FIELDS]
        picked += take(pool, round(k * n_noncs / total), "non_cs")
    picked += take([x for x in items if x["field"] in CS_FIELDS], n_cs, "cs")
    return picked


def subset(picked: list[dict], k: int) -> list[dict]:
    """分层子集：各学科大类轮流取一题（CS 对照排第二），k=10 时覆盖全部 7 个非 CS 大类 + 2 道 CS。
    先在子集上看效果、改问题，子集效果好了再跑全部题目。"""
    order = ["Health Sciences & Medicine", "cs", *[d for d in QUOTA if d != "Health Sciences & Medicine"]]
    queues = {key: [x for x in picked if (x["group"] == "cs" and key == "cs")
                    or (x["group"] == "non_cs" and x["general_domain"] == key)] for key in order}
    out: list[dict] = []
    while len(out) < k and any(queues.values()):
        for key in order:
            if queues[key] and len(out) < k:
                out.append(queues[key].pop(0))
    return out


def parse_labels(text: str, n: int) -> list[str] | None:
    """评委输出 → n 个档位；行首编号、项目符号、标点与大小写不计。条数或档位不对时返回 None（重试）。"""
    out = []
    for line in str(text or "").splitlines():
        s = re.sub(r"^\s*(?:[-*•]|\d+[.)、:：])\s*", "", line).strip().strip(".*`\"'").lower()
        if s:
            out.append(s)
    return out if len(out) == n and all(s in LABELS for s in out) else None


def coverage(judged: list[dict]) -> dict:
    """覆盖率（0~1）及按细则类型的覆盖率；一条细则可属于多个类型。"""
    scores = [j["score"] for j in judged]
    by_type: dict[str, list[float]] = defaultdict(list)
    for j in judged:
        for t in j["types"]:
            by_type[t].append(j["score"])
    return {"coverage": round(sum(scores) / len(scores), 4) if scores else None,
            "by_type": {t: round(sum(v) / len(v), 4) for t, v in sorted(by_type.items())}}


class CoverageJudge:
    """repeats>1 时同一批细则独立打分 repeats 次，每条取平均（压低评委噪声；官方只打一次）。"""

    def __init__(self, complete_text, repeats: int = 1):
        self.complete_text = complete_text   # async (prompt) -> str
        self.repeats = max(1, repeats)

    async def _labels(self, prompt: str, n: int, batch_no: int) -> list[str]:
        for _ in range(3):   # 官方同样重试 3 次；仍不对就整题记评委出错，不计 0 分
            with llm_stage("judge_rubric"):
                labels = parse_labels(await self.complete_text(prompt), n)
            if labels:
                return labels
        raise ValueError(f"评委输出条数或档位不符（第 {batch_no} 批）")

    async def judge(self, response: str, rubric: list[dict]) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(rubric), BATCH):
            batch = rubric[i:i + BATCH]
            prompt = _JUDGE_PROMPT.format(response=response, questions="\n".join(r["rubric_item"] for r in batch))
            runs = await asyncio.gather(*(self._labels(prompt, len(batch), i // BATCH + 1)
                                          for _ in range(self.repeats)))
            for j, r in enumerate(batch):
                labs = [run[j] for run in runs]
                out.append({"rubric_item": r["rubric_item"], "types": _types(r), "label": labs[0], "labels": labs,
                            "score": sum((LABELS[x] - 1) / 4 for x in labs) / len(labs)})
        return out


def make_judge(model: str, repeats: int = 1) -> CoverageJudge:
    from backend.eval.scholarqa_cs import judge_cfg
    from backend.services.llm import complete_text

    cfg = judge_cfg(model)

    async def call(prompt: str) -> str:
        # 官方只发一条用户消息；system 置空
        return await complete_text(cfg, system="", user=prompt, max_tokens=300, temperature=0.0,
                                   thinking_effort="off", max_retries=2)
    return CoverageJudge(call, repeats)


def _paired(records: list[dict], a: str, b: str, group: str | None = None) -> dict | None:
    diffs = [r["arms"][a]["coverage"] - r["arms"][b]["coverage"] for r in records
             if (group is None or r["group"] == group)
             and all((r["arms"].get(x) or {}).get("coverage") is not None for x in (a, b))]
    if len(diffs) < 2:
        return None
    return {"diff": round(statistics.mean(diffs), 4),
            "se": round(statistics.stdev(diffs) / len(diffs) ** 0.5, 4), "n": len(diffs),
            "wins": sum(d > 0 for d in diffs)}


def summarize(records: list[dict], arms: list[str]) -> dict:
    def mean(arm: str, pred) -> dict | None:
        vals = [r["arms"][arm]["coverage"] for r in records
                if pred(r) and (r["arms"].get(arm) or {}).get("coverage") is not None]
        return {"coverage": round(statistics.mean(vals), 4), "n": len(vals)} if vals else None

    def type_mean(arm: str, group: str) -> dict:
        acc: dict[str, list[float]] = defaultdict(list)
        for r in records:
            a = r["arms"].get(arm) or {}
            if r["group"] == group and a.get("by_type"):
                for t, v in a["by_type"].items():
                    acc[t].append(v)
        return {t: round(statistics.mean(v), 4) for t, v in sorted(acc.items())}

    out: dict = {"arms": {}, "paired": {}}
    for arm in arms:
        out["arms"][arm] = {"all": mean(arm, lambda r: True),
                            "non_cs": mean(arm, lambda r: r["group"] == "non_cs"),
                            "cs": mean(arm, lambda r: r["group"] == "cs"),
                            "by_domain": {d: mean(arm, lambda r, d=d: r["group"] == "non_cs" and r["domain"] == d)
                                          for d in QUOTA},
                            "by_type_non_cs": type_mean(arm, "non_cs"),
                            "words": round(statistics.mean([r["arms"][arm]["words"] for r in records
                                                            if (r["arms"].get(arm) or {}).get("words")] or [0]))}
    for a in arms:
        if a != "bare" and "bare" in arms:
            out["paired"][f"{a}-bare"] = {g: _paired(records, a, "bare", g) for g in ("non_cs", "cs")}
    for a, b in (("pipeline", "agent"), ("pipeline_ft", "pipeline"), ("pipeline_web", "pipeline_ft"),
                 ("agent_web", "agent"), ("pipeline_web", "agent_web"), ("pipeline_all", "pipeline_web"),
                 ("pipeline_all", "pipeline_ft"), ("pipeline_ctx", "pipeline_all"), ("pipeline_ctx", "bare")):
        if a in arms and b in arms:
            out["paired"][f"{a}-{b}"] = {g: _paired(records, a, b, g) for g in ("non_cs", "cs")}
    return out


def _ledger_lines(summary: dict) -> list[str]:
    lines = []
    for arm, s in summary["arms"].items():
        parts = [f"{g} {s[g]['coverage']:.3f}（{s[g]['n']} 题）" for g in ("non_cs", "cs") if s.get(g)]
        lines.append(f"{arm}：覆盖率 " + "，".join(parts) + f"，平均 {s['words']} 词")
    for k, v in summary["paired"].items():
        parts = [f"{g} {p['diff']:+.3f}（SE {p['se']:.3f}，{p['wins']}/{p['n']}）" for g, p in v.items() if p]
        if parts:
            lines.append(f"配对 {k}：" + "，".join(parts))
    return lines


async def _answer(arm: str, item: dict, *, system_cfg: dict, depth: str, runs_dir: Path, engine, mirror) -> dict:
    """一组作答，返回 {response, status, ...}；失败抛异常由调用方记录。"""
    from backend.eval import explore_eval
    from backend.eval.scholarqa_cs import _BASELINE_SYSTEM, make_llm_json
    from backend.services.deep_research.answer_agent import RECOMMENDED, write_with_pointers
    from backend.services.deep_research.answer_writer import render_answer
    from backend.services.deep_research.domain_synthesizer import _llm_json

    query = item["query"]
    mode, extra, *rest = ARM_SPECS[arm]
    run_opts = rest[0] if rest else {}
    if mode == "bare":
        base = await make_llm_json(system_cfg, max_tokens=4000)(_BASELINE_SYSTEM, query)
        return {"response": str(base.get("answer") or ""), "status": "ok"}
    save = runs_dir / f"{item['id']}_{arm}.json"
    if mode == "agent":
        from backend.services.deep_research.evidence_search import ScopusSearch

        async def answer_json(system: str, user: str) -> dict:
            return await _llm_json(system_cfg, system, user, max_tokens=6000)
        opts = {**RECOMMENDED, **extra}
        web = ScopusSearch() if opts.pop("web_search", None) == "scopus" else None
        answer = await write_with_pointers(query, [], llm_json=answer_json, mirror=mirror, web_search=web, **opts)
        save.write_text(json.dumps({"result": {"answer": answer}}, ensure_ascii=False), encoding="utf-8")
    else:
        options = {"answer_only": True, "answer_writer": "agent", **run_opts,
                   **({"answer_agent": extra} if extra else {})}
        sysrec = await explore_eval.run_system_arm(query, depth, save_as=save, engine=engine, options=options)
        if sysrec.get("error"):
            raise RuntimeError(sysrec["error"])
        answer = json.loads(save.read_text(encoding="utf-8"))["result"].get("answer") or {}
    ag = answer.get("agent") or {}
    return {"response": render_answer(answer), "status": answer.get("status"), "check": answer.get("check"),
            "materials": answer.get("materials"), "saved": str(save), "full_text": ag.get("full_text"),
            "found": {k: ag.get(k) for k in ("recall_papers", "works_found", "works_web_found", "search_added",
                                              "question_search_added", "claim_search_added")}}
    raise ValueError(arm)


async def run(args) -> Path:
    from backend.eval import explore_eval
    from backend.eval.public_benchmarks import _llm_cfg
    from backend.eval.scholarqa_cs import disable_openalex
    from backend.services.deep_research.arxiv_mirror import default_mirror
    from backend.services.llm import start_token_ledger, token_ledger_report

    disable_openalex()
    system_cfg = _llm_cfg(args.thinking_effort, args.model)
    judge = make_judge(args.judge_model, args.judge_repeats)
    prev: dict[str, dict] = {}
    if args.resume:
        old = json.loads(Path(args.resume).read_text(encoding="utf-8"))
        prev = {r["id"]: r for r in old["records"]}
        meta = old["meta"]
        out = Path(args.resume)
        picked = [{**r["item"], "group": r["group"]} for r in old["records"]]
        arms = meta["arms"]
    else:
        picked = pick_items(load_items(), seed=args.seed, n_noncs=args.n_noncs, n_cs=args.n_cs)
        if args.subset:
            picked = subset(picked, args.subset)
        if args.first:
            picked = picked[:args.first]
        arms = list(args.arms)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = OUT_DIR / f"researchqa_{stamp}.json"
        meta = {"seed": args.seed, "n": len(picked), "n_noncs": args.n_noncs, "n_cs": args.n_cs, "arms": arms,
                "depth": args.depth, "system_model": system_cfg.get("model"), "judge_model": args.judge_model,
                "judge_repeats": args.judge_repeats,
                "started": stamp, "label": args.label, "concurrency": args.concurrency,
                "openalex": False, "provenance": ledger.provenance(out)}
    runs_dir = out.with_name(out.stem + "_runs")
    runs_dir.mkdir(parents=True, exist_ok=True)
    log = ledger.open_log(out)
    mirror = default_mirror() if any(ARM_SPECS[a][0] == "agent" for a in arms) else None
    gate = asyncio.Semaphore(max(1, args.concurrency))
    done: dict[str, dict] = dict(prev)
    started = time.monotonic()

    def save() -> None:
        records = [done[x["id"]] for x in picked if x["id"] in done]
        out.write_text(json.dumps({"meta": meta, "partial": True, "records": records}, ensure_ascii=False,
                                  indent=1), encoding="utf-8")

    async def one_arm(arm: str, item: dict, engine) -> dict:
        t0 = time.monotonic()
        tokens = start_token_ledger()
        try:
            rec = await _answer(arm, item, system_cfg=system_cfg, depth=args.depth, runs_dir=runs_dir,
                                engine=engine, mirror=mirror)
            rec["tokens"] = token_ledger_report(tokens)
            rec["wall_s"] = round(time.monotonic() - t0, 1)
            rec["words"] = len(rec["response"].split())
            if rec["response"].strip():
                judged = await judge.judge(rec["response"], item["rubric"])
                rec |= {"judged": judged, **coverage(judged)}
            else:
                rec["error"] = "空答案"
        except Exception as exc:  # noqa: BLE001 —— 单组失败记录在案，--resume 可补跑
            rec = {"error": f"{type(exc).__name__}: {exc}"[:300], "wall_s": round(time.monotonic() - t0, 1)}
        return rec

    async def worker(item: dict, engine) -> None:
        old = (prev.get(item["id"]) or {}).get("arms") or {}
        todo = [a for a in arms if (old.get(a) or {}).get("coverage") is None]
        if not todo:
            return
        async with gate:
            res = await asyncio.gather(*(one_arm(a, item, engine) for a in todo))   # 各组同时提交
        rec = {"id": item["id"], "group": item["group"], "domain": item["general_domain"], "field": item["field"],
               "item": {k: item[k] for k in ("id", "general_domain", "subdomain", "field", "query", "date", "rubric")},
               "arms": {**old, **dict(zip(todo, res))}}
        done[item["id"]] = rec
        cov = "  ".join(f"{a}={(rec['arms'][a].get('coverage') if rec['arms'][a].get('coverage') is not None else 'ERR')}"
                        for a in arms)
        print(f"[{len(done)}/{len(picked)}] {item['group']} {item['field']}：{item['query'][:70]}\n    → {cov}"
              f"  [用时 {round((time.monotonic() - started) / 60, 1)} 分]", flush=True)
        for a in todo:
            if rec["arms"][a].get("error"):
                print(f"      {a} 出错：{rec['arms'][a]['error'][:160]}", flush=True)
        save()

    with explore_eval.shared_engine() as engine:
        await asyncio.gather(*(worker(x, engine) for x in picked))
    records = [done[x["id"]] for x in picked if x["id"] in done]
    meta["wall_min"] = round(meta.get("wall_min", 0) + (time.monotonic() - started) / 60, 1)
    report = {"meta": meta, "summary": summarize(records, arms), "records": records}
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    ledger.record_metrics(out, report, benchmark="researchqa", lines=_ledger_lines(report["summary"]))
    log.close()
    return out


async def rejudge(args) -> Path:
    """换评委重打已有答案（答案不变），写到新文件。"""
    src = Path(args.file)
    report = json.loads(src.read_text(encoding="utf-8"))
    judge = make_judge(args.judge_model, args.judge_repeats)
    gate = asyncio.Semaphore(max(1, args.concurrency))

    async def one(rec: dict, arm: str) -> None:
        a = rec["arms"].get(arm) or {}
        if not (a.get("response") or "").strip():
            return
        async with gate:
            try:
                judged = await judge.judge(a["response"], rec["item"]["rubric"])
                a |= {"judged": judged, **coverage(judged)}
                a.pop("judge_error", None)
            except Exception as exc:  # noqa: BLE001
                a |= {"judge_error": f"{type(exc).__name__}: {exc}"[:300], "coverage": None}

    arms = report["meta"]["arms"]
    await asyncio.gather(*(one(r, a) for r in report["records"] for a in arms))
    report["meta"] = {**report["meta"], "judge_model": args.judge_model, "judge_repeats": args.judge_repeats,
                      "rejudged_from": src.name,
                      "provenance": ledger.provenance(src)}
    report["summary"] = summarize(report["records"], arms)
    out = src.with_name(f"{src.stem}_judge-{args.judge_model}x{args.judge_repeats}.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    ledger.record_metrics(out, report, benchmark="researchqa", lines=_ledger_lines(report["summary"]))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--n-noncs", type=int, default=40)
    r.add_argument("--n-cs", type=int, default=10)
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--arms", nargs="+", choices=list(ARM_SPECS), default=list(ARMS))
    r.add_argument("--subset", type=int, default=0, help="分层子集：各学科大类轮流取，共 k 题（先子集、后全量）")
    r.add_argument("--first", type=int, default=0, help="只跑抽样中的前 k 题（冒烟验证）")
    r.add_argument("--depth", default="quick", help="pipeline 组的研究深度")
    r.add_argument("--thinking-effort", default="off")
    r.add_argument("--model", default="", help="覆盖系统模型（默认用设置里的模型）")
    r.add_argument("--concurrency", type=int, default=3, help="同时在跑的题数（每题各组同时提交）")
    r.add_argument("--resume", default="", help="沿用这次结果，只补跑缺失或出错的组")
    r.add_argument("--label", default="")
    j = sub.add_parser("judge")
    j.add_argument("file")
    j.add_argument("--concurrency", type=int, default=6)
    for p in (r, j):
        p.add_argument("--judge-model", default=JUDGE_MODEL)
        p.add_argument("--judge-repeats", type=int, default=1, help="同一答案独立打分几次取平均（压低评委噪声）")
    args = parser.parse_args()
    asyncio.run({"run": run, "judge": rejudge}[args.cmd](args))


if __name__ == "__main__":
    main()
