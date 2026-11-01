"""评测用的发表时间截断（防止看到出题来源）。

ResearchQA 每题出自一篇综述，题目日期 = 综述发表日期。召回到出题综述本身，或晚于题目日期、引用了它的论文，
等于看到答案来源（E26：动物生物学题召回了出题的 2010 综述，原文阅读 +0.30）。E21 召回里 43% 晚于题目年份。
两档规则，都是确定性的（不打分、不调模型）：
- strict（时间机器）：只留年份不晚于题目年份的论文；同年的综述排除；年份未知的排除。
  回答“系统的增益是不是看了答案来源”。
- regular（常规）：只排除疑似出题综述——同年的综述，以及不早于题目年份、题名与问题词干高度重合的论文。
  对应真实使用（直接问模型时模型自带题目日期之后的知识，严格档对系统并不完全公平）。
所有收录论文的入口（召回结果、作答阶段的镜像/在线补检、点名查找、参考文献回溯）共用同一个过滤器。
"""

from __future__ import annotations

import re
from typing import Callable

REVIEW_TITLE = re.compile(r"review|meta-analy|systematic|survey|overview|state of the art|progress in", re.I)
TITLE_OVERLAP = 0.5   # 常规档：题名与问题的词干重合（Jaccard）达到这个比例视为疑似出题综述

_WORD = re.compile(r"[a-z][a-z\-]{3,}")
_STOP = frozenset("with from that this these those their there which what when where while into onto over under "
                  "between among during after before about using based have been does used other such than "
                  "they them also more most".split())


def _stems(text: str) -> set[str]:
    return {w[:6] for w in _WORD.findall((text or "").lower()) if w not in _STOP}


def _year(paper: dict) -> int | None:
    y = paper.get("year")
    try:
        return int(y) if y not in (None, "") else None
    except (TypeError, ValueError):
        return None


def make_filter(question: str, question_year: int | None, tier: str) -> Callable[[dict], bool] | None:
    """返回 paper -> 是否收录；tier 为空或题目年份未知时返回 None（不过滤）。"""
    if tier not in ("strict", "regular") or not question_year:
        return None
    q = _stems(question)

    def admit(paper: dict) -> bool:
        year = _year(paper)
        title = str(paper.get("title") or "")
        if year == question_year and REVIEW_TITLE.search(title):
            return False
        if tier == "strict":
            return year is not None and year <= question_year
        if year is not None and year >= question_year:
            t = _stems(title)
            if t and len(q & t) / len(q | t) >= TITLE_OVERLAP:
                return False
        return True

    return admit


def question_year(date) -> int | None:
    m = re.match(r"\s*(\d{4})", str(date or ""))
    return int(m.group(1)) if m else None
