"""
BM25 词法索引 — 工作区 chunk 级检索。

可从 Chroma 文档或显式 chunk 列表构建；查询返回 chunk 级命中。
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def aggregate_paper_scores(
    chunk_scores: dict[str, float],
    chunk_paper: dict[str, int],
    *,
    method: str = "max",
) -> dict[int, float]:
    """将 chunk 级 BM25 分聚合到 paper 级（max 或 top3 sum）。"""
    by_paper: dict[int, list[float]] = {}
    for cid, score in chunk_scores.items():
        pid = chunk_paper.get(cid)
        if pid is None:
            continue
        by_paper.setdefault(pid, []).append(score)

    out: dict[int, float] = {}
    for pid, scores in by_paper.items():
        scores.sort(reverse=True)
        if method == "top3_sum":
            out[pid] = sum(scores[:3])
        else:
            out[pid] = scores[0] if scores else 0.0
    return out


class BM25Index:
    """轻量 BM25，面向 workspace chunk 文档。"""

    def __init__(self, *, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._docs: dict[str, list[str]] = {}
        self._metas: dict[str, dict[str, Any]] = {}
        self._raw: dict[str, str] = {}
        self._df: Counter[str] = Counter()
        self._avgdl = 0.0
        self._n_docs = 0

    @property
    def chunk_ids(self) -> list[str]:
        return list(self._docs.keys())

    def chunk_meta(self, chunk_id: str) -> dict[str, Any]:
        meta = dict(self._metas.get(chunk_id, {}))
        meta["chunk_id"] = chunk_id
        meta["snippet"] = self._raw.get(chunk_id, "")
        return meta

    def build(self, chunks: list[dict[str, Any]]) -> None:
        """从 chunk 列表构建索引。每项需含 id 与 text/snippet/document。"""
        self._docs.clear()
        self._metas.clear()
        self._raw.clear()
        self._df.clear()

        for chunk in chunks:
            cid = str(chunk.get("id") or chunk.get("chunk_id") or "")
            if not cid:
                continue
            text = chunk.get("text") or chunk.get("snippet") or chunk.get("document") or ""
            tokens = tokenize(text)
            if not tokens:
                continue
            self._docs[cid] = tokens
            self._raw[cid] = text
            meta = {k: v for k, v in chunk.items() if k not in ("id", "chunk_id", "text", "snippet", "document")}
            self._metas[cid] = meta
            for term in set(tokens):
                self._df[term] += 1

        self._n_docs = len(self._docs)
        if self._n_docs:
            self._avgdl = sum(len(toks) for toks in self._docs.values()) / self._n_docs
        else:
            self._avgdl = 0.0

    def query(
        self,
        question: str,
        *,
        top_k: int = 30,
        paper_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """返回按 BM25 分降序的 chunk 命中。"""
        if not self._docs or not question.strip():
            return []

        q_terms = tokenize(question)
        if not q_terms:
            return []

        allowed: set[int] | None = set(paper_ids) if paper_ids else None
        scores: list[tuple[str, float]] = []

        for cid, doc_tokens in self._docs.items():
            meta = self._metas.get(cid, {})
            pid = meta.get("paper_id")
            if allowed is not None and pid not in allowed:
                continue

            dl = len(doc_tokens)
            if dl == 0:
                continue
            tf = Counter(doc_tokens)
            score = 0.0
            for term in q_terms:
                if term not in tf:
                    continue
                df = self._df.get(term, 0)
                if df == 0:
                    continue
                idf = math.log(1 + (self._n_docs - df + 0.5) / (df + 0.5))
                freq = tf[term]
                denom = freq + self.k1 * (1 - self.b + self.b * dl / max(self._avgdl, 1e-9))
                score += idf * (freq * (self.k1 + 1)) / denom

            if score > 0:
                scores.append((cid, score))

        scores.sort(key=lambda x: (-x[1], x[0]))
        out: list[dict[str, Any]] = []
        for cid, score in scores[:top_k]:
            meta = dict(self._metas.get(cid, {}))
            out.append({
                **meta,
                "chunk_id": cid,
                "snippet": self._raw.get(cid, ""),
                "score": score,
                "source": "bm25",
            })
        return out
