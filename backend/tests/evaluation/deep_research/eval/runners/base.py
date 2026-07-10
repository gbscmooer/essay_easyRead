"""Runner protocol and shared types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class RunnerResult:
    seed_id: str
    ranked_ids: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)


class DiscoveryRunner(Protocol):
    name: str

    def run_seed(self, seed: dict[str, Any], corpus: list[dict[str, Any]], **kwargs: Any) -> RunnerResult:
        ...
