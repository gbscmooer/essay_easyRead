"""ScholarQA-CS（ScholarQABench 计算机子集，OpenScholar / AI2）：开放问答的专家细则评测。

每题跑一次完整八阶段流程（临时库，联网检索），取「直接回答」（answer_writer）的
正文 + 参考文献摘录作为提交答案，按官方 rubric_corpusqa_generic 打分：

  length 0.05（≤300 词满分、600 词归零，参考文献计入）、expertise 0.05、
  citations 0.2（陈述带引用比例）、excerpts 0.1（引用附摘录比例）、
  专家细则 0.6（每条 criterion 0~10 分 + 专家证据片段覆盖率）。

评分提示词与官方脚本 scripts/scholarqacs_rubric.py 逐字一致；评委模型换成 glm-5.3
（官方为 gpt-4-turbo / gpt-4o），因此分数只与本系统自身（及同评委的基线）比较，
不与论文公布的成绩直接比较。

用法：
  python -m backend.eval.scholarqa_cs run --n 10 --seed 0 [--baseline]
  python -m backend.eval.scholarqa_cs judge data/eval/scholarqa_cs_<stamp>.json   # 只重新打分
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import random
import time
from datetime import datetime
from pathlib import Path

from backend.eval import ledger
from backend.services.llm import llm_stage

ROOT =Path(__file__).resolve().parents[2]
DATA = ROOT / "data/eval/public/ScholarQABench/data/scholarqa_cs/test_configs_snippets.json"
OUT_DIR = ROOT / "data/eval"
JUDGE_MODEL = "glm-5.3"

_PROPERTY_SYSTEM = (
    "You will be given a question someone asked (in <question></question> tags) and the corresponding "
    "response (in <response></response> tags) given to them by an assistant.  You will then be given a "
    "specific criterion of the response to evaluate (in <criterion></criterion> tags).\n"
    "Return a score on a scale of 0 to 10 indicating how appropriate the response is based on the given "
    "criterion.  Judge only the specified aspect(s), not any other qualities of the answer.  Output JSON in "
    "the format: {{\"score\": x}}.")
_EVIDENCE_SYSTEM = (
    "You are given the response given by a scientific assistant to a user query enclosed in "
    "<response></response> tags.\n"
    "              In addition you are also given a list of snippets numbered from 1 to {n} enclosed in "
    "<snippets></snippets> tags, which should be present in the true answer. \n"
    "              Your job is to count how many snippets out of the given list are relevant to the provided "
    "response. \n"
    "              A snippet is relevant if the information provided by it is partly or completely present in "
    "the response. Count every snippet only once. \n"
    "              Output JSON with the count as a number in the format: {{\"score\": x}}.")
_CLAIMS_USER = (
    "Here is a response to a question that includes several claims and citations:\nResponse: {response}\n\n"
    "Split the response into individual claims, citations, and excerpts from the citations, in JSON format: "
    '{{"claims": [{{"claim_text": "...", "citations": [{{"citation_text": "...", "excerpts": ["...", ...]}}, '
    "...]}}, ...]}}\n\nIf a claim is missing citations or a citation is not accompanied by excerpts, some "
    "lists may be empty in your output.")
_EXPERTISE = ("The level of expertise required to understand the answer should be roughly aligned with the "
              "estimated expertise of a typical person who would ask the question.")
_STATIC = {"length", "expertise", "citations", "excerpts"}


def load_cases(path: Path = DATA) -> list[dict]:
    """按问题去重：同一问题的多份标注（不同专家）各自打分后取平均。"""
    by_q: dict[str, dict] = {}
    for row in json.loads(path.read_text(encoding="utf-8")):
        q = row["initial_prompt"]
        by_q.setdefault(q, {"question": q, "configs": []})["configs"].append(
            {"case_id": row["case_id"], **row["metric_config"]["config"]})
    return list(by_q.values())


def pick_cases(cases: list[dict], n: int, seed: int, *, exclude_seed: int | None = None,
               exclude_n: int = 20) -> list[dict]:
    """按种子抽题；exclude_seed 给出时先去掉“该种子抽 exclude_n 题”的那批（扩充新题，不与已评测的题重叠）。"""
    if exclude_seed is not None:
        used = {c["question"] for c in random.Random(exclude_seed).sample(cases, min(exclude_n, len(cases)))}
        cases = [c for c in cases if c["question"] not in used]
    return random.Random(seed).sample(cases, min(n, len(cases)))


def length_score(response: str, low: int = 300, high: int = 600) -> float:
    words = len(response.split())
    return 1 - (max(min(high, words), low) - low) / (high - low)


def score_weights(config: dict) -> dict[str, float]:
    weights = {"length": config.get("length_weight", 0.05), "expertise": config.get("expertise_weight", 0.05),
               "citations": config.get("citations_weight", 0.2), "excerpts": config.get("excerpts_weight", 0.1)}
    for prop in config.get("other_properties") or []:
        if prop.get("criterion"):
            weights[prop["name"]] = prop["weight"] / (2.0 if prop.get("evidence") else 1.0)
        if prop.get("evidence"):
            weights[f"{prop['name']}_evidence"] = prop["weight"] / (2.0 if prop.get("criterion") else 1.0)
    return weights


def combine(config: dict, components: dict[str, float]) -> dict:
    weights = score_weights(config)
    score = sum(weights[k] * components.get(k, 0.0) for k in weights)
    ann = sum(weights[k] * components.get(k, 0.0) for k in weights if k not in _STATIC) / 0.6
    return {"score": round(score, 4), "ann_score": round(ann, 4), **components}


class RubricJudge:
    """claims_json：拆分陈述/引用/摘录的调用输出很长（官方不设上限），单独给大的输出预算。"""

    def __init__(self, llm_json, claims_json=None):
        self.llm_json = llm_json
        self.claims_json = claims_json or llm_json

    async def _score(self, system: str, user: str) -> float | None:
        # 接口错误（限流、额度）向上抛出，由调用方记为该题出错；只有输出无法解析时
        # 按官方脚本记 0 分——否则评委故障会被静默算成系统低分
        try:
            with llm_stage("judge_rubric"):
                obj = await self.llm_json(system, user)
        except ValueError:   # 输出不是合法 JSON（JSONDecodeError）
            return None
        try:
            return float(obj["score"])
        except (KeyError, TypeError, ValueError):
            return None

    async def prop(self, question: str, response: str, criterion: str) -> float:
        s = await self._score(_PROPERTY_SYSTEM, f"<question>{question}</question>\n<response>{response}"
                              f"</response>\n<criterion>{criterion}</criterion>")
        return max(0.0, min(1.0, (s or 0.0) / 10.0))

    async def evidence(self, response: str, snippets: list[str]) -> float:
        listing = "\n".join(f"{i + 1}. {x}" for i, x in enumerate(snippets))
        s = await self._score(_EVIDENCE_SYSTEM.format(n=len(snippets)),
                              f"<response>{response}</response>\n<snippets>{listing}</snippets>")
        return min((s or 0.0) / len(snippets), 1.0)

    async def citations_excerpts(self, response: str) -> dict[str, float]:
        try:
            with llm_stage("judge_claims"):   # 拆分可用另一模型（--claims-model），用量分开记
                obj = await self.claims_json("You are a helpful assistant.", _CLAIMS_USER.format(response=response))
            claims = obj["claims"]
            cited = sum(1 for c in claims if c.get("citations")) / max(len(claims), 1)
            cites = list(itertools.chain.from_iterable(c.get("citations") or [] for c in claims))
            excerpts = sum(1 for c in cites if c.get("excerpts")) / max(len(cites), 1)
            return {"citations": cited, "excerpts": excerpts}
        except (KeyError, TypeError, AttributeError, ValueError):
            return {"citations": 0.0, "excerpts": 0.0}

    async def _evidence_all(self, response: str, lists: list[list[str]]) -> list[float]:
        # 说明里带条目数 n，同一 n 的调用才共用“说明+答案”前缀：每组先发一条写入缓存，其余再并发
        found: list[float] = [0.0] * len(lists)
        groups: dict[int, list[int]] = {}
        for i, snippets in enumerate(lists):
            groups.setdefault(len(snippets), []).append(i)

        async def run(idx: list[int]) -> None:
            found[idx[0]] = await self.evidence(response, lists[idx[0]])
            for i, v in zip(idx[1:], await asyncio.gather(*(self.evidence(response, lists[i]) for i in idx[1:]))):
                found[i] = v
        await asyncio.gather(*(run(idx) for idx in groups.values()))
        return found

    async def score(self, config: dict, response: str, cites: dict | None = None) -> dict:
        # 打分逻辑与逐条调用一致；调用顺序照顾模型服务的前缀缓存：各条细则共用“说明+问题+答案”前缀
        # （答案连参考文献约 4k token，每题十来次），先单独发一条写入缓存，其余再并发
        # （10-27：评委占全部用量约九成，齐发时 glm-5.3 输入只有约 13% 命中缓存）
        # cites：已有的陈述/引用/摘录拆分（拆分与细则无关，同一答案的几套细则共用一次）
        question = config["question"]
        props = config.get("other_properties") or []
        with_criterion = [p for p in props if p.get("criterion")]
        cites_task = asyncio.ensure_future(self.citations_excerpts(response) if cites is None else _done(cites))
        try:
            expertise = await self.prop(question, response, _EXPERTISE)
            crit = await asyncio.gather(*(self.prop(question, response, p["criterion"]) for p in with_criterion))
            cites = await cites_task
        finally:
            cites_task.cancel()   # 已完成时无作用；出错时不留悬空调用
        comp = {"length": length_score(response, config.get("low_length", 300), config.get("high_length", 600)),
                "expertise": expertise, **cites}
        comp.update({p["name"]: v for p, v in zip(with_criterion, crit)})
        # 官方规则：criterion 得 0 分时证据项也记 0
        need_evidence = [p for p in props if p.get("evidence") and (not p.get("criterion") or comp.get(p["name"]))]
        found = await self._evidence_all(response, [p["evidence"] for p in need_evidence])
        for p in props:
            if p.get("evidence"):
                comp[f"{p['name']}_evidence"] = 0.0
        comp.update({f"{p['name']}_evidence": v for p, v in zip(need_evidence, found)})
        return combine(config, comp)

    async def score_all(self, configs: list[dict], response: str) -> list[dict]:
        """同一答案的多套细则：第一套打完（缓存已写入）再并发打其余几套，陈述/引用/摘录拆分共用第一套的。"""
        if not configs:
            return []
        first = await self.score(configs[0], response)
        cites = {k: first[k] for k in ("citations", "excerpts")}
        return [first, *await asyncio.gather(*(self.score(c, response, cites) for c in configs[1:]))]


async def _done(value):
    return value


def summarize(records: list[dict], arm: str) -> dict | None:
    rows = [r[arm] for r in records if (r.get(arm) or {}).get("scores")]
    if not rows:
        return None

    def mean(key: str) -> float:
        vals = [s[key] for r in rows for s in r["scores"]]
        return round(sum(vals) / len(vals), 4)

    return {"answered": len(rows), "of": len(records), "score": mean("score"), "ann_score": mean("ann_score"),
            **{k: mean(k) for k in ("length", "expertise", "citations", "excerpts")},
            "words_mean": round(sum(r.get("words", 0) for r in rows) / len(rows), 1)}


# ── 运行 ────────────────────────────────────────────────────────────────────

def disable_openalex() -> None:
    """不用 OpenAlex（用户 10-24 决定）：所有请求立即失败，预检判死，召回只走 Scopus + arXiv。"""
    from backend.services.deep_research.providers import openalex

    async def _disabled(self, *args, **kwargs):
        raise RuntimeError("OpenAlex disabled for this evaluation")

    openalex.OpenAlexProvider._get = _disabled


def judge_cfg(model: str) -> dict:
    from backend.services import ai_config

    cfg = {**ai_config.get_analysis_config(), "model": model}
    if not cfg.get("api_key"):
        raise SystemExit("未配置模型 API Key")
    return cfg


def make_judge(args) -> "RubricJudge":
    cfg = judge_cfg(args.judge_model)
    # 陈述/引用/摘录拆分是机械切分、输出整篇答案那么长（评委输出几乎全在这一步），可单独指定模型
    claims_cfg = judge_cfg(getattr(args, "claims_model", "") or args.judge_model)
    return RubricJudge(make_llm_json(cfg, max_tokens=args.judge_tokens, thinking_effort=args.judge_thinking),
                       make_llm_json(claims_cfg, max_tokens=16000, thinking_effort=args.judge_thinking))


def make_llm_json(cfg: dict, *, max_tokens: int = 1500, thinking_effort: str | None = None):
    from backend.services.deep_research.domain_synthesizer import _llm_json

    async def llm_json(system: str, user: str) -> dict:
        return await _llm_json(cfg, system, user, max_tokens=max_tokens, temperature=0.0,
                               thinking_effort=thinking_effort)
    return llm_json


_BASELINE_SYSTEM = ("You are a scientific assistant. Answer from your own knowledge (you cannot search). "
                    "Cite papers you are confident exist as [n] and list them under References. "
                    "Output JSON: {\"answer\": \"markdown answer with references\"}.")


async def run(args) -> Path:
    from backend.eval import explore_eval
    from backend.eval.public_benchmarks import _llm_cfg
    from backend.services.deep_research.answer_writer import render_answer
    from backend.services.llm import model_limiter_stats

    if not args.use_openalex:
        disable_openalex()
    system_cfg = _llm_cfg(args.thinking_effort, args.model)
    cases = load_cases()
    picked = pick_cases(cases, args.n, args.seed, exclude_seed=args.exclude_seed, exclude_n=args.exclude_n)
    if args.first:
        picked = picked[:args.first]   # 同一抽样的前 k 题（冒烟验证用，题目与全量运行一致）
    judge = make_judge(args)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / f"scholarqa_cs_{stamp}.json"
    runs_dir = OUT_DIR / f"scholarqa_cs_{stamp}_runs"
    meta = {"depth": args.depth, "seed": args.seed, "n": len(picked), "exclude_seed": args.exclude_seed, "system_model": system_cfg.get("model"),
            "judge_model": args.judge_model, "claims_model": args.claims_model or args.judge_model, "openalex": args.use_openalex, "started": stamp,
            "arxiv_mirror": os.environ.get("ARXIV_MIRROR", "auto"), "label": args.label,
            "concurrency": args.concurrency, "answer_only": args.answer_only, "answer_writer": args.answer_writer,
            "provenance": ledger.provenance(out)}
    log = ledger.open_log(out)
    from backend.eval.public_benchmarks import QuotaWatch

    watch = QuotaWatch.install()
    done: dict[int, dict] = {}
    gate = asyncio.Semaphore(max(1, args.concurrency))
    options = {"answer_only": True} if args.answer_only else {}
    options["answer_writer"] = args.answer_writer   # 显式写入：流程默认值变化不影响评测口径

    async def one(i: int, case: dict, engine) -> dict:
        """单题：系统作答 + 评委打分。返回 (记录, 本题限流计数)。"""
        scope = QuotaWatch.scope()   # 本任务内的限流单独计数（并发时不串题）
        rec = {"question": case["question"], "case_ids": [c["case_id"] for c in case["configs"]]}
        t0 = time.monotonic()
        try:
            save = runs_dir / f"q{i:03d}.json"
            sysrec = await explore_eval.run_system_arm(case["question"], args.depth, save_as=save,
                                                       options=options, engine=engine)
            if sysrec.get("error"):
                # 流程失败（如检索规划阶段模型调用失败）：不是“答案得 0 分”，记为出错
                raise RuntimeError(sysrec["error"])
            result = json.loads(save.read_text(encoding="utf-8"))["result"]
            answer = result.get("answer") or {}
            response = render_answer(answer)
            rec["system"] = {"status": answer.get("status"), "response": response, "words": len(response.split()),
                             "check": answer.get("check"), "materials": answer.get("materials"),
                             "corpus": sysrec.get("total"), "run_error": sysrec.get("error"),
                             "wall_s": round(time.monotonic() - t0, 1), "saved": str(save)}
            if response.strip():
                try:
                    rec["system"]["scores"] = await judge.score_all(case["configs"], response)
                except Exception as exc:  # noqa: BLE001 —— 答案已保存，可用 judge 子命令补打分
                    rec["system"]["judge_error"] = f"{type(exc).__name__}: {exc}"[:300]
        except Exception as exc:  # noqa: BLE001 —— 单题失败记录在案
            rec["system"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        if args.baseline:
            try:
                base = await make_llm_json(system_cfg, max_tokens=4000)(_BASELINE_SYSTEM, case["question"])
                response = str(base.get("answer") or "")
                rec["baseline"] = {"response": response, "words": len(response.split()),
                                   "scores": await judge.score_all(case["configs"], response)}
            except Exception as exc:  # noqa: BLE001
                rec["baseline"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        return rec, scope

    async def worker(i: int, case: dict, engine) -> None:
        for attempt in range(QuotaWatch.MAX_RETRIES + 1):
            async with gate:
                rec, scope = await one(i, case, engine)
            judge_limited = any("RATE_LIMITED" in str((rec.get(arm) or {}).get(key) or "")
                                for arm in ("system", "baseline") for key in ("judge_error", "error"))
            if scope["hits"] or judge_limited:
                watch.note(" ".join(str((rec.get(arm) or {}).get(key) or "")
                                    for arm in ("system", "baseline") for key in ("judge_error", "error")))
            if (scope["hits"] or judge_limited) and attempt < QuotaWatch.MAX_RETRIES:
                # 限流期间流程按失败降级（筛选 fail-open、规划停止），本题结果不可信：丢弃、等重置后重跑
                print(f"[{i}] ⚠ 模型限流，丢弃本题结果（{(scope['sample'] or 'judge')[:120]}）", flush=True)
                await watch.wait_for_reset()
                continue
            break
        rec["system"]["attempts"] = attempt + 1   # >1 表示限流后丢弃重跑过
        if scope["hits"]:
            rec["system"]["rate_limited"] = scope["sample"][:300]
        done[i] = rec
        sys_scores = (rec["system"].get("scores") or [{}])
        print(f"[{i}/{len(picked)}] {case['question'][:80]}\n    → 系统 {sys_scores[0].get('score')}"
              f"（细则 {sys_scores[0].get('ann_score')}，{rec['system'].get('words')} 词，"
              f"{rec['system'].get('status') or rec['system'].get('error')}，"
              f"{rec['system'].get('wall_s')} 秒）"
              + (f"；基线 {((rec.get('baseline') or {}).get('scores') or [{}])[0].get('score')}" if args.baseline else "")
              + f"  [已完成 {len(done)}/{len(picked)}，用时 {round((time.monotonic() - started) / 60, 1)} 分，"
              f"模型并发占用 {(model_limiter_stats() or {}).get('utilization')}]",
              flush=True)
        out.write_text(json.dumps({"meta": meta, "partial": True, "records": [done[k] for k in sorted(done)]},
                                  ensure_ascii=False, indent=1), encoding="utf-8")

    started = time.monotonic()
    with explore_eval.shared_engine() as engine:
        await asyncio.gather(*(worker(i, case, engine) for i, case in enumerate(picked, 1)))
    records = [done[k] for k in sorted(done)]
    meta["wall_min"] = round((time.monotonic() - started) / 60, 1)
    meta["model_limiter"] = model_limiter_stats()  # 含评委调用：同一账号共享并发名额
    report = {"meta": meta, "summary": {"system": summarize(records, "system"),
                                        "baseline": summarize(records, "baseline")}, "records": records}
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    ledger.record(out, report)
    log.close()
    return out


META = DATA.parent / "qa_metadata_all.jsonl"
OPENSCHOLAR = DATA.parent / "src_answers/gpt-4o-ai2_rubric_reranking_pes2o_top_15_reranking_you_feedback_no_post_edit.jsonl"


def _render_nora(sections: list[dict]) -> str:
    parts, refs = [], {}
    for sec in sections:
        parts.append(f"## {sec.get('title') or ''}\n{sec.get('tldr') or ''}\n{sec.get('text') or ''}")
        for c in sec.get("citations") or []:
            refs.setdefault(c.get("id"), " ".join(c.get("snippets") or []))
    return "\n\n".join(parts) + "\n\nReferences:\n" + "\n".join(f"{k}: {v}" for k, v in refs.items())


def reference_answers() -> dict[str, dict[str, str]]:
    """数据集自带的其他系统答案：{question: {system: response}}（OpenScholar 按 case_id 对齐）。"""
    case_q = {c["case_id"]: case["question"] for case in load_cases() for c in case["configs"]}
    out: dict[str, dict[str, str]] = {}
    for line in META.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        answers = out.setdefault(row["question"], {})
        for name, ans in (row.get("src_answers") or {}).items():
            answers[name] = _render_nora(ans) if isinstance(ans, list) else str(ans)
    for line in OPENSCHOLAR.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["case_id"] in case_q:
            out.setdefault(case_q[row["case_id"]], {})["OpenScholar-GPT4o"] = row["answer_text"]
    return out


async def score_references(args) -> Path:
    """同一评委给数据集自带的其他系统答案打分（同题、同评分脚本），作为同评委下的对照。"""
    cases = load_cases()
    picked = pick_cases(cases, args.n, args.seed, exclude_seed=args.exclude_seed, exclude_n=args.exclude_n)
    refs = reference_answers()
    judge = make_judge(args)
    suffix = f"_ex{args.exclude_seed}" if args.exclude_seed is not None else ""
    out = OUT_DIR / f"scholarqa_cs_refs_seed{args.seed}_n{len(picked)}{suffix}.json"
    records = []
    for i, case in enumerate(picked, 1):
        rec = {"question": case["question"]}
        for name, response in sorted((refs.get(case["question"]) or {}).items()):
            if args.only and name not in args.only:
                continue
            rec[name] = {"response": response, "words": len(response.split())}
            try:
                rec[name]["scores"] = await judge.score_all(case["configs"], response)
            except Exception as exc:  # noqa: BLE001 —— 评委接口故障记为出错，不计 0 分
                rec[name]["error"] = f"{type(exc).__name__}: {exc}"[:300]
        records.append(rec)
        print(f"[{i}/{len(picked)}] " + "  ".join(
            f"{k}={v['scores'][0]['score']:.3f}" if v.get("scores") else f"{k}=ERR"
            for k, v in rec.items() if isinstance(v, dict)), flush=True)
        out.write_text(json.dumps({"partial": True, "records": records}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    systems = sorted({k for r in records for k in r if k != "question"})
    summary = {name: summarize(records, name) for name in systems}
    out.write_text(json.dumps({"meta": {"seed": args.seed, "n": len(picked), "judge_model": args.judge_model, "claims_model": args.claims_model or args.judge_model,
                                        "exclude_seed": args.exclude_seed},
                               "summary": summary, "records": records}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return out


async def rejudge(args) -> None:
    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    configs = {c["question"]: c["configs"] for c in load_cases()}
    judge = make_judge(args)
    for rec in data["records"]:
        for arm in ("system", "baseline"):
            row = rec.get(arm) or {}
            if row.get("response", "").strip() and (args.all or not row.get("scores")):
                try:
                    row["scores"] = await judge.score_all(configs[rec["question"]], row["response"])
                    row.pop("judge_error", None)
                except Exception as exc:  # noqa: BLE001
                    row["judge_error"] = f"{type(exc).__name__}: {exc}"[:300]
    data["summary"] = {"system": summarize(data["records"], "system"),
                       "baseline": summarize(data["records"], "baseline")}
    data["meta"]["judge_model"] = args.judge_model
    data["meta"]["claims_model"] = args.claims_model or args.judge_model
    Path(args.file).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(data["summary"], ensure_ascii=False))


async def resplit(args) -> Path:
    """只重做陈述/引用/摘录拆分（细则分不变），按新拆分重算综合分，写新文件（原文件不动）。

    换拆分模型（--claims-model）后与旧结果对齐用：对照组也用同一模型重拆，再做配对比较。
    运行结果（system/baseline）与参考系统文件（refs，按系统名）都适用；--arms 只重拆其中几个系统。"""
    src = Path(args.file)
    data = json.loads(src.read_text(encoding="utf-8"))
    configs = {c["question"]: c["configs"] for c in load_cases()}
    judge = make_judge(args)
    model = args.claims_model or args.judge_model
    gate = asyncio.Semaphore(max(1, args.concurrency))
    jobs = [(rec, row) for rec in data["records"] for arm, row in rec.items()
            if isinstance(row, dict) and row.get("scores") and str(row.get("response") or "").strip()
            and (not args.arms or arm in args.arms)]
    failed = 0

    async def one(rec: dict, row: dict) -> None:
        nonlocal failed
        async with gate:
            try:
                cites = await judge.citations_excerpts(row["response"])
            except Exception as exc:  # noqa: BLE001 —— 接口故障：保留原拆分并记录，不混进新模型的分数
                row["claims_error"] = f"{type(exc).__name__}: {exc}"[:300]
                failed += 1
                return
        row["scores"] = [combine(c, {**{k: v for k, v in s.items() if k not in ("score", "ann_score")}, **cites})
                         for c, s in zip(configs[rec["question"]], row["scores"])]
        row["claims_model"] = model

    await asyncio.gather(*(one(rec, row) for rec, row in jobs))
    if isinstance(data.get("summary"), dict):
        data["summary"] = {"system": summarize(data["records"], "system"),
                           "baseline": summarize(data["records"], "baseline")}
    data.setdefault("meta", {}).update(claims_model=model, resplit_of=src.name)
    out = src.with_name(f"{src.stem}_claims-{model}.json")
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"重拆 {len(jobs) - failed}/{len(jobs)} 份答案（{model}）→ {out}", flush=True)
    return out


async def rerender(args) -> None:
    """用保存的运行记录按当前 answer_writer 重建参考文献（正文不变、不重跑系统），写新文件待重新打分。"""
    from backend.services.deep_research import answer_writer as aw

    src = Path(args.file)
    data = json.loads(src.read_text(encoding="utf-8"))
    changed = 0
    for rec in data["records"]:
        row = rec.get("system") or {}
        saved = row.get("saved")
        if not saved or not Path(saved).exists():
            continue
        result = json.loads(Path(saved).read_text(encoding="utf-8"))["result"]
        answer = result.get("answer") or {}
        if not answer.get("text") or not answer.get("references"):
            continue
        records = ((result.get("report_extract") or {}).get("evidence_store") or {}).get("records") or []
        materials = aw.assemble_materials(result.get("papers") or [], records, answer.get("gap_papers"),
                                          limit=answer.get("material_limit") or aw.MAX_MATERIALS)
        refs = aw._references(answer["text"], materials)
        old = {r["n"]: r["key"] for r in answer["references"]}
        if {r["n"]: r["key"] for r in refs} != old:
            print(f"跳过（编号对不上）：{rec['question'][:60]}", flush=True)
            continue
        response = aw.render_answer({**answer, "references": refs})
        if response != row.get("response"):
            row.update(response=response, words=len(response.split()), scores=[], rerendered=True)
            changed += 1
    out = src.with_name(src.stem + "_rerender.json")
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"重建 {changed} 题 → {out}（再用 judge 子命令打分）", flush=True)


async def rewrite(args) -> Path:
    """复用保存的召回与阅读结果，按当前 answer_writer 重新规划要点、补检索、写答案并打分。

    只隔离答案环节的改动（召回不重跑，单题约 2~3 分钟）；补检索走 arXiv 源（ARXIV_MIRROR 控制是否用本地镜像）。
    """
    from backend.eval.public_benchmarks import _llm_cfg
    from backend.services.deep_research import answer_writer as aw
    from backend.services.deep_research.answer_agent import write_with_pointers
    from backend.services.deep_research.answer_writer import render_answer, write_answer
    from backend.services.deep_research.arxiv_mirror import default_mirror
    from backend.services.deep_research.explorer import gap_search

    src = Path(args.file)
    data = json.loads(src.read_text(encoding="utf-8"))
    configs = {c["question"]: c["configs"] for c in load_cases()}
    from backend.services.deep_research.domain_synthesizer import _llm_json

    system_cfg = _llm_cfg(args.thinking_effort, args.model)

    async def answer_json(system: str, user: str) -> dict:   # 与 explorer 中的调用方式一致
        return await _llm_json(system_cfg, system, user, max_tokens=6000)

    async def plan_json(system: str, user: str) -> dict:
        return await _llm_json(system_cfg, system, user, max_tokens=2000)

    async def draft_json(system: str, user: str) -> dict:   # 起草可单独开思考（思考 token 计入输出，预算放大）
        return await _llm_json(system_cfg, system, user, max_tokens=16000, thinking_effort=args.draft_thinking)

    judge = make_judge(args)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = src.with_name(f"{src.stem}_rewrite_{stamp}.json")
    runs_dir = src.with_name(f"{src.stem}_rewrite_{stamp}_runs")
    runs_dir.mkdir(parents=True, exist_ok=True)
    meta = {**data.get("meta", {}), "rewrite_of": str(src), "rewritten": stamp, "label": args.label,
            "plan": not args.no_plan, "gap": not (args.no_gap or args.no_plan), "arxiv_mirror": os.environ.get("ARXIV_MIRROR", "auto"),
            "materials": args.materials, "quotes": args.quotes, "writer": args.writer, "agent_fetch": args.agent_fetch, "agent_pool": args.agent_pool,
            "agent_draft_titles": args.agent_draft_titles, "agent_claim_search": args.agent_claim_search,
            "agent_final": args.agent_final, "agent_followup": args.agent_followup,
            "draft_thinking": args.draft_thinking,
            "provenance": ledger.provenance(out)}
    log = ledger.open_log(out)
    from backend.eval.public_benchmarks import QuotaWatch

    watch = QuotaWatch.install()
    mirror = default_mirror() if args.writer == "agent" else None
    total = len(data["records"])
    done: list[dict | None] = [None] * total
    sem = asyncio.Semaphore(max(1, args.concurrency))

    async def one(i: int, rec: dict) -> None:
        row = rec.get("system") or {}
        saved = row.get("saved")
        if not saved or not Path(saved).exists():
            done[i] = rec
            return
        async def attempt() -> tuple[dict, dict]:
            scope = QuotaWatch.scope()   # 本任务内的限流单独计数（并发时不串题）
            blob = json.loads(Path(saved).read_text(encoding="utf-8"))
            result = blob["result"]
            evidence = ((result.get("report_extract") or {}).get("evidence_store") or {}).get("records") or []
            t0 = time.monotonic()
            try:
                if args.writer == "agent":    # 指针式作答：起草 → 工具按论断检索原文句 → 引用指针定稿
                    pool = (result.get("papers") or []) if args.agent_pool == "recall" else []
                    answer = await write_with_pointers(rec["question"], pool, llm_json=answer_json, mirror=mirror,
                                                       fetch_full_text=args.agent_fetch,
                                                       draft_titles=args.agent_draft_titles,
                                                       claim_search=args.agent_claim_search,
                                                       final_mode=args.agent_final,
                                                       followup=args.agent_followup,
                                                       draft_json=draft_json if args.draft_thinking != "off" else None)
                elif args.writer == "memory":   # 对照：同一写作要求，不给材料，只凭模型知识
                    lang = aw.question_language(rec["question"])
                    data_ = await (draft_json if args.draft_thinking != "off" else answer_json)(
                        aw._KNOWLEDGE_SYSTEM, aw._user_prompt(rec["question"], [], lang, knowledge=True))
                    answer = {"status": "ok", "language": lang, "references": [], "materials": 0,
                              "text": str((data_ or {}).get("answer") or "").strip(), "writer": "memory"}
                else:
                    answer = await write_answer(rec["question"], result.get("papers") or [], evidence,
                                                llm_json=answer_json, plan_json=None if args.no_plan else plan_json,
                                                search_more=None if args.no_gap or args.no_plan else gap_search,
                                                aspect_quotes=args.quotes in ("pre", "both"),
                                                verify_json=plan_json if args.quotes in ("verify", "both") else None,
                                                knowledge=args.writer == "knowledge",
                                                **({"material_limit": args.materials} if args.materials else {}))
                response = render_answer(answer)
                result["answer"] = answer
                save = runs_dir / Path(saved).name
                save.write_text(json.dumps(blob, ensure_ascii=False), encoding="utf-8")
                new = {"status": answer.get("status"), "response": response, "words": len(response.split()),
                       "check": answer.get("check"), "materials": answer.get("materials"),
                       "gap_retrieval": answer.get("gap_retrieval"), "corpus": row.get("corpus"),
                       "aspect_quotes": answer.get("aspect_quotes"), "agent": answer.get("agent"),
                       "verification": {k: v for k, v in (answer.get("verification") or {}).items()
                                        if k != "unsupported"} or None,
                       "wall_s": round(time.monotonic() - t0, 1), "saved": str(save)}
                if response.strip():
                    new["scores"] = await judge.score_all(configs[rec["question"]], response)
            except Exception as exc:  # noqa: BLE001 —— 单题失败记录在案
                new = {"error": f"{type(exc).__name__}: {exc}"[:300]}
            return new, scope

        for n_try in range(QuotaWatch.MAX_RETRIES + 1):
            async with sem:
                new, scope = await attempt()
            limited = scope["hits"] or "RATE_LIMITED" in str(new.get("error") or "")
            if limited:
                watch.note(str(new.get("error") or "") + " " + str(scope.get("sample") or ""))
            if limited and n_try < QuotaWatch.MAX_RETRIES:
                # 限流期间写作与评分会降级，本题结果不可信：丢弃、等额度重置后重跑
                print(f"[{i + 1}] ⚠ 模型限流，丢弃本题结果（{(scope['sample'] or new.get('error') or '')[:120]}）",
                      flush=True)
                await watch.wait_for_reset()
                continue
            break
        new["attempts"] = n_try + 1
        if scope["hits"]:
            new["rate_limited"] = str(scope["sample"])[:300]
        done[i] = {**rec, "system": new}
        s = (new.get("scores") or [{}])[0]
        print(f"[{i + 1}/{total}] {rec['question'][:80]}\n    → 系统 {s.get('score')}（细则 {s.get('ann_score')}，"
              f"{new.get('words')} 词，{new.get('status') or new.get('error')}，"
              f"补检索 {(new.get('gap_retrieval') or {}).get('added')}）", flush=True)
        out.write_text(json.dumps({"meta": meta, "partial": True, "records": [r for r in done if r]},
                                  ensure_ascii=False, indent=1), encoding="utf-8")

    await asyncio.gather(*(one(i, rec) for i, rec in enumerate(data["records"])))
    records = [r for r in done if r]
    report = {"meta": meta, "summary": {"system": summarize(records, "system"), "baseline": None},
              "records": records}
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    ledger.record(out, report)
    log.close()
    return out


async def run_agent(args) -> Path:
    """指针式作答独立运行：不走召回流程，按题起草 → 本地镜像检索原文句（可补 arXiv 全文）→ 引用指针定稿，再打分。"""
    from backend.eval.public_benchmarks import QuotaWatch, _llm_cfg
    from backend.services.deep_research.answer_agent import RECOMMENDED, write_with_pointers
    from backend.services.deep_research.answer_writer import render_answer
    from backend.services.deep_research.arxiv_mirror import default_mirror
    from backend.services.deep_research.domain_synthesizer import _llm_json
    from backend.services.llm import model_limiter_stats, start_token_ledger, token_ledger_report

    disable_openalex()
    system_cfg = _llm_cfg(args.thinking_effort, args.model)

    async def answer_json(system: str, user: str) -> dict:
        return await _llm_json(system_cfg, system, user, max_tokens=6000)

    picked = pick_cases(load_cases(), args.n, args.seed, exclude_seed=args.exclude_seed, exclude_n=args.exclude_n)
    if args.first:
        picked = picked[:args.first]
    # 编号沿用抽样里的位置（与全量运行日志的 [i/20] 一致）；--pick 只跑其中几题，--repeat 每题重复几次估计波动
    numbered = [(i, c) for i, c in enumerate(picked, 1) if not args.pick or i in set(args.pick)]
    jobs = [(i, rep, c) for i, c in numbered for rep in range(max(1, args.repeat))]
    judge = make_judge(args)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / f"scholarqa_cs_agent_{stamp}.json"
    runs_dir = OUT_DIR / f"scholarqa_cs_agent_{stamp}_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    settings = {**RECOMMENDED, **({"final_mode": args.final} if args.final else {}),
                **({"followup": args.followup} if args.followup is not None else {}),
                **({"reading": args.reading} if args.reading is not None else {}),
                **({"question_search": args.question_search} if args.question_search is not None else {}),
                **({"opening": True} if args.opening else {}),
                **({"paper_reading": args.paper_reading} if args.paper_reading is not None else {}),
                **({"paper_reader": args.paper_reader} if args.paper_reader else {}),
                **({"paper_notes": args.paper_notes == "on"} if args.paper_notes else {}),
                **({"missing_fix": args.missing_fix} if args.missing_fix else {}),
                **({"paper_use": args.paper_use} if args.paper_use else {}),
                **({"claim_map": args.claim_map} if args.claim_map else {}),
                **({"fetch_full_text": args.fetch} if args.fetch is not None else {})}
    # --draft-from：沿用另一次运行保存的草稿（含 works/queries），只重跑检索与定稿——
    # 固定草稿后，两次运行的差别只来自证据环节与定稿，不混入起草的波动
    fixed: dict[str, dict[int, dict]] = {}
    if args.draft_from:
        for r in json.loads(Path(args.draft_from).read_text(encoding="utf-8"))["records"]:
            saved = (r.get("system") or {}).get("saved")
            if not saved or not Path(saved).exists():
                continue
            a = json.loads(Path(saved).read_text(encoding="utf-8"))["result"]["answer"]
            if a.get("draft"):
                fixed.setdefault(r["question"], {})[r.get("rep", 0)] = {
                    "draft": a["draft"], "works": [{"title": w} for w in (a.get("agent") or {}).get("works") or []],
                    "queries": (a.get("agent") or {}).get("queries") or []}
    meta = {"depth": "agent", "seed": args.seed, "n": len(numbered), "exclude_seed": args.exclude_seed,
            "pick": args.pick or None, "repeat": max(1, args.repeat), "draft_from": args.draft_from or None,
            "system_model": system_cfg.get("model"), "judge_model": args.judge_model,
            "claims_model": args.claims_model or args.judge_model, "started": stamp,
            "label": args.label, "concurrency": args.concurrency, "answer_only": True, "answer_writer": "agent",
            "agent_only": True, "agent": settings, "provenance": ledger.provenance(out)}
    log = ledger.open_log(out)
    watch = QuotaWatch.install()
    mirror = default_mirror()
    gate = asyncio.Semaphore(max(1, args.concurrency))
    done: dict[tuple[int, int], dict] = {}
    started = time.monotonic()

    async def attempt(i: int, rep: int, case: dict) -> tuple[dict, dict]:
        scope = QuotaWatch.scope()
        t0 = time.monotonic()
        tokens = start_token_ledger()    # 本题作答的模型用量（按阶段；评委打分不计）
        try:
            draft_json = None
            if args.draft_from:
                drafts = fixed.get(case["question"]) or {}
                stored = drafts.get(rep) or drafts.get(0)
                if stored is None:
                    raise RuntimeError("--draft-from 里没有这道题的草稿")

                async def draft_json(system: str, user: str, stored=stored) -> dict:
                    return stored
            answer = await write_with_pointers(case["question"], [], llm_json=answer_json, mirror=mirror,
                                               draft_json=draft_json, **settings)
            used = token_ledger_report(tokens)
            response = render_answer(answer)
            save = runs_dir / (f"q{i:03d}.json" if args.repeat <= 1 else f"q{i:03d}_r{rep}.json")
            save.write_text(json.dumps({"result": {"answer": answer}}, ensure_ascii=False), encoding="utf-8")
            rec = {"status": answer.get("status"), "response": response, "words": len(response.split()),
                   "check": answer.get("check"), "materials": answer.get("materials"), "agent": answer.get("agent"),
                   "tokens": used, "wall_s": round(time.monotonic() - t0, 1), "saved": str(save)}
            if response.strip():
                judge_tokens = start_token_ledger()   # 评委用量单独记（glm-5.3，不计入作答）
                rec["scores"] = await judge.score_all(case["configs"], response)
                rec["judge_tokens"] = token_ledger_report(judge_tokens)   # judge_rubric 为评委模型，judge_claims 为拆分模型
        except Exception as exc:  # noqa: BLE001 —— 单题失败记录在案
            rec = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        return rec, scope

    async def worker(i: int, rep: int, case: dict) -> None:
        for n_try in range(QuotaWatch.MAX_RETRIES + 1):
            async with gate:
                rec, scope = await attempt(i, rep, case)
            limited = scope["hits"] or "RATE_LIMITED" in str(rec.get("error") or "")
            if limited:
                watch.note(str(rec.get("error") or "") + " " + str(scope.get("sample") or ""))
            if limited and n_try < QuotaWatch.MAX_RETRIES:
                print(f"[{i}] ⚠ 模型限流，丢弃本题结果（{(scope['sample'] or rec.get('error') or '')[:120]}）", flush=True)
                await watch.wait_for_reset()
                continue
            break
        rec["attempts"] = n_try + 1
        done[(i, rep)] = {"question": case["question"], "case_ids": [c["case_id"] for c in case["configs"]],
                          "index": i, "rep": rep, "system": rec}
        s = (rec.get("scores") or [{}])[0]
        print(f"[{i}/{len(picked)}{f' 第{rep + 1}次' if args.repeat > 1 else ''}] {case['question'][:80]}\n"
              f"    → 系统 {s.get('score')}（细则 {s.get('ann_score')}，"
              f"{rec.get('words')} 词，{rec.get('status') or rec.get('error')}，{rec.get('wall_s')} 秒）"
              f"  [已完成 {len(done)}/{len(jobs)}，用时 {round((time.monotonic() - started) / 60, 1)} 分，"
              f"模型并发占用 {(model_limiter_stats() or {}).get('utilization')}]", flush=True)
        out.write_text(json.dumps({"meta": meta, "partial": True, "records": [done[k] for k in sorted(done)]},
                                  ensure_ascii=False, indent=1), encoding="utf-8")

    await asyncio.gather(*(worker(i, rep, c) for i, rep, c in jobs))
    records = [done[k] for k in sorted(done)]
    meta["wall_min"] = round((time.monotonic() - started) / 60, 1)
    report = {"meta": meta, "summary": {"system": summarize(records, "system"), "baseline": None},
              "records": records}
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    ledger.record(out, report)
    log.close()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--n", type=int, default=10)
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--exclude-seed", type=int, default=None, help="排除该种子抽到的那批题（扩充新题时用）")
    r.add_argument("--exclude-n", type=int, default=20)
    r.add_argument("--depth", default="quick")
    r.add_argument("--thinking-effort", default="off")
    r.add_argument("--model", default="", help="覆盖系统模型（默认用设置里的模型）")
    r.add_argument("--baseline", action="store_true", help="同时评测裸模型凭记忆作答")
    r.add_argument("--use-openalex", action="store_true")
    r.add_argument("--first", type=int, default=0, help="只跑抽样中的前 k 题")
    r.add_argument("--label", default="", help="本次运行的改动说明（写入 meta）")
    r.add_argument("--concurrency", type=int, default=4, help="同时在跑的题数（共享全局模型并发上限）")
    r.add_argument("--answer-only", action="store_true", help="开放问答模式：不生成领域综合与综述报告")
    r.add_argument("--answer-writer", choices=("grounded", "agent"), default="agent",
                   help="开放问答写作：grounded 只依据已提取材料；agent 指针式作答（起草→按论断检索原文句→引用指针）")
    rr = sub.add_parser("rerender", help="按当前 answer_writer 重建参考文献（不重跑系统）")
    rr.add_argument("file")
    j = sub.add_parser("judge")
    j.add_argument("file")
    j.add_argument("--all", action="store_true", help="全部重新打分（默认只补缺失的分数）")
    cl = sub.add_parser("claims", help="只重做陈述/引用/摘录拆分（细则分不变），写新文件；配合 --claims-model 对齐拆分模型")
    cl.add_argument("file")
    cl.add_argument("--arms", nargs="*", default=[], help="只重拆这几个系统（refs 文件按系统名；默认全部）")
    cl.add_argument("--concurrency", type=int, default=6)
    f = sub.add_parser("refs", help="同一评委给数据集自带的其他系统答案打分")
    f.add_argument("--n", type=int, default=10)
    f.add_argument("--seed", type=int, default=0)
    f.add_argument("--exclude-seed", type=int, default=None, help="排除该种子抽到的那批题")
    f.add_argument("--exclude-n", type=int, default=20)
    f.add_argument("--only", nargs="*", default=None, help="只给这些系统打分，如 OpenScholar-GPT4o")
    w = sub.add_parser("rewrite", help="复用保存的召回与阅读结果，只重跑答案环节并打分")
    w.add_argument("file")
    w.add_argument("--thinking-effort", default="off")
    w.add_argument("--model", default="")
    w.add_argument("--no-gap", action="store_true", help="只做要点规划，不补检索")
    w.add_argument("--no-plan", action="store_true", help="不做要点规划（对照组）")
    w.add_argument("--materials", type=int, default=None, help="送入写作的论文数上限（缺省沿用 MAX_MATERIALS）")
    w.add_argument("--concurrency", type=int, default=4, help="同时重写的题数（共享全局模型并发上限）")
    w.add_argument("--writer", choices=("grounded", "knowledge", "memory", "agent"), default="grounded",
                   help="grounded 只依据材料；knowledge 知识主导、材料支撑处引用；memory 不给材料（对照）；"
                        "agent 起草后按论断检索原文句、引用指针定稿")
    w.add_argument("--agent-fetch", type=int, default=0, help="agent：为前若干篇只有摘要的 arXiv 论文下载全文")
    w.add_argument("--agent-pool", choices=("recall", "none"), default="recall",
                   help="agent 的初始论文：recall 用本次召回结果；none 不用（只靠点名代表作与镜像检索，衡量召回的贡献）")
    w.add_argument("--agent-draft-titles", type=int, default=30, help="agent 起草时展示的召回题名数（0=纯凭知识起草）")
    w.add_argument("--agent-claim-search", type=int, default=0, help="agent 草稿每句到镜像检索的论文数")
    w.add_argument("--agent-final", choices=("revise", "attach", "enrich"), default="revise",
                   help="agent 定稿方式：revise 在草稿基础上改写；attach 以草稿为准逐句挂证据；"
                        "enrich 在 attach 基础上把证据里草稿没写到的重要发现补成带指针的新句")
    w.add_argument("--draft-thinking", default="off",
                   help="起草（agent 草稿 / memory 答案）的思考强度，off 表示不开")
    w.add_argument("--agent-followup", type=int, default=0,
                   help="agent 第二轮检索：取证据最弱的若干句，由模型写检索式再查")
    w.add_argument("--quotes", choices=("off", "pre", "verify", "both"), default="off",
                   help="原文摘录检索：pre 写前按方面并入原文句，verify 写后逐句核查引用并取原句为摘录")
    w.add_argument("--label", default="")
    ag = sub.add_parser("agent", help="指针式作答独立运行（不走召回流程）并打分")
    ag.add_argument("--n", type=int, default=20)
    ag.add_argument("--seed", type=int, default=0)
    ag.add_argument("--exclude-seed", type=int, default=None)
    ag.add_argument("--exclude-n", type=int, default=20)
    ag.add_argument("--first", type=int, default=0)
    ag.add_argument("--thinking-effort", default="off")
    ag.add_argument("--model", default="")
    ag.add_argument("--concurrency", type=int, default=4)
    ag.add_argument("--final", choices=("revise", "attach", "enrich"), default=None, help="覆盖推荐配置的定稿方式")
    ag.add_argument("--followup", type=int, default=None, help="覆盖推荐配置的第二轮检索句数")
    ag.add_argument("--reading", type=int, default=None, help="原文阅读：按问题从证据库再取几句交给模型补充（0 关）")
    ag.add_argument("--question-search", type=int, default=None, help="按问题到镜像检索论文，每条探查取几篇（0 关）")
    ag.add_argument("--opening", action="store_true", help="草稿第一段先交代背景（概念界定、重要性与难点）")
    ag.add_argument("--paper-reading", type=int, default=None, help="精读：已下载全文的论文每篇最多取几处（0 关）")
    ag.add_argument("--paper-reader", choices=("llm", "rerank"), default="",
                    help="精读方式：llm 由模型从重排前几段与全部表题里挑选（默认），rerank 直接取重排前几段")
    ag.add_argument("--paper-notes", choices=("on", "off"), default="", help="精读挑选时是否每处写一句要点")
    ag.add_argument("--missing-fix", choices=("supplement", "rewrite"), default="",
                    help="定稿漏写精读列出的对象时：只补句插回（supplement）或带反馈整篇重写（rewrite）")
    ag.add_argument("--paper-use", choices=("support", "cover"), default="",
                    help="精读原文怎么用：support 支撑、改准草稿并少量补句（默认）；cover 列出题目所问的全部对象并逐个写到")
    ag.add_argument("--claim-map", choices=("list", "inline"), default="",
                    help="草稿各句的候选证据：list 另列一份逐句清单（默认）；inline 就地标在草稿句末（省 token）")
    ag.add_argument("--draft-from", default="", help="沿用这次评测结果里保存的草稿，只重跑检索与定稿（固定草稿对照）")
    ag.add_argument("--fetch", type=int, default=None, help="覆盖推荐配置的补全文篇数")
    ag.add_argument("--pick", type=int, nargs="*", default=None, help="只跑抽样中的这几题（编号同全量运行日志）")
    ag.add_argument("--repeat", type=int, default=1, help="每题重复几次（估计同配置的波动）")
    ag.add_argument("--label", default="")
    for p in (r, j, cl, f, w, ag):
        p.add_argument("--judge-model", default=JUDGE_MODEL)
        p.add_argument("--judge-tokens", type=int, default=4000)
        # 官方评委是非推理模型（gpt-4-turbo，打分输出上限 100 token）；默认关闭思考
        p.add_argument("--judge-thinking", default="off")
        p.add_argument("--claims-model", default="", help="陈述/引用/摘录拆分用的模型（默认同评委）")
    args = parser.parse_args()
    asyncio.run({"run": run, "judge": rejudge, "refs": score_references, "rerender": rerender,
                 "rewrite": rewrite, "agent": run_agent, "claims": resplit}[args.cmd](args))


if __name__ == "__main__":
    main()
