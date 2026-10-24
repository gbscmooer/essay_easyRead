"""本地语料检索：在指定的本地论文集合里查找，替代联网的 arXiv/Scopus/OpenAlex 检索。

用途：在固定语料下测试召回（如 LitSearch 的 6.4 万篇 ML/NLP 语料），排除数据源
限流、网络抖动与语料随时间变化对评测的影响。返回格式与联网检索相同——
`search_one(source, keyword, limit, order)` 返回 `RecallCandidate` 列表，可直接注入
`run_target_recall(search_one=..., sources=("local",))`。

排序：
- relevance：BM25（k1=1.2, b=0.75），标题词计两次（标题命中权重更高），
  多词查询按 OR 语义打分，与 OpenAlex `search` 的相关性排序同类；
- cited：在 BM25 命中集合内按被引数降序（语料自带引用列表时为语料内被引数）。

语料记录字段：id（必需）、title（必需）、abstract、year、doi、arxiv_id、
citation_count、citations（本语料内的被引用 id 列表，用于计算语料内被引数）。
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from ...models.recall import RecallSource, build_recall_candidate

LOCAL_SOURCE = "local"
_K1 = 1.2
_B = 0.75
_TITLE_WEIGHT = 2
_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in into is it its of on or that the their this to was "
    "were which with via using based towards toward we our can not no do does how what when where why "
    "study studies paper papers approach approaches method methods".split())


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [_stem(t) for t in tokens if t not in _STOPWORDS and len(t) > 1]


def _stem(token: str) -> str:
    """极简复数归一（models→model、studies→study），不做激进词干化。"""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


class LocalCorpusIndex:
    def __init__(self, records: list[dict], *, name: str = "local_corpus"):
        self.name = name
        self.docs: list[dict] = []
        postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        lengths: list[int] = []
        cited_by: Counter = Counter()
        for rec in records:
            for cited in rec.get("citations") or []:
                cited_by[str(cited)] += 1
        for rec in records:
            title = str(rec.get("title") or "").strip()
            if not title or rec.get("id") is None:
                continue
            doc_id = len(self.docs)
            terms = tokenize(title) * _TITLE_WEIGHT + tokenize(rec.get("abstract") or "")
            for term, tf in Counter(terms).items():
                postings[term].append((doc_id, tf))
            lengths.append(len(terms))
            self.docs.append({**rec, "id": str(rec["id"]), "title": title,
                              "citation_count": int(rec.get("citation_count") or cited_by.get(str(rec["id"]), 0))})
        self._postings = dict(postings)
        self._lengths = lengths
        self._avg_len = (sum(lengths) / len(lengths)) if lengths else 0.0
        n = len(self.docs)
        self._idf = {t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in postings.items()}

    @classmethod
    def from_jsonl(cls, path: str | Path, *, id_field: str = "id", name: str | None = None) -> "LocalCorpusIndex":
        records = []
        with Path(path).open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    rec["id"] = rec.get(id_field)
                    records.append(rec)
        return cls(records, name=name or Path(path).stem)

    def __len__(self) -> int:
        return len(self.docs)

    def search(self, query: str, *, limit: int, order: str = "relevance") -> list[tuple[dict, float]]:
        scores: dict[int, float] = defaultdict(float)
        for term in set(tokenize(query)):
            idf = self._idf.get(term)
            if idf is None:
                continue
            for doc_id, tf in self._postings[term]:
                norm = _K1 * (1 - _B + _B * self._lengths[doc_id] / (self._avg_len or 1))
                scores[doc_id] += idf * tf * (_K1 + 1) / (tf + norm)
        if order == "cited":
            ranked = sorted(scores, key=lambda d: (-self.docs[d]["citation_count"], -scores[d]))
        else:
            ranked = sorted(scores, key=lambda d: -scores[d])
        return [(self.docs[d], scores[d]) for d in ranked[:max(0, limit)]]

    def candidate(self, doc: dict, rank: int, score: float, order: str):
        return build_recall_candidate(
            title=doc["title"], source=RecallSource.SEARCH, source_rank=rank,
            doi=doc.get("doi"), arxiv_id=doc.get("arxiv_id"), abstract=doc.get("abstract"),
            year=doc.get("year"), authors=doc.get("authors"), raw_score=round(score, 4),
            citation_count=doc.get("citation_count") or 0,
            provenance=[f"provider:{LOCAL_SOURCE}", f"corpus:{self.name}", f"method:{order}"],
            extra={"local_id": doc["id"], "local_corpus": self.name},
        )

    def as_search_one(self):
        """返回与联网检索同签名的 search_one；只响应 local 源，其他源返回空（不联网）。"""
        async def search_one(source: str, keyword: str, limit: int, order: str) -> list:
            if source != LOCAL_SOURCE:
                return []
            hits = self.search(keyword, limit=limit, order=order)
            return [c for c in (self.candidate(doc, i, score, order)
                                for i, (doc, score) in enumerate(hits)) if c is not None]
        return search_one
