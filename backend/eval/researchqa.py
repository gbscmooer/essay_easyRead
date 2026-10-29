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
  pipeline_draft  pipeline_all 的草稿本身（同一份草稿不加检索与证据），区分“写得全”与“检索和证据”的作用
  mark_answer / ctx_mark  复用召回，mark 定稿（草稿原文保留，只挂指针、改正矛盾句）；all_answer2 为补跑时的同时段复本
  all_answer / ctx_answer  复用 pipeline_all 同一次召回与选材的论文，只重跑作答（前者同配置复本，后者加起草参考文献）

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
    # 复用召回：在 base 组同一次召回与选材得到的论文上只重跑作答（隔离作答环节的差别，省一次完整流程）
    # 同一份草稿、不加检索与证据：拆开“写得长、写得全”与“检索和证据”各自的作用（草稿已保存，不另生成）
    "pipeline_draft": ("draft_of", {}, {"base": "pipeline_all"}),
    "ft_answer": ("reuse", _FT, {"base": "pipeline_all"}),   # 去掉 Scopus 提名通道
    "all_answer": ("reuse", {**_FT, "web_search": "scopus"}, {"base": "pipeline_all"}),
    "ctx_answer": ("reuse", {**_FT, "web_search": "scopus", "draft_context": 15}, {"base": "pipeline_all"}),
    "all_answer2": ("reuse", {**_FT, "web_search": "scopus"}, {"base": "pipeline_all"}),   # 补跑时的同时段对照
    "all_answer3": ("reuse", {**_FT, "web_search": "scopus"}, {"base": "pipeline_all"}),
    # mark 定稿：草稿原文由程序保留，模型只挑指针、改正与证据矛盾的句子
    "mark_answer": ("reuse", {**_FT, "web_search": "scopus", "final_mode": "mark"}, {"base": "pipeline_all"}),
    "mark_answer2": ("reuse", {**_FT, "web_search": "scopus", "final_mode": "mark"}, {"base": "pipeline_all"}),
    # 起草换 glm-5.3（其余步骤仍用 flash）：模型不熟悉的学科，草稿的知识面可能更关键（E19 在 CS 上无差别）
    "strong_draft": ("reuse", {**_FT, "web_search": "scopus"}, {"base": "pipeline_all", "draft_model": "glm-5.3"}),
    "strong_draft2": ("reuse", {**_FT, "web_search": "scopus"}, {"base": "pipeline_all", "draft_model": "glm-5.3"}),
    # 原文阅读：按问题本身从已收集原文中另取 8 句交给定稿补充（召回对题时补具体细节；CS 上默认关）
    "read_answer": ("reuse", {**_FT, "web_search": "scopus", "reading": 8}, {"base": "pipeline_all"}),
    "read_answer2": ("reuse", {**_FT, "web_search": "scopus", "reading": 8}, {"base": "pipeline_all"}),
    "all_answer4": ("reuse", {**_FT, "web_search": "scopus"}, {"base": "pipeline_all"}),
    "all_answer5": ("reuse", {**_FT, "web_search": "scopus"}, {"base": "pipeline_all"}),
    "strong_read": ("reuse", {**_FT, "web_search": "scopus", "reading": 8}, {"base": "pipeline_all", "draft_model": "glm-5.3"}),
    "strong_read2": ("reuse", {**_FT, "web_search": "scopus", "reading": 8}, {"base": "pipeline_all", "draft_model": "glm-5.3"}),
    "strong_read3": ("reuse", {**_FT, "web_search": "scopus", "reading": 8}, {"base": "pipeline_all", "draft_model": "glm-5.3"}),
    "strong_read4": ("reuse", {**_FT, "web_search": "scopus", "reading": 8}, {"base": "pipeline_all", "draft_model": "glm-5.3"}),
    "strong_read16": ("reuse", {**_FT, "web_search": "scopus", "reading": 16}, {"base": "pipeline_all", "draft_model": "glm-5.3"}),
    "strong_read16b": ("reuse", {**_FT, "web_search": "scopus", "reading": 16}, {"base": "pipeline_all", "draft_model": "glm-5.3"}),
    "all_answer6": ("reuse", {**_FT, "web_search": "scopus"}, {"base": "pipeline_all"}),
    "all_answer7": ("reuse", {**_FT, "web_search": "scopus"}, {"base": "pipeline_all"}),
    # 不开 Scopus 通道（不耗 Scopus 配额）：默认作答选项 vs 强模型起草 + 原文阅读 8/16 句，均在 pipeline_all 的论文上
    "nw_def": ("reuse", {}, {"base": "pipeline_all"}),
    "nw_def2": ("reuse", {}, {"base": "pipeline_all"}),
    "nw_sr8": ("reuse", {"reading": 8}, {"base": "pipeline_all", "draft_model": "glm-5.3"}),
    "nw_sr8b": ("reuse", {"reading": 8}, {"base": "pipeline_all", "draft_model": "glm-5.3"}),
    "nw_sr16": ("reuse", {"reading": 16}, {"base": "pipeline_all", "draft_model": "glm-5.3"}),
    "nw_sr16b": ("reuse", {"reading": 16}, {"base": "pipeline_all", "draft_model": "glm-5.3"}),
    "nw_sr16c": ("reuse", {"reading": 16}, {"base": "pipeline_all", "draft_model": "glm-5.3"}),
    "nw_sr16d": ("reuse", {"reading": 16}, {"base": "pipeline_all", "draft_model": "glm-5.3"}),
    "nw_sr24": ("reuse", {"reading": 24}, {"base": "pipeline_all", "draft_model": "glm-5.3"}),
    "nw_sr24b": ("reuse", {"reading": 24}, {"base": "pipeline_all", "draft_model": "glm-5.3"}),
    "nw_r16": ("reuse", {"reading": 16}, {"base": "pipeline_all"}),     # flash 起草 + 原文阅读 16 句
    "nw_r16b": ("reuse", {"reading": 16}, {"base": "pipeline_all"}),
    # 全集用：以默认配置的 pipeline 为 base（默认指针式作答选项），候选改进只重跑作答
    "def_rep": ("reuse", {}, {"base": "pipeline"}),
    "def_read": ("reuse", {"reading": 8}, {"base": "pipeline"}),
    "def_strong": ("reuse", {}, {"base": "pipeline", "draft_model": "glm-5.3"}),
    "def_strong_read": ("reuse", {"reading": 8}, {"base": "pipeline", "draft_model": "glm-5.3"}),
    "def_rep2": ("reuse", {}, {"base": "pipeline"}),
    "def_read16": ("reuse", {"reading": 16}, {"base": "pipeline"}),
    "def_read16b": ("reuse", {"reading": 16}, {"base": "pipeline"}),
    "def_noread": ("reuse", {"reading": 0}, {"base": "pipeline"}),     # 默认改为读 16 句后的“改动前”对照
    "def_noread2": ("reuse", {"reading": 0}, {"base": "pipeline"}),
    # v9.16.2 起默认不读原文：同时段对照（def_now ×2）对两份草稿合并（merge2 ×2），--recall-from 复用全集的召回
    # （E22 时 def_now 为单份草稿；v9.16.6 起默认 drafts=2，def_now 即两份合并，单份对照用 single 组）
    "single": ("reuse", {"drafts": 1}, {"base": "pipeline"}),
    # 低分题（E25）：补摘要 + 参考文献回溯（只从早于题目日期的论文回溯）
    "fix": ("reuse", {"abstract_backfill": True, "ref_chase": 15}, {"base": "pipeline"}),
    "fix2": ("reuse", {"abstract_backfill": True, "ref_chase": 15}, {"base": "pipeline"}),
    # 召回到位的低分题（E26）：两份合并（v9.16.6 默认）+ 原文阅读 16 句
    "read_m": ("reuse", {"reading": 16}, {"base": "pipeline"}),
    "read_m2": ("reuse", {"reading": 16}, {"base": "pipeline"}),
    # E27 召回修复后重测（2×2：旧召回/新召回 × 不读/读原文），--recall-from 把 E21 的 pipeline 记为 pipeline_e21
    "pipeline_e21": ("pipeline", {}),
    "pipeline_v917": ("pipeline", {}, {"ref_chase": 20}),
    "old_def": ("reuse", {}, {"base": "pipeline_e21"}),
    "old_read": ("reuse", {"reading": 16}, {"base": "pipeline_e21"}),
    "new_def": ("reuse", {}, {"base": "pipeline_v917"}),
    "new_read": ("reuse", {"reading": 16}, {"base": "pipeline_v917"}),
    # E28 发表时间截断后的基线（同一批召回：不截断 / 常规档 / 严格档，作答均为 v9.16.6 默认）
    "tm_none": ("reuse", {}, {"base": "pipeline"}),
    "tm_regular": ("reuse", {}, {"base": "pipeline", "time_machine": "regular"}),
    "tm_strict": ("reuse", {}, {"base": "pipeline", "time_machine": "strict"}),
    "merge3": ("reuse", {"drafts": 3}, {"base": "pipeline"}),     # 三份草稿合并（对 v9.16.6 默认两份）
    "merge3b": ("reuse", {"drafts": 3}, {"base": "pipeline"}),
    "def_now": ("reuse", {}, {"base": "pipeline"}),
    "def_now2": ("reuse", {}, {"base": "pipeline"}),
    "merge2": ("reuse", {"drafts": 2}, {"base": "pipeline"}),
    "merge2b": ("reuse", {"drafts": 2}, {"base": "pipeline"}),
    "mergeb": ("reuse", {"drafts": 2, "merge_mode": "base"}, {"base": "pipeline"}),   # 以第一份为主体补入
    "mergeb2": ("reuse", {"drafts": 2, "merge_mode": "base"}, {"base": "pipeline"}),
    "ctx_mark": ("reuse", {**_FT, "web_search": "scopus", "draft_context": 15, "final_mode": "mark"},
                 {"base": "pipeline_all"}),
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


