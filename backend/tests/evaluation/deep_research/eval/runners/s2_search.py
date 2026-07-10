"""Semantic Scholar search baseline — uses recorded mock responses only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import RunnerResult

_PKG_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_FIXTURE = _PKG_ROOT / "fixtures" / "mock" / "s2_search_responses.json"


class S2SearchRunner:
    name = "s2_search"

    def __init__(self, fixture_path: Path | None = None):
        self.fixture_path = fixture_path or _DEFAULT_FIXTURE
        self._cache: dict[str, list[dict[str, Any]]] | None = None

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        if self._cache is None:
            with open(self.fixture_path, encoding="utf-8") as f:
                self._cache = json.load(f)
        return self._cache

    def run_seed(
        self,
        seed: dict[str, Any],
        corpus: list[dict[str, Any]],
        *,
        top_k: int = 10,
        **_: Any,
    ) -> RunnerResult:
        seed_id = str(seed.get("seed_id") or "")
        records = self._load().get(seed_id, [])
        ranked: list[str] = []
        for row in records:
            ext_id = row.get("ext_id") or row.get("paperId")
            if ext_id:
                ranked.append(str(ext_id))
            if len(ranked) >= top_k:
                break
        return RunnerResult(
            seed_id=seed_id,
            ranked_ids=ranked,
            metadata={"fixture": str(self.fixture_path), "mock": True},
        )
