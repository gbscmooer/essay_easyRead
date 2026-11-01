"""论文记录：各数据源的候选统一成 dict，按身份去重合并。

论文 dict 的字段（循环内与 result.papers 相同）：
    key, title, abstract, year, authors, doi, arxiv_id, citation_count, url, pdf_url,
    source_query                       —— 来自数据源
    origin: search|expand|refine|seed  —— 进入队列的方式
    parent: 展开出它的论文 key（search/seed 为空）；via: 检索式或章节名；depth: 第几层
    label: Selector 判定 0/1/2（None = 未判定）；rerank: 交叉编码器分；score: 排序分
"""

from __future__ import annotations

import re

from ...paper_identity import normalize_title

_WORD = re.compile(r"[a-z][a-z\-]{3,}")
_STOP = frozenset(
    "with from that this these those their there which what when where while into onto over under between "
    "among during after before about using based study studies effect effects role analysis approach approaches "
    "method methods model models paper survey towards toward via learning".split())


def paper_from_candidate(cand) -> dict | None:
    """RecallCandidate（或同形对象）→ 论文 dict；没有题名返回 None。"""
    title = (getattr(cand, "title", "") or "").strip()
    if not title:
        return None
    paper = {
        "key": getattr(cand, "canonical_key", None) or f"t:{normalize_title(title)}",
        "title": title,
        "year": getattr(cand, "year", None),
        "citation_count": getattr(cand, "citation_count", None) or 0,
        "doi": getattr(cand, "doi", None),
        "arxiv_id": getattr(cand, "arxiv_id", None),
        "openalex_id": getattr(cand, "openalex_id", None),
        "url": getattr(cand, "url", None),
        "pdf_url": getattr(cand, "pdf_url", None),
        "authors": getattr(cand, "authors", None),
        "abstract": getattr(cand, "abstract", None),
        "source_query": getattr(cand, "seed_relation", None) or "",
    }
    for k, v in (getattr(cand, "extra", None) or {}).items():
        if v and k not in paper:
            paper[k] = v
    return paper


def merge_into(cur: dict, new: dict) -> None:
    """同一篇的两条记录合并：缺的字段补上，被引数取大。"""
    for k, v in new.items():
        if v and not cur.get(k):
            cur[k] = v
    cur["citation_count"] = max(cur.get("citation_count") or 0, new.get("citation_count") or 0)


def dedupe_candidates(candidates: list) -> list[dict]:
    """按 canonical_key（缺省用规范化题名）去重；同一篇保留信息最全的一条。保持首次出现顺序。"""
    by_key: dict[str, dict] = {}
    for cand in candidates:
        paper = cand if isinstance(cand, dict) else paper_from_candidate(cand)
        if not paper or not paper.get("title"):
            continue
        paper.setdefault("key", f"t:{normalize_title(paper['title'])}")
        cur = by_key.get(paper["key"])
        if cur is None:
            by_key[paper["key"]] = dict(paper)
        else:
            merge_into(cur, paper)
    return list(by_key.values())


def title_key(title: str | None) -> str:
    return normalize_title(title or "") or ""


def aliases(paper: dict) -> list[str]:
    """同一篇论文在不同数据源下的身份（key / DOI / arXiv / 题名），用于跨源合并。"""
    from ...paper_identity import normalize_arxiv_id, normalize_doi

    out = [str(paper.get("key") or "")]
    if paper.get("doi") and normalize_doi(paper["doi"]):
        out.append("doi:" + normalize_doi(paper["doi"]))
    if paper.get("arxiv_id") and normalize_arxiv_id(paper["arxiv_id"]):
        out.append("arxiv:" + normalize_arxiv_id(paper["arxiv_id"]))
    if title_key(paper.get("title")):
        out.append("title:" + title_key(paper.get("title")))
    return [a for a in dict.fromkeys(out) if a]


def paper_text(paper: dict, limit: int = 1500) -> str:
    return f"{paper.get('title') or ''}. {(paper.get('abstract') or '')[:limit]}".strip()


def terms(text: str) -> set[str]:
    """词干（前 6 个字母）集合，用于不花模型调用的粗略相关性（参考文献列表排序）。"""
    return {w[:6] for w in _WORD.findall((text or "").lower()) if w not in _STOP}


def public(paper: dict) -> dict:
    """落库/推送用：去掉下划线开头的内部字段与大段正文。"""
    return {k: v for k, v in paper.items() if not k.startswith("_") and k not in ("source_text",)}