def resume_items(items: list[dict], meta: dict, records: list[dict], subset_arg: int = 0) -> list[dict]:
    """续跑：按原抽样参数重新抽题，再并入已保存但不在抽样里的题。
    只取已保存的记录会漏掉中途停下时还没跑到的题（10-28 实测：子集跑到 4/10 被停后续跑直接结束）。"""
    sub = subset_arg or meta.get("subset") or 0
    picked = pick_items(items, seed=meta["seed"], n_noncs=meta["n_noncs"], n_cs=meta["n_cs"])
    if sub:
        picked = subset(picked, sub)
    if meta.get("first"):
        picked = picked[:meta["first"]]
    meta["subset"] = sub
    ids = {x["id"] for x in picked}
    return picked + [{**r["item"], "group": r["group"]} for r in records if r["id"] not in ids]


def recall_seed(records: list[dict], picked: list[dict], arms: list[str], name: str) -> dict[str, dict]:
    """--recall-from：从另一次结果里只取本次复用组要用的 base 组记录（含已保存论文的路径与分数），
    这些 base 组不再重跑；其余组（含 bare）都在本次同时段重新跑。"""
    by_id = {r["id"]: r for r in records}
    bases = {ARM_SPECS[a][2]["base"] for a in arms if ARM_SPECS[a][0] in ("reuse", "draft_of")}
    out = {}
    for x in picked:
        r = by_id.get(x["id"])
        if r:
            src = {b: RECALL_ALIAS.get(b, b) for b in sorted(bases)}
            out[x["id"]] = {**r, "arms": {b: {**r["arms"][s], "copied_from": name}
                                          for b, s in src.items() if s in r["arms"]}}
    return out


