"""Crawler：PaSa 的三个动作 [Search] / [Expand] / [Stop]，加一轮按已选论文的补检（refine）。

模型只做两件窄事：写检索式、从章节名里挑章节。检索、解析引用、去重、判定都不经过模型。
"""

from __future__ import annotations

import logging
import re

from .config import LoopConfig
from .llm import LlmJson
from .papers import terms, title_key
from .sections import fetch_sections
from .sources import Sources

logger = logging.getLogger(__name__)

_SYSTEM = "你是文献检索专家。只输出一个 JSON 对象。"
_FALLBACK_SECTIONS = re.compile(r"related|background|prior|literature|preliminar|introduction|overview", re.I)
_CJK = re.compile(r"[㐀-鿿]")


def _clean_query(q: str) -> str:
    q = re.sub(r"[\"“”()\[\]{}]|\b(?:AND|OR|NOT)\b|\w+:", " ", str(q or ""))
    return re.sub(r"\s+", " ", q).strip()[:200]


def _queries_prompt(question: str, n: int, conditions: list[str]) -> str:
    lines = [f"## 研究问题\n{question}"]
    if conditions:
        lines += ["", "## 补充条件", *[f"- {c}" for c in conditions]]
    lines += [
        "", "## 任务",
        f"写 {n} 条英文学术检索式，用于在 arXiv / Scopus / OpenAlex 中找到回答该问题所需的论文：",
        "- 每条 3~10 个词，用领域里的标准术语（方法名、任务名、数据集、对象），不要写布尔运算符和引号；",
        "- 各条互不重叠，分别覆盖问题的不同方面（对象、方法、评测、应用、局限等），合起来覆盖整个问题；",
        "- 其中一条面向综述（survey / review），综述的参考文献是后续展开的好起点。",
        '输出：{"queries": ["...", "..."]}',
    ]
    return "\n".join(lines)


def _sections_prompt(question: str, paper: dict, names: list[str], k: int) -> str:
    lines = [f"## 研究问题\n{question}", "", "## 正在阅读的论文",
             f"题名：{paper.get('title') or ''}", f"摘要：{(paper.get('abstract') or '')[:900]}", "",
             "## 论文章节（每个章节里都有引用的参考文献）", *[f"- {n}" for n in names], "",
             "## 任务",
             f"为了找到更多与研究问题相关的论文，应该追踪哪些章节里引用的文献？最多选 {k} 个章节，"
             "章节名照抄上面的写法；没有值得追的章节就返回空列表。",
             '输出：{"sections": ["..."]}']
    return "\n".join(lines)


def _refine_prompt(question: str, selected: list[dict], tried: list[str], n: int) -> str:
    lines = [f"## 研究问题\n{question}", "", "## 已经找到的相关论文（题名）"]
    lines += [f"- {p.get('title')}" + (f" ({p['year']})" if p.get("year") else "") for p in selected[:40]]
    lines += ["", "## 已经用过的检索式", *[f"- {q}" for q in tried[-30:]], "",
              "## 任务",
              f"对照研究问题，已找到的论文还缺哪些方面？为缺的方面写至多 {n} 条新的英文检索式（3~10 个词，"
              "不要与用过的重复）。各方面都已覆盖时返回空列表。",
              '输出：{"queries": ["..."]}']
    return "\n".join(lines)


