"""领域探索评测 harness；入口与口径见 docs/DEEP_RESEARCH_EVAL.md。

系统臂在临时 SQLite 上执行当前 explorer，基线臂使用相同模型仅凭参数记忆回答。
两臂同时有检索、上下文组织、调用数量等差异，不能把分数差只归因于检索，
也不能代替同工具 agent 对照。

指标：verdict 名义标签 exact-match 与分类报告；DOI 可解析率；材料/引用年份。
DOI 可解析不代表结论支持，年份不能单独证明答案质量。questions.expected
是人工草案，正式出数前需校核。除 --list 外，运行可能调用真实来源与模型。

使用：python -m backend.eval.explore_eval --list / --only Q1,Q10 / --skip-baseline。
结果落 data/eval/。显式支持标注见 backend.eval.survey_metrics；新对照协议见
 docs/workflow_refactor/VALIDATION.md。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from backend.eval.survey_metrics import PROVENANCE, REFERENCE_CAVEAT, nominal_report

VERDICT_LABELS = ("unclear", "gap", "emerging", "done_partial", "done_extensively")
NO_RECALL = "no_recall"          # 预期「检索未命中」的题（铁律：不得声称 gap）
_BUNDLED_QUESTIONS = Path(__file__).parent / "explore_questions.json"


# ── 载入与计分 ────────────────────────────────────────────────────────────────

def load_questions(path: Path | None = None) -> list[dict]:
    data = json.loads((path or _BUNDLED_QUESTIONS).read_text(encoding="utf-8"))
    out = []
    for q in data.get("questions") or []:
        if not (q.get("id") and q.get("question")):
            raise ValueError(f"题目缺 id/question: {q}")
        expected = q.get("expected")
        if expected not in VERDICT_LABELS and expected != NO_RECALL:
            raise ValueError(f"{q['id']} 预期判定非法: {expected}")
        out.append(q)
    return out


def score_verdict(expected: str, actual: str | None) -> str:
    """名义标签：exact / miss / no_verdict；保留 no_recall 的 correct/miss。"""
    if expected not in (*VERDICT_LABELS, NO_RECALL):
        raise ValueError(f"Unknown reference verdict: {expected!r}")
    if actual == NO_RECALL:
        # 只有 no_recall 题才算对：普通题拿不到召回=系统侧失败（网络/检索源），记 miss
        return "correct" if expected == NO_RECALL else "miss"
    if expected == NO_RECALL:
        return "miss"
    if not actual:
        return "no_verdict"
    if actual == expected:
        return "exact"
    return "miss"


def median_year(years: list) -> float | None:
    ys = [y for y in years if isinstance(y, int) and y > 1900]
    return float(statistics.median(ys)) if ys else None


def _norm_doi(doi) -> str | None:
    d = str(doi or "").strip().replace("https://doi.org/", "")
    return d or None


# ── 系统臂：完整 explorer 流水线（独立临时库，不污染业务库）─────────────────

def _fresh_engine(td: str):
    from sqlmodel import SQLModel, create_engine

    # 显式导入全部表模型（与 tests/conftest.py 的建表集一致）
    from backend.models.analysis import AnalysisResult  # noqa: F401
    from backend.models.annotation import Annotation  # noqa: F401
    from backend.models.code_analysis import CodeAnalysis  # noqa: F401
    from backend.models.exploration import ExplorationRun
    from backend.models.note import Note  # noqa: F401
    from backend.models.paper import Paper  # noqa: F401
    from backend.models.research import PaperReference, ResearchGraph, ResearchRun  # noqa: F401
    from backend.models.tag import PaperTag, Tag  # noqa: F401
    from backend.models.workspace import Workspace, WorkspacePaper  # noqa: F401

    engine = create_engine(f"sqlite:///{td}/eval.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


async def run_system_arm(question: str, depth: str = "standard") -> dict:
    """真实跑一轮领域探索（真 LLM + 真检索源），提取可比字段。"""
    import tempfile

    from sqlmodel import Session

    from backend.models.exploration import ExplorationRun
    from backend.services.deep_research import explorer

    with tempfile.TemporaryDirectory(prefix="explore_eval_") as td:
        engine = _fresh_engine(td)
        original = explorer.get_engine
        explorer.get_engine = lambda: engine
        try:
            with Session(engine) as s:
                run = ExplorationRun(question=question, depth=depth or "standard")
                s.add(run)
                s.commit()
                s.refresh(run)
                run_id = run.id
            t0 = time.monotonic()
            await explorer.run_exploration(run_id)
            wall = round(time.monotonic() - t0, 1)
            with Session(engine) as s:
                run = s.get(ExplorationRun, run_id)
                result = run.result or {}
                run_failed = run.status == "failed"
                token_total = (((run.result or {}).get("sources") or {}).get("token_usage") or {}).get("total")
                run_error = run.error_message
        finally:
            explorer.get_engine = original
            engine.dispose()

    papers = result.get("papers") or []
    metrics = result.get("metrics") or {}
    synthesis = result.get("synthesis") or {}
    verdict = synthesis.get("verdict") or {}
    total = metrics.get("total") or len(papers)
    doi_by_key = {p.get("key"): _norm_doi(p.get("doi")) for p in papers}
    rec = {
        "verdict": verdict.get("status"),
        "confidence": verdict.get("confidence"),
        "total": total,
        "provider": result.get("provider", ""),
        "median_year": median_year([p.get("year") for p in papers]),
        "recent_share": metrics.get("recent_share"),
        "evidence_n": len(verdict.get("evidence_keys") or []),
        "evidence_dois": [
            d for d in (doi_by_key.get(k) for k in (verdict.get("evidence_keys") or [])) if d
        ],
        "partial": bool(synthesis.get("partial")),
        "fallback_decompose": bool((result.get("decompose") or {}).get("fallback")),
        "wall_s": wall,
        "tokens": token_total,
        "error": None,
    }
    # 运行失败（如检索式规划超时）不是「检索未命中」：记错误，不映射为 no_recall
    if run_failed:
        rec["error"] = f"run failed: {run_error or 'unknown'}"[:300]
        return rec
    # 零召回题：系统应如实走「检索未命中」通道（无 verdict），不算综合失败
    if total <= 3 and not rec["verdict"]:
        rec["verdict"] = NO_RECALL
    return rec


# ── 基线臂：同一 LLM 仅凭记忆回答；不代表 GPT 网页联网回答 ─────────────────

_BASELINE_SYSTEM = (
    "你是学术研究顾问。仅凭你已有的参数知识回答（你无法联网检索，也不要假装检索过）。"
    "对不确定的信息如实标注，禁止编造论文。只输出 JSON，不要输出任何其他文字。"
)

_BASELINE_USER = """## 研究问题
{q}