RECALL_ALIAS = {"pipeline_e21": "pipeline"}   # 复用召回时本次组名 → 来源结果里的组名


async def scopus_remaining() -> int | None:
    """Scopus 周配额剩余（一次 count=1 的检索看响应头）；查不到或本地检索模式时返回 None。"""
    from backend.services.deep_research.providers import scopus_local

    if scopus_local.mode() == "local":
        return None
    import httpx

    from backend.config import settings
    from backend.services.ai_config import get_research_config

    key = settings.elsevier_api_key or get_research_config().get("elsevier_api_key", "")
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://api.elsevier.com/content/search/scopus",
                            params={"query": "TITLE-ABS-KEY(retrieval)", "count": 1, "field": "dc:identifier"},
                            headers={"X-ELS-APIKey": key, "Accept": "application/json"})
        return int(r.headers.get("x-ratelimit-remaining")) if r.headers.get("x-ratelimit-remaining") else None
    except Exception:  # noqa: BLE001
        return None


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
                 ("pipeline_all", "pipeline_ft"), ("pipeline_ctx", "pipeline_all"), ("pipeline_ctx", "bare"),
                 ("all_answer", "pipeline_all"), ("ctx_answer", "all_answer"), ("ctx_answer", "bare"),
                 ("all_answer", "ft_answer"), ("pipeline_all", "bare"),
                 ("pipeline_all", "pipeline_draft"), ("pipeline_draft", "bare"),
                 ("mark_answer", "all_answer2"), ("ctx_mark", "mark_answer"), ("mark_answer", "pipeline_draft"),
                 ("ctx_mark", "bare"), ("all_answer2", "all_answer"), ("mark_answer2", "all_answer3"),
                 ("strong_draft", "all_answer2"), ("strong_draft2", "all_answer3"),
                 ("def_rep", "bare"), ("def_read", "def_rep"), ("def_strong", "def_rep"),
                 ("def_strong_read", "def_rep"), ("def_read", "bare"), ("def_strong_read", "bare"),
                 ("def_read16", "def_rep"), ("def_read16", "bare"), ("def_rep", "pipeline"), ("pipeline", "def_noread"),
                 ("def_rep", "def_noread"), ("def_noread", "bare"), ("def_now2", "def_now"), ("merge2", "def_now"),
                 ("merge2b", "def_now2"), ("merge2b", "merge2"), ("merge2", "bare"),
                 ("mergeb", "def_now"), ("mergeb2", "def_now2"), ("mergeb", "merge2"),
                 ("merge3", "def_now"), ("merge3b", "def_now2"), ("single", "def_now"),
                 ("fix", "def_now"), ("fix2", "def_now2"), ("read_m", "def_now"), ("read_m2", "def_now2"),
                 ("new_def", "old_def"), ("pipeline_v917", "old_def"), ("new_read", "old_read"),
                 ("old_read", "old_def"), ("new_read", "new_def"),
                 ("tm_regular", "tm_none"), ("tm_strict", "tm_none"), ("tm_strict", "tm_regular"),
                 ("tm_none", "bare"), ("tm_regular", "bare"), ("tm_strict", "bare")):
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


