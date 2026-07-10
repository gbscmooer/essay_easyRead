"""Mock of current expand stage: pdf_url filter + citation_count sort."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import RunnerResult

_PKG_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_FIXTURE = _PKG_ROOT / "fixtures" / "mock" / "expand_references.json"


class CurrentExpandRunner:
    """Offline replay of ``stage_expand`` ranking logic (no PDF download)."""

    name = "current_expand"

    def __init__(self, fixture_path: Path | None = None):
        self.fixture_path = fixture_path or _DEFAULT_FIXTURE
        self._cache: dict[str, list[dict[str, Any]]] | None = None

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        if self._cache is None:
            with open(self.fixture_path, encoding="utf-8") as f:
                self._cache = json.load(f)
        return self._cache

    @staticmethod
    def rank_references(refs: list[dict[str, Any]], *, top_k: int) -> list[str]:
        """Pure ranking: keep pdf_url, sort by citation_count desc, take top_k."""
        filtered = [r for r in refs if r.get("pdf_url")]
        filtered.sort(key=lambda r: r.get("citation_count") or 0, reverse=True)
        ranked: list[str] = []
        for ref in filtered[:top_k]:
            ext_id = ref.get("ext_id") or ref.get("doi") or ref.get("arxiv_id")
            if ext_id:
                ranked.append(str(ext_id))
        return ranked

    def run_seed(
        self,
        seed: dict[str, Any],
        corpus: list[dict[str, Any]],
        *,
        top_k: int = 5,
        **_: Any,
    ) -> RunnerResult:
        seed_id = str(seed.get("seed_id") or "")
        refs = self._load().get(seed_id, [])
        ranked = self.rank_references(refs, top_k=top_k)
        with_pdf = sum(1 for r in refs if r.get("pdf_url"))
        return RunnerResult(
            seed_id=seed_id,
            ranked_ids=ranked,
            metadata={
                "fixture": str(self.fixture_path),
                "total_refs": len(refs),
                "with_pdf_url": with_pdf,
                "top_k": top_k,
            },
        )