class Crawler:
    def __init__(self, question: str, llm_json: LlmJson | None, sources: Sources, cfg: LoopConfig, *,
                 conditions: list[str] | None = None):
        self.question = question
        self.llm_json = llm_json
        self.sources = sources
        self.cfg = cfg
        self.conditions = list(conditions or [])
        self.stats = {"llm_calls": 0, "searches": 0, "expanded": 0, "expand_html": 0, "expand_reflist": 0,
                      "expand_failed": 0}

    async def _ask(self, prompt: str, stage: str) -> dict:
        from ... import llm

        if self.llm_json is None:
            return {}
        self.stats["llm_calls"] += 1
        try:
            with llm.llm_stage(stage):
                return await self.llm_json(_SYSTEM, prompt)
        except Exception as exc:  # noqa: BLE001 —— 窄决策失败走确定性兜底
            logger.warning("crawler %s failed: %s", stage, exc)
            return {}

    # ── [Search] ────────────────────────────────────────────────────────────
    async def initial_queries(self, extra: list[str] | None = None) -> list[str]:
        data = await self._ask(_queries_prompt(self.question, self.cfg.queries, self.conditions), "crawl_queries")
        queries = [_clean_query(q) for q in (data.get("queries") or []) if isinstance(q, str)]
        queries = [q for q in queries if len(q.split()) >= 2 and not _CJK.search(q)][:self.cfg.queries]
        if not queries and not _CJK.search(self.question):
            queries = [_clean_query(self.question)]
        return list(dict.fromkeys([*(_clean_query(q) for q in extra or [] if q), *queries]))

    async def search(self, query: str) -> list[dict]:
        self.stats["searches"] += 1
        return await self.sources.search(query, self.cfg.per_query)

    async def refine_queries(self, selected: list[dict], tried: list[str]) -> list[str]:
        data = await self._ask(_refine_prompt(self.question, selected, tried, self.cfg.refine_queries), "crawl_refine")
        seen = {q.lower() for q in tried}
        out = [_clean_query(q) for q in (data.get("queries") or []) if isinstance(q, str)]
        return [q for q in dict.fromkeys(out) if q and q.lower() not in seen and not _CJK.search(q)][:self.cfg.refine_queries]

    # ── [Expand] ────────────────────────────────────────────────────────────
    async def choose_sections(self, paper: dict, names: list[str]) -> list[str]:
        k = self.cfg.sections_per_paper
        data = await self._ask(_sections_prompt(self.question, paper, names, k), "crawl_sections")
        by_lower = {n.lower(): n for n in names}
        chosen = []
        for raw in data.get("sections") or []:
            name = by_lower.get(str(raw).strip().lower())
            if name is None:   # 模型只写了子章节名或省略了父章节
                name = next((n for n in names if str(raw).strip().lower() in n.lower()), None)
            if name and name not in chosen:
                chosen.append(name)
        if not chosen and not data:   # 模型不可用（不是“没有值得追的章节”）：按章节名兜底
            chosen = [n for n in names if _FALLBACK_SECTIONS.search(n)]
        return chosen[:k]

    async def expand(self, paper: dict) -> tuple[list[dict], dict]:
        """展开一篇论文：有 arXiv HTML 时追所选章节的引用，否则从参考文献列表按词重合取前若干条。"""
        self.stats["expanded"] += 1
        depth = int(paper.get("depth") or 0) + 1
        parsed = await fetch_sections(paper["arxiv_id"]) if paper.get("arxiv_id") else None
        if parsed is not None:
            self.stats["expand_html"] += 1
            names = parsed.names()
            chosen = await self.choose_sections(paper, names)
            refs = parsed.cited(chosen, self.cfg.refs_per_paper)
            resolved = await self.sources.resolve_titles([r.title for r in refs if r.title])
            out = []
            for ref in refs:
                hit = resolved.get(title_key(ref.title)) if ref.title else None
                if hit is None:
                    continue
                section = next((n for n in chosen if ref.bib_id in parsed.sections.get(n, [])), "")
                out.append({**hit, "origin": "expand", "parent": paper["key"], "via": section, "depth": depth})
            return out, {"mode": "sections", "sections": names, "chosen": chosen, "refs": len(refs),
                         "resolved": len(out)}
        rows = await self.sources.references(paper, limit=100)
        if not rows:
            self.stats["expand_failed"] += 1
            return [], {"mode": "none", "reason": "no_html_no_reference_list"}
        self.stats["expand_reflist"] += 1
        probe = terms(f"{self.question} {paper.get('title') or ''}")
        ranked = sorted(rows, key=lambda r: (-len(probe & terms(r.get("title") or "")), -(r.get("citation_count") or 0)))
        picked = [r for r in ranked if len(probe & terms(r.get("title") or "")) >= 1][:self.cfg.reference_fallback]
        await self.sources.fill_abstracts(picked)
        out = [{**r, "origin": "expand", "parent": paper["key"], "via": "reference_list", "depth": depth}
               for r in picked]
        return out, {"mode": "reference_list", "refs": len(rows), "picked": len(out)}