async def _answer(arm: str, item: dict, *, system_cfg: dict, depth: str, runs_dir: Path, engine, mirror,
                  base_saved: str | None = None) -> dict:
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
    if mode == "draft_of":
        if not base_saved or not Path(base_saved).exists():
            raise RuntimeError(f"草稿组：{run_opts.get('base')} 组没有可用结果")
        base = json.loads(Path(base_saved).read_text(encoding="utf-8"))["result"].get("answer") or {}
        return {"response": str(base.get("draft") or ""), "status": "ok" if base.get("draft") else "no_draft"}
    if mode in ("agent", "reuse"):
        from backend.services.deep_research.evidence_search import ScopusSearch

        async def answer_json(system: str, user: str) -> dict:
            return await _llm_json(system_cfg, system, user, max_tokens=6000)
        papers: list[dict] = []
        if mode == "reuse":
            if not base_saved or not Path(base_saved).exists():
                raise RuntimeError(f"复用召回：{run_opts.get('base')} 组没有可用结果")
            papers = json.loads(Path(base_saved).read_text(encoding="utf-8"))["result"].get("papers") or []
        paper_filter = _time_filter(item, run_opts)
        if paper_filter is not None:   # 发表时间截断：复用的召回先过滤，作答阶段的补检也走同一个过滤器
            papers = [p for p in papers if paper_filter(p)]
        opts = {**RECOMMENDED, **extra}
        if opts.get("ref_chase"):   # 只从早于题目日期（出题综述发表时间）的论文回溯参考文献，防止拿到出题来源
            opts["chase_before_year"] = int(str(item.get("date") or "9999")[:4])
        web = ScopusSearch() if opts.pop("web_search", None) == "scopus" else None
        draft_json = None
        if run_opts.get("draft_model"):
            from backend.eval.scholarqa_cs import judge_cfg

            draft_cfg = {**judge_cfg(run_opts["draft_model"]), "thinking_effort": "off"}

            async def draft_json(system: str, user: str) -> dict:
                return await _llm_json(draft_cfg, system, user, max_tokens=6000, thinking_effort="off")
        answer = await write_with_pointers(query, papers, llm_json=answer_json, mirror=mirror, web_search=web,
                                           draft_json=draft_json, paper_filter=paper_filter, **opts)
        save.write_text(json.dumps({"result": {"answer": answer}}, ensure_ascii=False), encoding="utf-8")
    else:
        options = {"answer_only": True, "answer_writer": "agent", **run_opts,
                   **({"answer_agent": extra} if extra else {})}
        if options.get("ref_chase"):   # 召回阶段参考文献回溯只用早于题目日期的种子（出题综述及其后的论文不作种子）
            options["chase_before_year"] = int(str(item.get("date") or "9999")[:4])
        if options.get("time_machine"):
            from backend.services.deep_research.time_machine import question_year
            options["question_year"] = question_year(item.get("date"))
        sysrec = await explore_eval.run_system_arm(query, depth, save_as=save, engine=engine, options=options)
        if sysrec.get("error"):
            raise RuntimeError(sysrec["error"])
        answer = json.loads(save.read_text(encoding="utf-8"))["result"].get("answer") or {}
    ag = answer.get("agent") or {}
    return {"response": render_answer(answer), "status": answer.get("status"), "check": answer.get("check"),
            "materials": answer.get("materials"), "saved": str(save), "full_text": ag.get("full_text"),
            "found": {k: ag.get(k) for k in ("recall_papers", "works_found", "works_web_found", "search_added",
                                              "question_search_added", "claim_search_added")}}


