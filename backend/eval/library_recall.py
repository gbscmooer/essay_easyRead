"""库内召回测试（E15）：只用本地论文库里的全文回答一道自编的数据集类问题，逐层测“该给的信息有没有
送到定稿模型面前、有没有写进答案”。不联网、不用评委。

标准答案：backend/eval/library_recall_gt_v1.json——题目、20 个与虚假相关有关的数据集（别名、虚假特征、
用到它们的论文）。由全文扫描与人工逐条核对原句确定，不经过被测工具。

同一份草稿下比较几组配置（只换证据环节）：
  v0  只按草稿逐句找证据（现行推荐配置的证据环节）
  v1  v0 + 原文阅读（按问题从证据库取若干句）
  v2  v1 + 逐篇精读（全文论文按题名与摘要和问题的相关性取前 K 篇，每篇由模型挑最多 N 处，含表格）
  v3  v0 + 逐篇精读（不做原文阅读；E16：开精读时原文阅读没有独有的送达）
  精读的开关对 v2/v3 生效：--paper-notes on|off（挑选时是否写要点）、--paper-use cover|support、--missing-fix supplement|rewrite
  （定稿漏写清单对象时只补句还是整篇重写）
指标：
  送达召回  标准数据集里有原文送到定稿模型面前的比例；另算“论文 × 数据集”用法、相关论文两个口径
  答案召回  定稿正文写出的标准数据集比例；写出的数据集里虚假特征写对的比例（关键词核对，原句另存供人工复核）
  草稿召回  只凭模型知识起草的草稿写出的比例（对照：不用任何工具能写出多少）
  用量      每组配置按阶段记模型调用次数、输入（其中缓存命中）与输出 token（草稿固定时起草不计）
用法：
  python -m backend.eval.library_recall --variants v0,v1,v2 --write v2
  python -m backend.eval.library_recall --draft-from data/eval/library_recall_<stamp>.json ...   # 复用草稿
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from backend.eval import ledger

ROOT = Path(__file__).resolve().parents[2]
GT_DEFAULT = Path(__file__).with_name("library_recall_gt_v1.json")
DB = ROOT / "data" / "essay_read.db"
OUT_DIR = ROOT / "data" / "eval"
_SENT = re.compile(r"(?<=[.!?。！？])\s+")


def load_library(db: Path = DB) -> list[dict]:
    """论文库里有全文的论文 → 作答用的“召回论文”（全文直接进证据库）。"""
    con = sqlite3.connect(db)
    rows = con.execute("SELECT id, title, year, abstract, full_text, doi, arxiv_id, authors FROM papers "
                       "WHERE length(full_text) > 5000 ORDER BY id").fetchall()
    return [{"key": f"lib:{pid}", "title": title or "", "year": year, "abstract": abstract or "",
             "source_text": full_text, "doi": doi or None, "arxiv_id": arxiv_id or None, "authors": authors}
            for pid, title, year, abstract, full_text, doi, arxiv_id, authors in rows]


class _Alias:
    """数据集名称匹配：alias 按 ignore_case 决定是否忽略大小写；alias_cased 是必须大写才算的缩写（如 SIN）。"""

    def __init__(self, d: dict):
        self.rx = re.compile(d["alias"], re.I if d.get("ignore_case") else 0)
        self.cased = re.compile(d["alias_cased"]) if d.get("alias_cased") else None

    def search(self, text: str):
        return self.rx.search(text) or (self.cased.search(text) if self.cased else None)


def compile_gt(gt: dict) -> list[dict]:
    return [{**d, "rx": _Alias(d), "attr_rx": re.compile(d["attribute_check"], re.I)} for d in gt["datasets"]]


class _Shown:
    """离线重算用：保存下来的送达原句（pid、论文、正文）。"""

    def __init__(self, rows: list[dict]):
        self._rows = {r["pid"]: r for r in rows}

    def get(self, pid: str):
        r = self._rows.get(pid)
        return None if r is None else type("P", (), {"key": f"lib:{r['paper']}", "text": r["text"]})()


def _paper_id(key: str, dups: dict) -> int:
    pid = int(str(key).split(":", 1)[1])
    return int(dups.get(str(pid), pid))


def delivery(index, pids: list[str], datasets: list[dict], relevant: set[int], dups: dict) -> dict:
    """送到模型面前的原文里，标准数据集、用法（论文 × 数据集）、相关论文各被覆盖了多少。"""
    passages = [p for p in (index.get(pid) for pid in dict.fromkeys(pids)) if p is not None]
    per, pair_hit, pair_all = {}, 0, 0
    for d in datasets:
        hits = [p for p in passages if d["rx"].search(p.text)]
        from_papers = {_paper_id(p.key, dups) for p in hits}
        pair_all += len(d["papers"])
        pair_hit += sum(1 for pid in d["papers"] if pid in from_papers)
        per[d["name"]] = {"delivered": bool(hits), "passages": len(hits), "core": d["core"],
                          "gt_papers_covered": sorted(set(d["papers"]) & from_papers)}
    covered_papers = {_paper_id(p.key, dups) for p in passages if any(d["rx"].search(p.text) for d in datasets)}
    core = [d for d in datasets if d["core"]]
    return {"shown": len(passages),
            "dataset_recall": round(sum(v["delivered"] for v in per.values()) / len(datasets), 3),
            "core_recall": round(sum(per[d["name"]]["delivered"] for d in core) / max(len(core), 1), 3),
            "pair_recall": round(pair_hit / max(pair_all, 1), 3),
            "paper_recall": round(len(covered_papers & relevant) / max(len(relevant), 1), 3),
            "missed": [n for n, v in per.items() if not v["delivered"]], "per_dataset": per}


def mentions(text: str, datasets: list[dict]) -> dict:
    """正文写出了哪些标准数据集；虚假特征按“提到该数据集的句子及下一句”里有没有特征关键词核对。"""
    sents = [s for s in _SENT.split(text or "") if s.strip()]
    per = {}
    for d in datasets:
        idx = [i for i, s in enumerate(sents) if d["rx"].search(s)]
        ctx = [" ".join(sents[i:i + 2]) for i in idx]
        per[d["name"]] = {"mentioned": bool(idx), "attribute_ok": any(d["attr_rx"].search(c) for c in ctx),
                          "core": d["core"], "sentences": [sents[i][:400] for i in idx[:2]]}
    hit = [v for v in per.values() if v["mentioned"]]
    core = [v for v in per.values() if v["core"]]
    return {"dataset_recall": round(len(hit) / len(datasets), 3),
            "core_recall": round(sum(v["mentioned"] for v in core) / max(len(core), 1), 3),
            "attribute_ok": round(sum(v["attribute_ok"] for v in hit) / max(len(hit), 1), 3),
            "missed": [n for n, v in per.items() if not v["mentioned"]], "per_dataset": per}


VARIANTS = {
    "v0": {},
    "v1": {"reading": "READING"},
    "v2": {"reading": "READING", "paper_reading": "PAPER_READING", "paper_reading_papers": "READ_PAPERS",
           "paper_notes": "NOTES", "missing_fix": "FIX", "paper_use": "USE"},
    "v3": {"paper_reading": "PAPER_READING", "paper_reading_papers": "READ_PAPERS", "paper_notes": "NOTES",
           "missing_fix": "FIX", "paper_use": "USE"},
}


async def run(args) -> Path:
    from backend.eval.public_benchmarks import _llm_cfg
    from backend.services.deep_research import answer_agent as ag
    from backend.services.deep_research.domain_synthesizer import _llm_json
    from backend.services.deep_research.evidence_search import EvidenceIndex
    from backend.services.llm import start_token_ledger, token_ledger_report

    gt = json.loads(Path(args.gt).read_text(encoding="utf-8"))
    datasets, dups = compile_gt(gt), gt.get("duplicates") or {}
    relevant = set(gt["relevant_papers"])
    question = gt["question"]
    papers = load_library()
    cfg = _llm_cfg(args.thinking_effort, args.model)

    async def model(system: str, user: str) -> dict:
        return await _llm_json(cfg, system, user, max_tokens=6000)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / f"library_recall_{stamp}.json"
    log = ledger.open_log(out)
    started = time.monotonic()
    if args.draft_from:
        stored = json.loads(Path(args.draft_from).read_text(encoding="utf-8"))["draft"]
    else:
        lang = ag.aw.question_language(question)
        stored = await model(ag._DRAFT_SYSTEM, ag._draft_prompt(question, [], lang, 0, opening=True))
        stored = {"draft": str(stored.get("draft") or ""), "works": stored.get("works") or [],
                  "queries": stored.get("queries") or []}
    # 草稿先落盘：后面任何一步出错，都能用 --draft-from 复用它重跑
    out.write_text(json.dumps({"meta": {"started": stamp, "label": args.label, "partial": True}, "draft": stored},
                              ensure_ascii=False, indent=1), encoding="utf-8")

    async def draft_json(system: str, user: str) -> dict:
        return stored

    knobs = {"READING": args.reading, "PAPER_READING": args.paper_reading, "READ_PAPERS": args.read_papers,
             "NOTES": args.paper_notes == "on", "FIX": args.missing_fix, "USE": args.paper_use}
    write = set(filter(None, args.write.split(",")))
    results = {}
    for name in filter(None, args.variants.split(",")):
        settings = {k: knobs[v] for k, v in VARIANTS[name].items()}
        created = []

        class Spy(EvidenceIndex):
            def __init__(self, **kw):
                super().__init__(**kw)
                created.append(self)

        async def routed(system: str, user: str, writing=name in write) -> dict:
            if system == ag._WRITE_SYSTEM and not writing:
                return {"answer": ""}          # 只测送达：不调用定稿
            return await model(system, user)

        t0 = time.monotonic()
        tokens = start_token_ledger()      # 按阶段记这组配置的模型用量（起草固定、不计）
        real, ag.EvidenceIndex = ag.EvidenceIndex, Spy
        try:
            answer = await ag.write_with_pointers(question, papers, llm_json=routed, draft_json=draft_json, mirror=None,
                                                  draft_titles=0, final_mode="attach", opening=True,
                                                  max_attempts=2 if name in write else 1, **settings)
        finally:
            ag.EvidenceIndex = real
        audit, index = answer.get("agent") or {}, created[-1]
        shown = audit.get("shown") or {}
        rec = {"settings": settings, "wall_s": round(time.monotonic() - t0, 1), "tokens": token_ledger_report(tokens),
               "delivery": delivery(index, [p for ch in ("claim", "paper_reading", "reading") for p in shown.get(ch, [])],
                                    datasets, relevant, dups),
               "by_channel": {ch: delivery(index, shown.get(ch, []), datasets, relevant, dups)["dataset_recall"]
                              for ch in ("claim", "paper_reading", "reading")},
               "audit": {k: audit.get(k) for k in ("claims", "passages", "papers", "paper_reading", "reading", "timing",
                                                    "cited_papers", "inventory", "supplement", "write_problems")}}
        # 送到模型面前的原句全文存档：标准答案修订后可 --rescore 离线重算，不必重跑模型
        rec["shown"] = {ch: shown.get(ch, []) for ch in ("claim", "paper_reading", "reading")}
        rec["shown_passages"] = [{"pid": p.pid, "paper": _paper_id(p.key, {}), "text": p.text}
                                 for p in (index.get(pid) for pid in dict.fromkeys(
                                     pid for ch in rec["shown"] for pid in rec["shown"][ch])) if p is not None]
        inv = audit.get("inventory") or {}
        if inv.get("items"):
            # 对象清单的准确程度：多少是标准数据集、多少是通用分布外基准、多少是别的（预训练数据、来源数据集等）
            gt_hit = [n for n in inv["items"] if any(d["rx"].search(n) for d in datasets)]
            ood = [n for n in inv["items"] if n not in gt_hit
                   and any(o.casefold() in n.casefold() for o in gt.get("general_ood") or [])]
            rec["inventory_quality"] = {"items": len(inv["items"]), "gt": len(gt_hit), "general_ood": len(ood),
                                        "other": sorted(set(inv["items"]) - set(gt_hit) - set(ood)),
                                        "missing_after_writing": (inv.get("missing_by_attempt") or [[]])[-1]}
        pr = audit.get("paper_reading") or {}
        if pr.get("selected"):
            sel = {_paper_id(k, dups) for k in pr["selected"]}
            rec["selection_recall"] = round(len(sel & relevant) / len(relevant), 3)
            rec["selection_missed"] = sorted(relevant - sel)
        if name in write:
            rec["answer"] = mentions(answer.get("text") or "", datasets)
            rec["text"] = answer.get("text")
            rec["status"] = answer.get("status")
        results[name] = rec
        d = rec["delivery"]
        print(f"[{name}] 送达：数据集 {d['dataset_recall']}（核心 {d['core_recall']}），用法 {d['pair_recall']}，"
              f"论文 {d['paper_recall']}；未送达 {d['missed']}"
              + (f"\n      答案：数据集 {rec['answer']['dataset_recall']}（核心 {rec['answer']['core_recall']}），"
                 f"特征写对 {rec['answer']['attribute_ok']}；未写 {rec['answer']['missed']}" if "answer" in rec else "")
              + (f"\n      精读选文召回 {rec['selection_recall']}，漏选 {rec['selection_missed']}"
                 if "selection_recall" in rec else "") + f"（{rec['wall_s']} 秒）"
              + "\n      用量：" + "；".join(f"{k} {v['calls']} 次 输入 {v['input_tokens']}（缓存 {v['cached_input_tokens']}）"
                                    f" 输出 {v['output_tokens']}" for k, v in rec["tokens"].items()), flush=True)
    draft_m = mentions(stored["draft"], datasets)
    meta = {"started": stamp, "label": args.label, "question": question, "gt": str(args.gt), "library_papers": len(papers),
            "system_model": cfg.get("model"), "draft_from": args.draft_from or None,
            "knobs": knobs, "wall_min": round((time.monotonic() - started) / 60, 1), "provenance": ledger.provenance(out)}
    summary = {"draft": {k: draft_m[k] for k in ("dataset_recall", "core_recall", "missed")},
               **{n: {"delivery": {k: r["delivery"][k] for k in ("dataset_recall", "core_recall", "pair_recall",
                                                                 "paper_recall")},
                      **({"answer": {k: r["answer"][k] for k in ("dataset_recall", "core_recall", "attribute_ok")}}
                         if "answer" in r else {}), "tokens": r["tokens"]["total"]} for n, r in results.items()}}
    report = {"meta": meta, "summary": summary, "draft": stored, "draft_mentions": draft_m, "variants": results}
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"题目：{question}", f"草稿召回 {draft_m['dataset_recall']}（核心 {draft_m['core_recall']}）"]
    for n, s in summary.items():
        if n == "draft":
            continue
        lines.append(f"{n} 送达 数据集/核心/用法/论文 = {s['delivery']['dataset_recall']}/{s['delivery']['core_recall']}/"
                     f"{s['delivery']['pair_recall']}/{s['delivery']['paper_recall']}"
                     + (f"；答案 数据集/核心/特征 = {s['answer']['dataset_recall']}/{s['answer']['core_recall']}/"
                        f"{s['answer']['attribute_ok']}" if "answer" in s else "")
                     + f"；用量 {s['tokens']['calls']} 次 输入 {s['tokens']['input_tokens']}"
                       f"（缓存 {s['tokens']['cached_input_tokens']}）输出 {s['tokens']['output_tokens']}")
    ledger.record_metrics(out, report, benchmark="library_recall", lines=lines)
    log.close()
    return out


def rescore(path: Path, gt_path: Path) -> dict:
    """按当前标准答案离线重算一次结果的送达与答案指标（用存档的送达原句与答案正文，不调模型）。"""
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    gt = json.loads(Path(gt_path).read_text(encoding="utf-8"))
    datasets, dups, relevant = compile_gt(gt), gt.get("duplicates") or {}, set(gt["relevant_papers"])
    out = {"draft": {k: v for k, v in mentions(report["draft"]["draft"], datasets).items() if k != "per_dataset"}}
    for name, rec in report["variants"].items():
        if "shown_passages" not in rec:
            out[name] = "（这次结果没有存送达原句，不能离线重算送达）"
            continue
        idx = _Shown(rec["shown_passages"])
        pids = [p for ch in ("claim", "paper_reading", "reading") for p in rec["shown"].get(ch, [])]
        d = delivery(idx, pids, datasets, relevant, dups)
        out[name] = {"delivery": {k: d[k] for k in ("dataset_recall", "core_recall", "pair_recall", "paper_recall",
                                                    "missed")}}
        if rec.get("text"):
            a = mentions(rec["text"], datasets)
            out[name]["answer"] = {k: a[k] for k in ("dataset_recall", "core_recall", "attribute_ok", "missed")}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rescore", default="", help="离线重算这次结果的指标（按当前标准答案）")
    p.add_argument("--gt", default=str(GT_DEFAULT))
    p.add_argument("--variants", default="v0,v1,v2")
    p.add_argument("--write", default="v2", help="哪些配置跑定稿并测答案召回（逗号分隔；其余只测送达）")
    p.add_argument("--draft-from", default="", help="复用某次结果里的草稿（固定草稿，只比证据环节）")
    p.add_argument("--reading", type=int, default=30)
    p.add_argument("--paper-reading", type=int, default=6, help="精读：每篇最多挑几处")
    p.add_argument("--read-papers", type=int, default=20, help="精读：最多读几篇（按题名与摘要的相关性选）")
    p.add_argument("--paper-notes", choices=["on", "off"], default="off", help="精读挑选时是否每处写一句要点")
    p.add_argument("--paper-use", choices=["cover", "support"], default="cover",
                   help="精读原文怎么用：cover 列出题目所问的全部对象并逐个写到（本测试是列举题）；support 只支撑、改准草稿")
    p.add_argument("--missing-fix", choices=["supplement", "rewrite"], default="supplement",
                   help="定稿漏写对象清单里的对象时：只补句插回（supplement）或带反馈整篇重写（rewrite）")
    p.add_argument("--thinking-effort", default="off")
    p.add_argument("--model", default="")
    p.add_argument("--label", default="")
    args = p.parse_args()
    if args.rescore:
        rescore(Path(args.rescore), Path(args.gt))
        return
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