## 输出格式
```json
{{"verdict": "done_extensively|done_partial|emerging|gap|unclear（该 idea/方向是否已被实现：充分实现/部分实现/近1~2年新兴/未见实现/不确定）",
 "confidence": 0.0~1.0,
 "explanation": "2~3 句判断依据",
 "cited_papers": [{{"title": "论文标题", "year": 2021, "doi": "10.xxxx/yyy 或 null"}}]（支撑判断的代表论文，最多 8 篇，凭记忆给出；不确定的少列或不列）}}
```"""


async def ask_baseline(question: str, llm_cfg: dict) -> dict:
    """裸 LLM 回答同题。返回与系统臂同形的记录（含引用论文 DOI/年份）。"""
    from backend.services.deep_research.domain_synthesizer import _llm_json

    t0 = time.monotonic()
    meta: dict = {}
    data = await _llm_json(
        llm_cfg, _BASELINE_SYSTEM, _BASELINE_USER.format(q=question), max_tokens=6000,
        metadata_out=meta,
    )
    wall = round(time.monotonic() - t0, 1)
    v = data.get("verdict")
    verdict = v if v in VERDICT_LABELS else None
    try:
        conf = max(0.0, min(1.0, float(data.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        conf = 0.0
    cited = [c for c in (data.get("cited_papers") or []) if isinstance(c, dict)][:8]
    dois = [d for d in (_norm_doi(c.get("doi")) for c in cited) if d]
    years = []
    for c in cited:
        try:
            years.append(int(c.get("year")))
        except (TypeError, ValueError):
            pass
    return {
        "verdict": verdict,
        "confidence": round(conf, 2),
        "explanation": str(data.get("explanation") or "")[:300],
        "cited_n": len(cited),
        "cited_dois": dois,
        "median_cited_year": median_year(years),
        "wall_s": wall,
        "tokens": meta.get("usage") or None,
        "error": None,
    }


async def verify_dois(dois: list[str]) -> tuple[int, int]:
    """返回 (resolved, attempted)，仅检查解析；失败可能是缺索引或网络错误。"""
    from backend.services.deep_research.providers.openalex import get_shared_openalex_provider

    if not dois:
        return 0, 0
    oa = get_shared_openalex_provider()
    resolved = 0
    for doi in dois:
        try:
            work = await oa._safe_get(f"/works/doi:{doi}", {"select": "id"})
            if work and work.get("id"):
                resolved += 1
        except Exception:
            pass
    return resolved, len(dois)


# ── 汇总与报告 ────────────────────────────────────────────────────────────────

def build_report(meta: dict, records: list[dict]) -> dict:
    n = len(records)

    def _classification(side: str) -> dict:
        included = [r for r in records if r.get(side) is not None and not r[side].get("skipped")]
        scores = [score_verdict(r["expected"], r[side].get("verdict")) for r in included]
        classification = nominal_report(
            [r["expected"] for r in included], [r[side].get("verdict") for r in included],
            (*VERDICT_LABELS, NO_RECALL),
        )
        return {
            "n_evaluated": len(included), "n_skipped": n - len(included),
            "exact": scores.count("exact"), "miss": scores.count("miss"),
            "no_verdict": scores.count("no_verdict"), "no_recall_correct": scores.count("correct"),
            "strict_accuracy": round(classification["accuracy"], 3) if included else None,
            "classification": classification,
        }

    def _doi_counts(side: str, ids_key: str, resolved_key: str) -> tuple[int, int, int]:
        resolved = attempted = unchecked = 0
        for record in records:
            arm = record.get(side) or {}
            if arm.get("skipped"):
                continue
            count = len(arm.get(ids_key) or [])
            value = arm.get(resolved_key)
            if isinstance(value, int) and 0 <= value <= count:
                resolved += value
                attempted += count
            else:
                unchecked += count
        return resolved, attempted, unchecked

    sys_doi_resolved, sys_doi_total, sys_unchecked = _doi_counts("system", "evidence_dois", "evidence_doi_resolved")
    base_doi_resolved, base_doi_total, base_unchecked = _doi_counts("baseline", "cited_dois", "doi_resolved")
    sys_years = [r["system"]["median_year"] for r in records
                 if (r.get("system") or {}).get("median_year") is not None]
    base_years = [r["baseline"]["median_cited_year"] for r in records
                  if (r.get("baseline") or {}).get("median_cited_year") is not None]

    def _rate(num: int, den: int):
        return round(num / den, 3) if den else None

    return {
        "meta": meta,
        "evaluation_notes": {
            "provenance": dict(PROVENANCE),
            "reference_set_caveat": REFERENCE_CAVEAT,
            "verdict": "Nominal exact matching; expected labels require independent review",
            "citation_existence": "DOI resolution only; unresolved may mean missing index coverage or service failure",
            "evidence_support": "Not assessed by this live harness; use survey_metrics with explicit support judgments",
            "confidence": "Model self-report, not a calibrated probability of correctness",
        },
        "summary": {
            "n_questions": n,
            "system": {
                **_classification("system"),
                "evidence_doi_resolved": f"{sys_doi_resolved}/{sys_doi_total}",
                "evidence_doi_resolve_rate": _rate(sys_doi_resolved, sys_doi_total),
                "evidence_doi_unchecked": sys_unchecked,
                "median_corpus_year_avg": round(statistics.mean(sys_years), 1) if sys_years else None,
            },
            "baseline": {
                **_classification("baseline"),
                "cited_doi_resolved": f"{base_doi_resolved}/{base_doi_total}",
                "cited_doi_resolve_rate": _rate(base_doi_resolved, base_doi_total),
                "cited_doi_unchecked": base_unchecked,
                "median_cited_year_avg": round(statistics.mean(base_years), 1) if base_years else None,
            },
        },
        "records": records,
    }


def render_markdown(report: dict) -> str:
    m, s = report["meta"], report["summary"]
    sys_, base = s["system"], s["baseline"]

    def _macro(arm: dict) -> str:
        report = arm["classification"]["report"]
        if report is None:
            return "未评测"
        avg = report["macro avg"]
        return " / ".join(f"{avg[key]:.3f}" for key in ("precision", "recall", "f1-score"))

    lines = [
        "# 领域探索评测报告（系统 vs 仅凭记忆的 LLM 基线）",
        "",
        f"- 日期：{m['date']}　题集：{m['questions_version']}（{s['n_questions']} 题）　"
        f"模型：{m['model']}",
        "",
        "## 汇总",
        "",
        "| 指标 | 系统（真实检索 + LLM 综合） | 基线（同一 LLM 仅凭记忆） |",
        "|---|---|---|",
        f"| 判定严格准确 | {sys_['strict_accuracy']}（exact {sys_['exact']} + 未命中如实 {sys_['no_recall_correct']}） | {base['strict_accuracy']} |",
        f"| 宏平均 Precision / Recall / F1 | {_macro(sys_)} | {_macro(base)} |",
        f"| 已评测 / 已跳过 | {sys_['n_evaluated']} / {sys_['n_skipped']} | {base['n_evaluated']} / {base['n_skipped']} |",
        f"| 失误 / 无判定 | {sys_['miss']} / {sys_['no_verdict']} | {base['miss']} / {base['no_verdict']} |",
        f"| DOI 解析成功 / 已尝试 | {sys_['evidence_doi_resolved']} | {base['cited_doi_resolved']} |",
        f"| DOI 未检查 | {sys_['evidence_doi_unchecked']} | {base['cited_doi_unchecked']} |",
        f"| 语料/引用中位年份（均值） | {sys_['median_corpus_year_avg']} | {base['median_cited_year_avg']}（受训练截止冻结） |",
        "",
        "> 预期判定为人工标注草案；各标签为名义类别，只计完全匹配。宏平均固定包含六类，缺席类计零。",
        "> DOI 解析成功只说明能定位论文，不说明论文支持结论；解析失败也不能证明论文不存在。",
        "> 证据支持尚未评测；使用 survey_metrics 的显式支持标注入口。模型自报 confidence 未经过概率校准。",
        "> 参考文献集并非完整相关集，未被引用不等于不相关。",
        "",
        "## 逐题",
        "",
        "| 题 | 预期 | 系统 | 基线 | 系统语料 | 系统证据 | 基线引用 | 备注 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in report["records"]:
        sys_r, base_r = r.get("system") or {}, r.get("baseline") or {}
        note = r.get("note", "")
        if sys_r.get("fallback_decompose"):
            note += " ⚠回退拆解"
        if sys_r.get("partial"):
            note += " ⚠综合部分失败"
        if sys_r.get("error"):
            note += f" ⚠{sys_r['error'][:40]}"
        sys_v = sys_r.get("verdict") or "—"
        if sys_v == NO_RECALL:
            sys_v = "检索未命中(如实)"
        conf = sys_r.get("confidence")
        sys_cell = f"{sys_v}" + (f" conf{conf}" if conf is not None else "")
        base_cell = base_r.get("verdict") or ("⚠" + base_r["error"][:20] if base_r.get("error") else "—")
        corpus = f"{sys_r.get('total', '—')}篇/{sys_r.get('provider') or '?'}"
        sys_ev = f"{sys_r.get('evidence_n', 0)}条(DOI核验{sys_r.get('evidence_doi_resolved', '—')}/{len(sys_r.get('evidence_dois') or [])})"
        base_ev = f"{base_r.get('cited_n', 0)}篇(DOI核验{base_r.get('doi_resolved', '—')}/{len(base_r.get('cited_dois') or [])})"
        lines.append(
            f"| {r['id']} | {r['expected']} | {sys_cell} | {base_cell} | {corpus} | {sys_ev} | {base_ev} | {note} |"
        )
    return "\n".join(lines) + "\n"


# ── 编排 ──────────────────────────────────────────────────────────────────────

async def evaluate(
    questions: list[dict], *, skip_system: bool = False, skip_baseline: bool = False,
    on_record=None,
) -> list[dict]:
    """逐题跑两臂并计分。顺序执行：探索内部已并行，多题并发会挤压检索源限速。"""
    from backend.services.ai_config import get_analysis_config

    llm_cfg = get_analysis_config()
    records: list[dict] = []
    for q in questions:
        print(f"[{q['id']}] {q['question']}")
        rec: dict = {"id": q["id"], "question": q["question"], "expected": q["expected"],
                     "category": q.get("category", ""), "note": ""}
        if not skip_system:
            try:
                rec["system"] = await run_system_arm(q["question"], q.get("depth") or "standard")
                # 瞬时抖动重跑：① decompose 连接失败→回退中文原句→Scopus 400 零召回；
                # ② 有真实召回（total>3）但综合被服务端劣化打死（无 verdict）。
                # 两种都是环境故障而非系统真实表现，有 Key 时重跑一次（记录在案）
                sys0 = rec["system"]
                if (not sys0.get("verdict") and (sys0.get("fallback_decompose") or (sys0.get("total") or 0) > 3)
                        and llm_cfg.get("api_key")):
                    rec["note"] = "系统臂重试 1 次（拆解回退/综合失败）"
                    rec["system"] = await run_system_arm(q["question"], q.get("depth") or "standard")
            except Exception as e:  # noqa: BLE001 —— 单题失败不中断评测
                rec["system"] = {"error": f"{type(e).__name__}: {e}"[:300]}
        if not skip_baseline:
            if llm_cfg.get("api_key"):
                try:
                    rec["baseline"] = await ask_baseline(q["question"], llm_cfg)
                except Exception as e:  # noqa: BLE001
                    # 服务端瞬时劣化（连续空回复）不算基线的真实水平，歇一下重试一次
                    await asyncio.sleep(20)
                    try:
                        rec["baseline"] = await ask_baseline(q["question"], llm_cfg)
                        rec["note"] = (rec.get("note", "") + " 基线臂重试 1 次").strip()
                    except Exception as e2:  # noqa: BLE001
                        rec["baseline"] = {"error": f"{type(e2).__name__}: {e2}"[:300]}
            else:
                rec["baseline"] = {"error": "未配置 LLM Key"}

        # DOI 核验（两臂同口径：OpenAlex 能否解析）
        if rec.get("system", {}).get("evidence_dois"):
            try:
                resolved, _total = await verify_dois(rec["system"]["evidence_dois"])
                rec["system"]["evidence_doi_resolved"] = resolved
            except Exception:
                rec["system"]["evidence_doi_resolved"] = None
        if rec.get("baseline", {}).get("cited_dois"):
            try:
                resolved, _total = await verify_dois(rec["baseline"]["cited_dois"])
                rec["baseline"]["doi_resolved"] = resolved
            except Exception:
                rec["baseline"]["doi_resolved"] = None

        rec["system"] = rec.get("system") or {}
        if skip_system:
            rec["system"]["skipped"] = True
        rec["system"]["score"] = score_verdict(q["expected"], rec["system"].get("verdict"))
        rec["baseline"] = rec.get("baseline") or {}
        if skip_baseline or not llm_cfg.get("api_key"):
            rec["baseline"]["skipped"] = True
        rec["baseline"]["score"] = score_verdict(q["expected"], rec["baseline"].get("verdict"))
        records.append(rec)
        if on_record:
            try:
                on_record(rec, records)
            except Exception:
                pass
    return records


def _parse_args(argv: list[str] | None = None):
    p = argparse.ArgumentParser(description="领域探索评测：系统 vs 裸 LLM 基线")
    p.add_argument("--questions", type=Path, default=None, help="题目 JSON（默认内置题集）")
    p.add_argument("--only", default="", help="只跑指定题号（逗号分隔，如 Q1,Q10）")
    p.add_argument("--out-dir", type=Path, default=Path("data/eval"), help="结果输出目录")
    p.add_argument("--skip-system", action="store_true")
    p.add_argument("--skip-baseline", action="store_true")
    p.add_argument("--list", action="store_true", help="列出题集后退出")
    p.add_argument("--thinking-effort", default="", choices=["", "off", "low", "medium", "high"],
                   help="覆盖全局 LLM 思考强度（两臂同时生效），用于模型能力降级的消融实验")
    return p.parse_args(argv)


async def main_async(argv: list[str] | None = None) -> int:
    from backend.services.ai_config import get_analysis_config

    args = _parse_args(argv)
    if args.thinking_effort:
        # 两臂与系统内部所有阶段都经 ai_config.get_llm_config 取配置（get_analysis_config 亦然），
        # 在此统一覆盖思考强度；显式传 thinking_effort 的调用点保持各自设定
        from backend.services import ai_config as _ai_config

        _orig_llm_config = _ai_config.get_llm_config

        def _llm_config_override() -> dict:
            return {**_orig_llm_config(), "thinking_effort": args.thinking_effort}

        _ai_config.get_llm_config = _llm_config_override
    questions = load_questions(args.questions)
    if args.list:
        for q in questions:
            print(f"{q['id']:<4} [{q.get('category', ''):<14}] 预期={q['expected']:<16} {q['question']}")
        return 0
    if args.only:
        keep = {x.strip() for x in args.only.split(",") if x.strip()}
        selected = [q for q in questions if q["id"] in keep]
        if not selected:
            print(f"--only 未匹配任何题号（可用：{','.join(q['id'] for q in questions)}）")
            return 2
        questions = selected

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_json = args.out_dir / f"explore_eval_{stamp}.json"
    out_md = args.out_dir / f"explore_eval_{stamp}.md"

    def _flush(all_records: list[dict]) -> None:
        out_json.write_text(
            json.dumps({"records": all_records}, ensure_ascii=False, indent=1), encoding="utf-8")

    def _on_record(rec: dict, all_records: list[dict]) -> None:
        _flush(all_records)
        print(f"    → 系统={((rec.get('system') or {}).get('verdict'))} "
              f"基线={((rec.get('baseline') or {}).get('verdict'))}")

    print(f"评测 {len(questions)} 题 → {out_json}")
    t0 = time.monotonic()
    records = await evaluate(
        questions, skip_system=args.skip_system, skip_baseline=args.skip_baseline,
        on_record=_on_record,
    )
    llm_cfg = get_analysis_config()
    report = build_report(
        {
            "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "questions_version": (args.questions or _BUNDLED_QUESTIONS).name,
            "model": llm_cfg.get("model", ""),
            "thinking_effort": llm_cfg.get("thinking_effort", ""),
            "duration_s": round(time.monotonic() - t0, 1),
        },
        records,
    )
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    out_md.write_text(render_markdown(report), encoding="utf-8")
    print(f"完成（{report['meta']['duration_s']}s）：{out_md}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