async def run(args) -> Path:
    from backend.eval import explore_eval
    from backend.eval.public_benchmarks import QuotaWatch, _llm_cfg
    from backend.eval.scholarqa_cs import disable_openalex
    from backend.services.deep_research.arxiv_mirror import default_mirror
    from backend.services.llm import start_token_ledger, token_ledger_report

    disable_openalex()
    _use_scopus_cache(args)
    system_cfg = _llm_cfg(args.thinking_effort, args.model)
    judge = make_judge(args.judge_model, args.judge_repeats)
    prev: dict[str, dict] = {}
    if args.resume:
        old = json.loads(Path(args.resume).read_text(encoding="utf-8"))
        prev = {r["id"]: r for r in old["records"]}
        meta = old["meta"]
        out = Path(args.resume)
        arms = meta["arms"]
        picked = resume_items(load_items(), meta, old["records"], args.subset)
        if args.add_arms:   # 在已有结果上补跑新组（复用召回的组用已保存的 base 组论文）
            arms = meta["arms"] = list(dict.fromkeys([*arms, *args.add_arms]))
    else:
        picked = pick_items(load_items(), seed=args.seed, n_noncs=args.n_noncs, n_cs=args.n_cs)
        if args.subset:
            picked = subset(picked, args.subset)
        if args.first:
            picked = picked[:args.first]
        if args.ids:   # 只跑指定的几道题（低分题诊断）
            picked = [x for x in picked if x["id"] in set(args.ids)]
        arms = list(args.arms)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = OUT_DIR / f"researchqa_{stamp}.json"
        meta = {"seed": args.seed, "n": len(picked), "n_noncs": args.n_noncs, "n_cs": args.n_cs, "arms": arms,
                "subset": args.subset, "first": args.first,
                "depth": args.depth, "system_model": system_cfg.get("model"), "judge_model": args.judge_model,
                "judge_repeats": args.judge_repeats,
                "started": stamp, "label": args.label, "concurrency": args.concurrency,
                "openalex": False, "provenance": ledger.provenance(out)}
    if args.recall_from:
        # 复用另一次结果里 base 组的召回（不调 Scopus）：只把 base 组记录带进来，新组都在本次同时段跑
        src = Path(args.recall_from)
        seeded = recall_seed(json.loads(src.read_text(encoding="utf-8"))["records"], picked, arms, src.name)
        prev = {**seeded, **prev}
        meta["recall_from"] = str(src)
    runs_dir = out.with_name(out.stem + "_runs")
    runs_dir.mkdir(parents=True, exist_ok=True)
    log = ledger.open_log(out)
    mirror = default_mirror() if any(ARM_SPECS[a][0] in ("agent", "reuse") for a in arms) else None
    for a in arms:   # 复用召回、草稿组必须带上它的 base 组
        if ARM_SPECS[a][0] in ("reuse", "draft_of") and ARM_SPECS[a][2]["base"] not in arms:
            raise SystemExit(f"{a} 需要同时跑 {ARM_SPECS[a][2]['base']} 组")
    gate = asyncio.Semaphore(max(1, args.concurrency))
    done: dict[str, dict] = dict(prev)
    finished = [sum(1 for x in picked if all(((prev.get(x["id"]) or {}).get("arms") or {}).get(a, {}).get("coverage")
                                             is not None for a in arms))]   # 进度按本次各组都已跑完的题数计
    started = time.monotonic()

    def save() -> None:
        records = [done[x["id"]] for x in picked if x["id"] in done]
        out.write_text(json.dumps({"meta": meta, "partial": True, "records": records}, ensure_ascii=False,
                                  indent=1), encoding="utf-8")

    watch = QuotaWatch.install()

    async def one_arm(arm: str, item: dict, engine, base_saved: str | None = None) -> dict:
        """限流期间流程按失败降级（筛选放行、规划停止）而不报错：本组结果作废，等额度重置后重跑。"""
        for n_try in range(QuotaWatch.MAX_RETRIES + 1):
            scope = QuotaWatch.scope()   # 本任务内单独计数（并发时不串组）
            rec = await _one_arm(arm, item, engine, base_saved)
            limited = scope["hits"] or "RATE_LIMITED" in str(rec.get("error") or "")
            if limited:
                watch.note(f"{rec.get('error') or ''} {scope.get('sample') or ''}")
            if limited and n_try < QuotaWatch.MAX_RETRIES:
                print(f"    ⚠ {item['id']} {arm} 模型限流，作废重跑（{(scope['sample'] or rec.get('error') or '')[:100]}）",
                      flush=True)
                await watch.wait_for_reset()
                continue
            break
        rec["attempts"] = n_try + 1
        if limited:
            rec |= {"rate_limited": (scope["sample"] or rec.get("error") or "")[:300], "coverage": None}
        return rec

    async def _one_arm(arm: str, item: dict, engine, base_saved: str | None = None) -> dict:
        t0 = time.monotonic()
        tokens = start_token_ledger()
        try:
            rec = await _answer(arm, item, system_cfg=system_cfg, depth=args.depth, runs_dir=runs_dir,
                                engine=engine, mirror=mirror, base_saved=base_saved)
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
        first = [a for a in todo if ARM_SPECS[a][0] not in ("reuse", "draft_of")]
        later = [a for a in todo if ARM_SPECS[a][0] in ("reuse", "draft_of")]
        async with gate:
            if args.scopus_reserve and any(ARM_SPECS[a][0] == "pipeline" for a in first):
                # 配额耗尽时 Scopus 静默失败、召回退化成只剩 arXiv：剩余低于预留就留到配额重置后 --resume 续跑
                left = await scopus_remaining()
                if left is not None and left < args.scopus_reserve:
                    print(f"    ⏸ Scopus 周配额只剩 {left}（预留 {args.scopus_reserve}），{item['id']} 留待配额重置后续跑",
                          flush=True)
                    return
            res = dict(zip(first, await asyncio.gather(*(one_arm(a, item, engine) for a in first))))   # 各组同时提交
            got = {**old, **res}
            res |= dict(zip(later, await asyncio.gather(*(
                one_arm(a, item, engine, (got.get(ARM_SPECS[a][2]["base"]) or {}).get("saved")) for a in later))))
        rec = {"id": item["id"], "group": item["group"], "domain": item["general_domain"], "field": item["field"],
               "item": {k: item[k] for k in ("id", "general_domain", "subdomain", "field", "query", "date", "rubric")},
               "arms": {**old, **res}}
        done[item["id"]] = rec
        cov = "  ".join(f"{a}={(rec['arms'][a].get('coverage') if rec['arms'][a].get('coverage') is not None else 'ERR')}"
                        for a in arms)
        finished[0] += 1
        print(f"[{finished[0]}/{len(picked)}] {item['group']} {item['field']}：{item['query'][:70]}\n    → {cov}"
              f"  [用时 {round((time.monotonic() - started) / 60, 1)} 分]", flush=True)
        for a in todo:
            if rec["arms"][a].get("error"):
                print(f"      {a} 出错：{rec['arms'][a]['error'][:160]}", flush=True)
        save()

    with explore_eval.shared_engine() as engine:
        await asyncio.gather(*(worker(x, engine) for x in picked))
    records = [done[x["id"]] for x in picked if x["id"] in done]
    meta["wall_min"] = round(meta.get("wall_min", 0) + (time.monotonic() - started) / 60, 1)
    meta["scopus_cache"] = _scopus_cache_stats()
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


