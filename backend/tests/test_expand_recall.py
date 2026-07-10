"""expand 多路召回集成测试 — mock provider，零网络。"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session

from backend.models.paper import Paper
from backend.models.recall import RecallCandidate, RecallSource, build_recall_candidate
from backend.models.workspace import Workspace, WorkspacePaper
from backend.services.deep_research.fusion_ranker import (
    CHANNEL_BACKWARD,
    CHANNEL_FORWARD,
    fuse_candidates,
    tag_channel,
)
from backend.services.deep_research.recall_orchestrator import recall_for_seed
from backend.services.deep_research.stages.expand import _rerank_fused_papers, stage_expand
from backend.services.paper_identity import build_canonical_id


@dataclass
class _FakeProvider:
    name: str = "openalex"

    async def find_paper_id(self, title: str, doi: str = "", arxiv_id: str = "") -> str:
        return "W-SEED"

    async def get_paper_references(self, paper_id: str, limit: int = 50) -> list[dict]:
        raise RuntimeError("backward down")

    async def get_paper_citations(self, paper_id: str, limit: int = 50) -> list[RecallCandidate]:
        c = build_recall_candidate(
            title="Forward Hit",
            doi="10.1234/forward.only",
            source=RecallSource.FORWARD_CIT,
            source_rank=0,
        )
        assert c is not None
        return [tag_channel(c, CHANNEL_FORWARD)]

    async def get_recommendations(self, paper_id: str, limit: int = 50) -> list[RecallCandidate]:
        return []

    async def search_works(self, query: str, limit: int = 20) -> list[RecallCandidate]:
        return []


@pytest.mark.asyncio
async def test_recall_single_source_failure_returns_others():
    provider = _FakeProvider()
    seed = Paper(id=1, filename="s.pdf", pdf_path="/x.pdf", file_size=0, title="Seed")
    config = {"recall": {"enabled_sources": ["backward", "forward", "recommend"]}}

    result = await recall_for_seed(provider, seed, config)
    assert result.pools.get(CHANNEL_FORWARD)
    assert len(result.pools[CHANNEL_FORWARD]) == 1
    assert any(s.channel == CHANNEL_BACKWARD and s.failed for s in result.stats)
    assert any(s.channel == CHANNEL_FORWARD and not s.failed for s in result.stats)


@pytest.mark.asyncio
async def test_stage_expand_uses_fusion_not_citation_sort(engine, monkeypatch):
    """forward 源命中、backward 故意失败时，expand 仍应登记 forward 论文。"""
    ws = Workspace(name="t", description="")
    with Session(engine) as s:
        s.add(ws)
        s.commit()
        s.refresh(ws)
        seed = Paper(
            filename="seed.pdf", pdf_path="/seed.pdf", file_size=0,
            title="Seed Paper", status="done", level=1,
        )
        s.add(seed)
        s.commit()
        s.refresh(seed)
        s.add(WorkspacePaper(workspace_id=ws.id, paper_id=seed.id))
        s.commit()
        seed_id = seed.id
        ws_id = ws.id

    events: list[dict] = []

    async def on_event(event, **data):
        events.append({"event": event, **data})

    import backend.services.deep_research.stages.expand as expand_mod
    expand_mod._engine = engine

    with Session(engine) as s:
        seed = s.get(Paper, seed_id)

    fake = _FakeProvider()

    with patch("backend.services.deep_research.stages.expand.get_provider", return_value=fake), patch(
        "backend.services.deep_research.stages.expand.download_for_paper",
        new_callable=AsyncMock,
        return_value=None,
    ):
        expanded, stats = await stage_expand(
            ws_id,
            [seed],
            {"expand_top_k": 5, "expand_total_limit": 10, "discovery_v2": True},
            on_event,
            next_level=2,
        )

    assert len(expanded) == 1
    assert expanded[0].doi == "10.1234/forward.only"
    assert expanded[0].canonical_id == build_canonical_id(doi="10.1234/forward.only")
    assert expanded[0].download_status == "unavailable"
    assert any(s["channel"] == CHANNEL_BACKWARD and s["failed"] >= 1 for s in stats)
    assert any(e.get("channels") for e in events if e["event"] == "paper_added")
    added = next(e for e in events if e["event"] == "paper_added")
    assert added["channel_ranks"] == {CHANNEL_FORWARD: 1}
    assert added["fusion_contributions"][CHANNEL_FORWARD] > 0


@pytest.mark.asyncio
async def test_stage_expand_legacy_when_discovery_v2_disabled(engine, monkeypatch):
    """discovery_v2=false 时走 backward + citation_count 单源路径。"""
    ws = Workspace(name="legacy", description="")
    with Session(engine) as s:
        s.add(ws)
        s.commit()
        s.refresh(ws)
        seed = Paper(
            filename="seed.pdf", pdf_path="/seed.pdf", file_size=0,
            title="Seed Paper", status="done", level=1,
        )
        s.add(seed)
        s.commit()
        s.refresh(seed)
        s.add(WorkspacePaper(workspace_id=ws.id, paper_id=seed.id))
        s.commit()
        seed_id = seed.id
        ws_id = ws.id

    events: list[dict] = []

    async def on_event(event, **data):
        events.append({"event": event, **data})

    import backend.services.deep_research.stages.expand as expand_mod
    expand_mod._engine = engine

    with Session(engine) as s:
        seed = s.get(Paper, seed_id)

    refs = [
        {"ext_id": "low", "title": "Low Cit", "citation_count": 10, "pdf_url": "http://x"},
        {"ext_id": "high", "title": "High Cit", "citation_count": 100, "pdf_url": "http://y"},
        {"ext_id": "nopdf", "title": "No PDF", "citation_count": 999, "pdf_url": None},
    ]

    class _LegacyProvider:
        name = "openalex"

        async def find_paper_id(self, title: str, doi: str = "", arxiv_id: str = "") -> str:
            return "W-SEED"

        async def get_paper_references(self, paper_id: str, limit: int = 50) -> list[dict]:
            return refs

    with patch("backend.services.deep_research.stages.expand.get_provider", return_value=_LegacyProvider()), patch(
        "backend.services.deep_research.stages.expand.download_for_paper",
        new_callable=AsyncMock,
        return_value=None,
    ):
        expanded, stats = await stage_expand(
            ws_id,
            [seed],
            {"expand_top_k": 2, "expand_total_limit": 10, "discovery_v2": False},
            on_event,
            next_level=2,
        )

    assert len(expanded) == 2
    titles = [p.title for p in expanded]
    assert titles[0] == "High Cit"
    assert titles[1] == "Low Cit"
    assert any(e.get("legacy") for e in events if e["event"] == "paper_added")
    assert stats and stats[0]["channel"] == "backward"


@pytest.mark.asyncio
async def test_expand_paper_level_reranker_reorders_rrf_top_candidates(monkeypatch):
    first = build_recall_candidate(
        title="Lexically First",
        abstract="not as relevant",
        doi="10.1/first",
        source=RecallSource.FORWARD_CIT,
        source_rank=1,
    )
    second = build_recall_candidate(
        title="Semantically Best",
        abstract="matches the seed problem",
        doi="10.1/second",
        source=RecallSource.FORWARD_CIT,
        source_rank=2,
    )
    assert first is not None and second is not None
    fused = fuse_candidates({CHANNEL_FORWARD: [first, second]})

    async def fake_rerank(_query, documents, top_n=None):
        assert "matches the seed problem" in documents[1]
        return [
            {"index": 1, "score": 0.9, "document": documents[1]},
            {"index": 0, "score": 0.2, "document": documents[0]},
        ]

    monkeypatch.setattr(
        "backend.services.deep_research.stages.expand.get_reranker_config",
        lambda: {"enabled": True},
    )
    monkeypatch.setattr(
        "backend.services.deep_research.stages.expand.rerank",
        fake_rerank,
    )
    seed = Paper(filename="seed.pdf", pdf_path="/seed.pdf", file_size=0, title="Seed Problem")
    reranked = await _rerank_fused_papers(fused, [seed], {})
    assert [item.representative.title for item in reranked] == [
        "Semantically Best",
        "Lexically First",
    ]
    assert reranked[0].rerank_score == pytest.approx(0.9)
