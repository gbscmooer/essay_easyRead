"""评测台账：每次评测自动留存出处、分数、逐题诊断与对比，方便事后比对、定位问题。

一次运行（结果文件 <stem>.json）结束后留下：
  <stem>.json         meta.provenance：git 提交、未提交改动的文件、补丁摘要
  <stem>.patch        有未提交改动时的完整补丁（含新文件），可据此复现当时的代码
  <stem>.log          完整控制台输出 + 流程 WARNING 以上日志（带时间与模块名）
  <stem>.digest.json  逐题明细（分数、细则两半、阶段耗时、召回分段计时、补摘要、筛选、
                      提取、时长上限、限流、错误）与均值、与对照运行的同题配对比较
  LEDGER.jsonl / LEDGER.md（data/eval/ 下）各追加一条，按时间顺序累积

对照运行缺省取台账里同一评测、同一抽样（seed、题数）的上一条；rewrite 取其来源文件。

用法（补登记已有结果、或任意两次对比）：
  python -m backend.eval.ledger add data/eval/scholarqa_cs_<stamp>.json [--against <另一个结果>]
  python -m backend.eval.ledger compare <结果A> <结果B>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import statistics
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data/eval"
LEDGER_JSONL = OUT_DIR / "LEDGER.jsonl"
LEDGER_MD = OUT_DIR / "LEDGER.md"


# ── 出处 ────────────────────────────────────────────────────────────────────

def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def provenance(out: Path) -> dict:
    """当前代码版本；有未提交改动（含未跟踪的新文件）时把补丁存为 <stem>.patch。"""
    status = [line for line in _git("status", "--porcelain", "--", "backend", "frontend").splitlines() if line]
    patch = _git("diff", "HEAD", "--", "backend", "frontend")
    for line in status:
        if line.startswith("??"):
            path = line[3:].strip()
            patch += _git("diff", "--no-index", "--", "/dev/null", path)
    info = {"commit": _git("rev-parse", "--short", "HEAD").strip(),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD").strip(),
            "dirty_files": [line[3:].strip() for line in status]}
    if patch.strip():
        info["patch_sha1"] = hashlib.sha1(patch.encode("utf-8")).hexdigest()[:12]
        patch_path = out.with_suffix(".patch")
        patch_path.write_text(patch, encoding="utf-8")
        info["patch"] = patch_path.name
    return info


# ── 日志 ────────────────────────────────────────────────────────────────────

class _Tee:
    def __init__(self, stream, sink):
        self.stream, self.sink = stream, sink

    def write(self, data):
        self.stream.write(data)
        self.sink.write(data)
        return len(data)

    def flush(self):
        self.stream.flush()
        self.sink.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


# 评测设置本身造成、每次都有的警告（如评测里关掉 OpenAlex），不写进日志以免淹没真问题
_EXPECTED = ("disabled for this evaluation",)


class RunLog:
    """控制台输出原样复制到 <stem>.log；流程日志（WARNING 以上）带时间与模块名写入同一文件。"""

    def __init__(self, out: Path):
        self.path = out.with_suffix(".log")
        self._sink = self.path.open("a", encoding="utf-8")
        self._stdout, self._stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = _Tee(sys.stdout, self._sink), _Tee(sys.stderr, self._sink)
        self._handler = logging.StreamHandler(self._sink)
        self._handler.setLevel(logging.WARNING)
        self._handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        self._handler.addFilter(lambda rec: not any(q in rec.getMessage() for q in _EXPECTED))
        root = logging.getLogger()
        root.addHandler(self._handler)
        if root.level > logging.WARNING or root.level == logging.NOTSET:
            root.setLevel(logging.WARNING)

    def close(self) -> None:
        logging.getLogger().removeHandler(self._handler)
        sys.stdout, sys.stderr = self._stdout, self._stderr
        self._sink.close()


def open_log(out: Path) -> RunLog:
    return RunLog(out)


# ── 逐题明细 ────────────────────────────────────────────────────────────────

def _mean(values) -> float | None:
    vals = [v for v in values if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 4) if vals else None


def _score_parts(scores: list[dict]) -> dict:
    """同一题各评分配置取平均；细则拆成“标准 / 依据”两半（必备、加分各一组）。"""
    halves = {"must_criterion": [], "must_evidence": [], "nice_criterion": [], "nice_evidence": []}
    for s in scores:
        for k, v in s.items():
            group = "must" if k.startswith("most_important") else "nice" if k.startswith("nice_to_have") else ""
            if group:
                halves[f"{group}_{'evidence' if k.endswith('_evidence') else 'criterion'}"].append(v)
    out = {k: _mean(s.get(k) for s in scores)
           for k in ("score", "ann_score", "length", "expertise", "citations", "excerpts")}
    out.update({k: _mean(v) for k, v in halves.items()})
    return out


def _run_details(saved: str | None) -> dict:
    """从保存的单题运行记录里取诊断字段；文件缺失时返回空。"""
    if not saved or not Path(saved).exists():
        return {}
    try:
        result = json.loads(Path(saved).read_text(encoding="utf-8")).get("result") or {}
    except (OSError, ValueError):
        return {}
    sources = result.get("sources") or {}
    recall = sources.get("recall") or {}
    filt = recall.get("filter") or {}
    extract = result.get("report_extract") or {}
    extractions = extract.get("extractions") or []
    if isinstance(extractions, dict):
        extractions = list(extractions.values())
    timeline = sources.get("stage_timeline") or []
    loops = sources.get("agent_loops") or {}
    exhaustion = (result.get("budget") or {}).get("exhaustion_log") or []
    answer = result.get("answer") or {}
    return {
        "stages_s": {row.get("stage"): row.get("duration_s") for row in timeline},
        "stage_failed": [f"{row.get('stage')}: {row.get('error')}"[:200] for row in timeline
                         if row.get("status") == "failed"],
        "loop_stops": {k: v.get("stop_reason") for k, v in loops.items() if isinstance(v, dict)},
        "recall": {
            "stop_reason": recall.get("stop_reason"),
            "corpus_after_search": recall.get("corpus_after_search"),
            "final_count": recall.get("final_count"),
            "timing_s": recall.get("timing_s") or {},
            "backfill": recall.get("abstract_backfill") or {},
            "filter": {k: filt.get(k) for k in ("batches", "kept", "rejected", "fail_open_batches",
                                                 "confirmed", "title_confirmed", "unknown")},
        },
        "extract": {
            "selected": len(extract.get("selected_keys") or []),
            "status": dict(Counter((e or {}).get("status") for e in extractions)),
            "evidence_records": len((extract.get("evidence_store") or {}).get("records") or []),
            "download": dict(Counter((d or {}).get("status") for d in extract.get("download_audit") or [])),
        },
        "answer": {"materials": answer.get("materials"), "material_limit": answer.get("material_limit"),
                   "gap_added": (answer.get("gap_retrieval") or {}).get("added"),
                   "problems": ((answer.get("check") or {}).get("problems") or [])[:5],
                   "aspect_quotes": answer.get("aspect_quotes"),
                   "verification": {k: v for k, v in (answer.get("verification") or {}).items()
                                    if k != "unsupported"} or None},
        "time_cap_hits": [e.get("resource") for e in exhaustion
                          if isinstance(e, dict) and str(e.get("resource", "")).startswith("time_cap")],
        "tokens": (sources.get("token_usage") or {}).get("total") or {},
        "integrity": (result.get("integrity") or {}).get("level"),
        "source_warnings": len(sources.get("warnings") or []),
    }


def question_rows(report: dict, arm: str = "system") -> list[dict]:
    rows = []
    for i, rec in enumerate(report.get("records") or [], 1):
        sysrec = rec.get(arm) or {}
        scores = sysrec.get("scores") or []
        rows.append({
            "i": i, "question": rec.get("question"),
            **(_score_parts(scores) if scores else {"score": None}),
            "words": sysrec.get("words"), "status": sysrec.get("status"), "wall_s": sysrec.get("wall_s"),
            "error": sysrec.get("error") or sysrec.get("judge_error"),
            "rate_limited": bool(sysrec.get("rate_limited")) or (sysrec.get("attempts") or 1) > 1,
            "details": _run_details(sysrec.get("saved")),
        })
    return rows


def _flatten(d: dict, prefix: str = "") -> dict:
    flat = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            flat.update(_flatten(v, key + "."))
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            flat[key] = v
    return flat


def aggregate(rows: list[dict]) -> dict:
    """数值字段（含嵌套诊断）逐项求均值；分数只统计有分的题。"""
    scored = [r for r in rows if r.get("score") is not None]
    flat = [_flatten({k: v for k, v in r.items() if k not in ("i",)}) for r in scored]
    keys = sorted({k for f in flat for k in f})
    out = {k: _mean(f.get(k) for f in flat) for k in keys}
    walls = [r["wall_s"] for r in scored if isinstance(r.get("wall_s"), (int, float))]
    out.update({"answered": len(scored), "of": len(rows),
                "failed": sum(1 for r in rows if r.get("score") is None),
                "rate_limited": sum(1 for r in rows if r.get("rate_limited")),
                "time_cap_hits": sum(1 for r in rows if (r.get("details") or {}).get("time_cap_hits")),
                "wall_s_max": max(walls) if walls else None})
    return out


# ── 对比 ────────────────────────────────────────────────────────────────────

_COMPARE_KEYS = ("score", "ann_score", "must_criterion", "must_evidence", "nice_criterion", "nice_evidence",
                 "citations", "excerpts", "words", "wall_s")


def compare(rows: list[dict], ref_rows: list[dict]) -> dict:
    """同题配对：两边都有分的题才参与；差值标准误用于判断是否超出运行间波动。"""
    ref = {r["question"]: r for r in ref_rows}
    pairs = [(r, ref[r["question"]]) for r in rows
             if r["question"] in ref and r.get("score") is not None and ref[r["question"]].get("score") is not None]
    out: dict = {"paired": len(pairs),
                 "newly_failed": [r["i"] for r in rows if r.get("score") is None
                                  and (ref.get(r["question"]) or {}).get("score") is not None],
                 "fixed": [r["i"] for r in rows if r.get("score") is not None
                           and r["question"] in ref and ref[r["question"]].get("score") is None]}
    if not pairs:
        return out
    diffs = [a["score"] - b["score"] for a, b in pairs]
    out.update({"mean": _mean(a["score"] for a, _ in pairs), "ref_mean": _mean(b["score"] for _, b in pairs),
                "diff": round(statistics.mean(diffs), 4),
                "se": round(statistics.stdev(diffs) / len(diffs) ** 0.5, 4) if len(diffs) > 1 else None})
    out["components"] = {k: round((_mean(a.get(k) for a, _ in pairs) or 0) - (_mean(b.get(k) for _, b in pairs) or 0), 4)
                         for k in _COMPARE_KEYS[1:]}
    moved = sorted(((round(a["score"] - b["score"], 4), a["i"], (a["question"] or "")[:60]) for a, b in pairs),
                   reverse=True)
    out["largest_gains"] = moved[:3]
    out["largest_drops"] = moved[-3:][::-1]
    return out


# ── 台账 ────────────────────────────────────────────────────────────────────

def _entries() -> list[dict]:
    """台账条目，按运行开始时间排序。"""
    if not LEDGER_JSONL.exists():
        return []
    rows = [json.loads(line) for line in LEDGER_JSONL.read_text(encoding="utf-8").splitlines() if line.strip()]
    return sorted(rows, key=lambda e: e.get("time", ""))


def _run_time(meta: dict) -> str:
    stamp = meta.get("rewritten") or meta.get("started") or ""
    try:
        return datetime.strptime(stamp, "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return datetime.now().strftime("%Y-%m-%d %H:%M")


def _reference(out: Path, meta: dict, benchmark: str) -> Path | None:
    if meta.get("rewrite_of"):
        return Path(meta["rewrite_of"])
    before = [e for e in _entries() if e.get("time", "") < _run_time(meta)]
    for entry in reversed(before):
        # 跳过大半题没答出的运行（如撞额度的那轮），否则配对题数太少
        complete = (entry.get("answered") or 0) >= 0.8 * (entry.get("of") or 1)
        same = (entry.get("benchmark") == benchmark and entry.get("kind") == "run" and complete
                and entry.get("seed") == meta.get("seed") and entry.get("n") == meta.get("n"))
        if same and entry.get("file") != out.name and (OUT_DIR / entry["file"]).exists():
            return OUT_DIR / entry["file"]
    return None


def _fmt(v, digits: int = 3) -> str:
    return "—" if v is None else f"{v:.{digits}f}" if isinstance(v, float) else str(v)


def _markdown(entry: dict, agg: dict, cmp: dict | None) -> str:
    m, p = entry["meta"], entry["meta"].get("provenance") or {}
    t = {k.split(".")[-1]: agg.get(k) for k in agg if k.startswith("details.recall.timing_s.")}
    stages = {k.split(".")[-1]: agg.get(k) for k in agg if k.startswith("details.stages_s.")}
    lines = [
        f"## {entry['time']} {entry['file']}" + (f" — {m.get('label')}" if m.get("label") else ""),
        "",
        f"- 代码：{p.get('commit') or '?'}" + (f"，未提交改动 {len(p.get('dirty_files') or [])} 个文件"
                                              f"（补丁 {p.get('patch')}）" if p.get("dirty_files") else "，无未提交改动"),
        f"- 设置：{entry['kind']}，{m.get('depth')}，系统模型 {m.get('system_model')}，评委 {m.get('judge_model')}，"
        f"并发 {m.get('concurrency')}" + (f"，材料上限 {m.get('materials')}" if m.get("materials") else "")
        + (f"，整轮 {m.get('wall_min')} 分" if m.get("wall_min") else ""),
        f"- 分数：综合 {_fmt(agg.get('score'))}（{agg['answered']}/{agg['of']} 题），细则 {_fmt(agg.get('ann_score'))}；"
        f"必备 标准/依据 {_fmt(agg.get('must_criterion'))}/{_fmt(agg.get('must_evidence'))}；"
        f"加分 {_fmt(agg.get('nice_criterion'))}/{_fmt(agg.get('nice_evidence'))}；"
        f"引用 {_fmt(agg.get('citations'))}，摘录 {_fmt(agg.get('excerpts'))}，平均 {_fmt(agg.get('words'), 0)} 词",
        f"- 耗时：单题平均 {_fmt(agg.get('wall_s'), 0)} 秒（最长 {_fmt(agg.get('wall_s_max'), 0)}）；阶段 "
        + "、".join(f"{k} {_fmt(v, 0)}" for k, v in stages.items() if v)
        + "；召回 " + "、".join(f"{k} {_fmt(v, 0)}" for k, v in t.items()),
        f"- 异常：失败 {agg['failed']} 题，限流重跑 {agg['rate_limited']} 题，时长上限触发 {agg['time_cap_hits']} 题；"
        f"模型并发占用 {_fmt(((m.get('model_limiter') or {}).get('utilization')), 2)}",
    ]
    if cmp:
        c = cmp["compare"]
        lines.append(f"- 对照 {cmp['against']}（同题 {c['paired']} 道）：{_fmt(c.get('ref_mean'))} → {_fmt(c.get('mean'))}，"
                     f"差 {c.get('diff', 0):+.3f}（标准误 {_fmt(c.get('se'))}）"
                     + (f"；新失败 {c['newly_failed']}" if c.get("newly_failed") else "")
                     + (f"；恢复 {c['fixed']}" if c.get("fixed") else ""))
        if c.get("components"):
            lines.append("  - 分项差：" + "，".join(f"{k} {v:+.3f}" for k, v in c["components"].items()))
            lines.append("  - 升幅最大：" + "；".join(f"第{i}题 {d:+.3f} {q}" for d, i, q in c["largest_gains"]))
            lines.append("  - 降幅最大：" + "；".join(f"第{i}题 {d:+.3f} {q}" for d, i, q in c["largest_drops"]))
    return "\n".join(lines) + "\n\n"


def record(out: Path, report: dict | None = None, *, benchmark: str = "scholarqa_cs",
           against: Path | None = None) -> dict:
    """写 <stem>.digest.json 并在台账追加一条；返回 digest。重复登记同一文件时替换旧条目。"""
    report = report or json.loads(out.read_text(encoding="utf-8"))
    meta = report.get("meta") or {}
    rows = question_rows(report)
    agg = aggregate(rows)
    # 综合分与评测自身的汇总口径一致（按评分配置平均）；逐题配对仍用每题各配置的平均
    official = (report.get("summary") or {}).get("system") or {}
    agg.update({k: official[k] for k in ("score", "ann_score") if official.get(k) is not None})
    ref_path = against or _reference(out, meta, benchmark)
    cmp = None
    if ref_path is not None and ref_path.exists():
        cmp = {"against": ref_path.name,
               "compare": compare(rows, question_rows(json.loads(ref_path.read_text(encoding="utf-8"))))}
    digest = {"file": out.name, "meta": meta, "aggregate": agg, "comparison": cmp, "questions": rows}
    out.with_suffix(".digest.json").write_text(json.dumps(digest, ensure_ascii=False, indent=1), encoding="utf-8")

    entry = {"time": _run_time(meta), "benchmark": benchmark,
             "kind": "rewrite" if meta.get("rewrite_of") else "run", "file": out.name,
             "seed": meta.get("seed"), "n": meta.get("n"), "label": meta.get("label"),
             "commit": (meta.get("provenance") or {}).get("commit"),
             "score": agg.get("score"), "answered": agg["answered"], "of": agg["of"],
             "against": cmp["against"] if cmp else None,
             "diff": (cmp or {}).get("compare", {}).get("diff"), "se": (cmp or {}).get("compare", {}).get("se")}
    entry["md"] = _markdown({**entry, "meta": meta}, agg, cmp)
    entries = sorted([e for e in _entries() if e.get("file") != out.name] + [entry], key=lambda e: e["time"])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LEDGER_JSONL.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries), encoding="utf-8")
    # LEDGER.md 由台账整体重建：同一文件重复登记只保留最新一条
    LEDGER_MD.write_text("# 评测台账（自动生成，勿手改；说明见 backend/eval/ledger.py）\n\n"
                         + "".join(e.get("md", "") for e in entries), encoding="utf-8")
    print(entry["md"], flush=True)
    return digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="登记已有结果（生成 digest、追加台账）")
    a.add_argument("file")
    a.add_argument("--against", default=None)
    a.add_argument("--benchmark", default="scholarqa_cs")
    c = sub.add_parser("compare", help="只打印两次结果的同题配对比较")
    c.add_argument("file")
    c.add_argument("against")
    args = parser.parse_args()
    if args.cmd == "add":
        record(Path(args.file), benchmark=args.benchmark, against=Path(args.against) if args.against else None)
    else:
        load = lambda p: question_rows(json.loads(Path(p).read_text(encoding="utf-8")))  # noqa: E731
        print(json.dumps(compare(load(args.file), load(args.against)), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