def _time_filter(item: dict, run_opts: dict):
    """组配置里 time_machine=strict/regular 时按题目日期截断（见 services/deep_research/time_machine）。"""
    from backend.services.deep_research.time_machine import make_filter, question_year

    return make_filter(item["query"], question_year(item.get("date")), str(run_opts.get("time_machine") or ""))


def citation_targets(item: dict) -> list[str]:
    """细则里要求引用的文献题名（Citation 类细则写成 “… (title: XXX) …”）。"""
    out = []
    for it in item.get("rubric") or []:
        text = it if isinstance(it, str) else str(it.get("rubric_item") or "")
        m = re.search(r"title:\s*(.+?)\)\s", text + " ")
        if m:
            out.append(m.group(1).strip())
    return out


async def recall_check(args) -> Path:
    """只跑召回阶段（不作答、不打分）：同一题各配置同时段跑召回引擎，看细则要引用的文献是否进入最终论文、
    论文带摘要的比例。检索式沿用 --queries-from 那次结果里保存的（E21 全集），只比较召回引擎本身。"""
    from backend.eval.public_benchmarks import QuotaWatch, _llm_cfg
    from backend.eval.scholarqa_cs import disable_openalex
    from backend.services.deep_research.evidence_search import title_key
    from backend.services.deep_research.recall_engine import LISTWISE_TOP, run_target_recall
    from backend.services.deep_research.reranker import rerank_scores
    from backend.services.llm import start_token_ledger, token_ledger_report

    disable_openalex()
    _use_scopus_cache(args)
    system_cfg = _llm_cfg(args.thinking_effort, args.model)
    src = {r["id"]: r for r in json.loads(Path(args.queries_from).read_text(encoding="utf-8"))["records"]}
    picked = pick_items(load_items(), seed=args.seed, n_noncs=args.n_noncs, n_cs=args.n_cs)
    if args.subset:
        picked = subset(picked, args.subset)
    if args.ids:
        picked = [x for x in picked if x["id"] in set(args.ids)]
    variants = {"base": {}, **{k: json.loads(v) for k, v in (x.split("=", 1) for x in args.variant)}}
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / f"researchqa_recall_{stamp}.json"
    log = ledger.open_log(out)
    meta = {"n": len(picked), "variants": variants, "queries_from": args.queries_from, "label": args.label,
            "system_model": system_cfg.get("model"), "provenance": ledger.provenance(out)}
    watch = QuotaWatch.install()
    gate = asyncio.Semaphore(max(1, args.concurrency))
    records = []

    async def one(item: dict, name: str, kw: dict) -> dict:
        saved = json.loads(Path(src[item["id"]]["arms"]["pipeline"]["saved"]).read_text(encoding="utf-8"))["result"]
        queries = (saved.get("decompose") or {}).get("en_queries") or [item["query"]]
        year = int(str(item.get("date") or "9999")[:4])
        scope, tokens, t0 = QuotaWatch.scope(), start_token_ledger(), time.monotonic()
        try:
            papers, audit = await run_target_recall(
                item["query"], queries, depth=args.depth, now_year=datetime.now().year, llm_cfg=system_cfg,
                sources=("arxiv", "scopus"), rerank_scores=rerank_scores, listwise_top=LISTWISE_TOP,
                chase_before_year=year, **kw)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"[:300]}
        have = {title_key(p.get("title") or "") for p in papers}
        targets = citation_targets(item)
        hits = [t for t in targets if title_key(t) in have]
        return {"papers": len(papers), "with_abstract": sum(1 for p in papers if (p.get("abstract") or "").strip()),
                "targets": len(targets), "hits": hits, "ref_chase": audit.get("ref_chase"),
                "abstract_backfill": audit.get("abstract_backfill"), "rate_limited": bool(scope["hits"]),
                "tokens": token_ledger_report(tokens), "wall_s": round(time.monotonic() - t0, 1)}

    async def worker(item: dict) -> None:
        async with gate:
            res = await asyncio.gather(*(one(item, n, kw) for n, kw in variants.items()))   # 各配置同时段
        rec = {"id": item["id"], "group": item["group"], "domain": item["general_domain"], "query": item["query"],
               "variants": dict(zip(variants, res))}
        records.append(rec)
        print(f"[{len(records)}/{len(picked)}] {item['field']}：{item['query'][:60]}\n    → " + "  ".join(
            f"{n}: 论文 {v.get('papers')}，带摘要 {v.get('with_abstract')}，命中 {len(v.get('hits') or [])}/{v.get('targets')}"
            + ("（限流）" if v.get("rate_limited") else "") for n, v in rec["variants"].items()), flush=True)

    await asyncio.gather(*(worker(x) for x in picked))
    summary = {}
    for n in variants:
        rows = [r["variants"][n] for r in records if "papers" in r["variants"][n]]
        summary[n] = {"questions": len(rows), "papers": sum(r["papers"] for r in rows),
                      "abstract_share": round(sum(r["with_abstract"] for r in rows) / max(1, sum(r["papers"] for r in rows)), 3),
                      "target_hits": sum(len(r["hits"]) for r in rows), "targets": sum(r["targets"] for r in rows)}
    meta["scopus_cache"] = _scopus_cache_stats()
    report = {"meta": meta, "summary": summary, "records": records}
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    ledger.record_metrics(out, report, benchmark="researchqa_recall",
                          lines=[f"- {n}：{v['questions']} 题，论文 {v['papers']} 篇，带摘要 {v['abstract_share']:.0%}，"
                                 f"细则所引文献命中 {v['target_hits']}/{v['targets']}" for n, v in summary.items()])
    log.close()
    return out


