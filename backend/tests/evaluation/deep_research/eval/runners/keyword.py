"""Keyword baseline — rank corpus by title keyword overlap."""

from __future__ import annotations

import re
from typing import Any

from .base import RunnerResult

_STOP = {
    "a", "an", "the", "for", "with", "from", "and", "or", "of", "in", "on", "to", "by",
    "is", "are", "was", "were", "be", "been", "being", "at", "as", "into", "through",
}


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if len(w) > 1 and w not in _STOP}


def _score_title(query: str, candidate: str) -> float:
    q = _tokenize(query)
    c = _tokenize(candidate)
    if not q or not c:
        return 0.0
    return len(q & c) / len(q)


class KeywordRunner:
    name = "keyword"

    def run_seed(
        self,
        seed: dict[str, Any],
        corpus: list[dict[str, Any]],
        *,
        top_k: int = 10,
        **_: Any,
    ) -> RunnerResult:
        title = seed.get("title") or ""
        scored: list[tuple[float, str]] = []
        for paper in corpus:
            pid = paper.get("ext_id") or paper.get("id")
            if not pid:
                continue
            score = _score_title(title, paper.get("title") or "")
            if score > 0:
                scored.append((score, str(pid)))
        scored.sort(key=lambda x: (-x[0], x[1]))
        ranked = [pid for _, pid in scored[:top_k]]
        return RunnerResult(
            seed_id=str(seed.get("seed_id") or ""),
            ranked_ids=ranked,
            metadata={"strategy": "title_keyword_overlap"},
        )