def _use_scopus_cache(args) -> None:
    """--scopus-cache record|local：Scopus 响应写入本地库 / 只用本地库（不耗 Scopus 配额，见 providers/scopus_local）。"""
    import os

    if getattr(args, "scopus_cache", ""):
        os.environ["ESSAY_SCOPUS_CACHE"] = args.scopus_cache
        if args.scopus_cache != "off":
            print(f"Scopus 本地缓存：{args.scopus_cache}", flush=True)


def _scopus_cache_stats() -> dict | None:
    from backend.services.deep_research.providers import scopus_local

    local = scopus_local.active()
    return {"mode": scopus_local.mode(), **local.stats()} if local else None


def seed_cache(args) -> None:
    """把已保存结果里各组召回到的论文导入 Scopus 本地论文库（local 模式检索的底库）。"""
    from backend.services.deep_research.providers import scopus_local

    n = 0
    for f in args.files:
        for r in json.loads(Path(f).read_text(encoding="utf-8"))["records"]:
            for arm in (r.get("arms") or {}).values():
                saved = arm.get("saved")
                if saved and Path(saved).exists():
                    papers = (json.loads(Path(saved).read_text(encoding="utf-8")).get("result") or {}).get("papers")
                    n += scopus_local.seed_from_papers(papers or [])
    local = scopus_local.active() or scopus_local.ScopusLocal(scopus_local.default_path())
    print(f"导入 {n} 条；本地库 {local.stats()}", flush=True)


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
    r.add_argument("--ids", nargs="+", default=[], help="只跑抽样中的这几道题（按题目 id，低分题诊断）")
    r.add_argument("--depth", default="quick", help="pipeline 组的研究深度")
    r.add_argument("--thinking-effort", default="off")
    r.add_argument("--model", default="", help="覆盖系统模型（默认用设置里的模型）")
    r.add_argument("--concurrency", type=int, default=3, help="同时在跑的题数（每题各组同时提交）")
    r.add_argument("--scopus-reserve", type=int, default=0,
                   help="每题跑完整流程前查 Scopus 周配额，低于此数就跳过（留待重置后 --resume），0 不检查")
    r.add_argument("--resume", default="", help="沿用这次结果，只补跑缺失或出错的组")
    r.add_argument("--add-arms", nargs="+", choices=list(ARM_SPECS), default=[],
                   help="与 --resume 合用：在已有结果上补跑这些组（同时带一个复本组作同时段对照）")
    r.add_argument("--recall-from", default="",
                   help="复用召回的组从这次结果里取 base 组已保存的论文（新文件；base 组不重跑、不调 Scopus）")
    r.add_argument("--label", default="")
    sc = sub.add_parser("seed-cache", help="把已保存结果里的召回论文导入 Scopus 本地论文库")
    sc.add_argument("files", nargs="+")
    rc = sub.add_parser("recall", help="只跑召回阶段，看细则所引文献是否召回、论文是否带摘要")
    rc.add_argument("--queries-from", required=True, help="沿用这次结果里保存的检索式（pipeline 组）")
    rc.add_argument("--n-noncs", type=int, default=40)
    rc.add_argument("--n-cs", type=int, default=10)
    rc.add_argument("--seed", type=int, default=0)
    rc.add_argument("--subset", type=int, default=0)
    rc.add_argument("--ids", nargs="+", default=[])
    rc.add_argument("--variant", action="append", default=[], help='与 base 同时段对比的召回配置，如 chase=\'{"ref_chase": 20}\'')
    rc.add_argument("--depth", default="quick")
    rc.add_argument("--thinking-effort", default="off")
    rc.add_argument("--model", default="")
    rc.add_argument("--concurrency", type=int, default=2)
    rc.add_argument("--label", default="")
    j = sub.add_parser("judge")
    j.add_argument("file")
    j.add_argument("--concurrency", type=int, default=6)
    for p in (r, rc):
        p.add_argument("--scopus-cache", choices=("off", "record", "local"), default="record",
                       help="record（默认）：Scopus 响应写入本地库、同一请求用缓存；local：不调 Scopus，召回只用本地库——"
                            "改召回以外的环节（筛选、精读、作答等）时同一批题反复重跑不耗检索额度；off：不用缓存")
    for p in (r, j):
        p.add_argument("--judge-model", default=JUDGE_MODEL)
        p.add_argument("--judge-repeats", type=int, default=1, help="同一答案独立打分几次取平均（压低评委噪声）")
    args = parser.parse_args()
    if args.cmd == "seed-cache":
        import os

        os.environ.setdefault("ESSAY_SCOPUS_CACHE", "record")
        return seed_cache(args)
    asyncio.run({"run": run, "judge": rejudge, "recall": recall_check}[args.cmd](args))


if __name__ == "__main__":
    main()
