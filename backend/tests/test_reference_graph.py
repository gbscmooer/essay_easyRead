"""PaperReference 存储与 cites 图谱构建测试。"""

import asyncio
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, select

from backend.models.paper import Paper
from backend.models.research import PaperReference
from backend.services.deep_research.graph_builder import build_graph
from backend.services.deep_research.reference_store import (
    ReferenceInput,
    list_by_citing,
    resolve_cited_links,
    upsert_references,
)
from backend.services.paper_identity import PaperWork, apply_identity_to_paper, build_canonical_id


def _make_paper(
    session: Session,
    *,
    paper_id: int | None = None,
    title: str,
    doi: str | None = None,
    arxiv_id: str | None = None,
    parent_paper_id: int | None = None,
    source: str = "uploaded",
    level: int = 1,
    download_status: str = "available",
) -> Paper:
    p = Paper(
        filename=f"{title[:40]}.pdf",
        pdf_path="/tmp/fake.pdf",
        file_size=100,
        title=title,
        doi=doi,
        arxiv_id=arxiv_id,
        source=source,
        level=level,
        parent_paper_id=parent_paper_id,
        status="indexed",
        download_status=download_status,
        canonical_id=build_canonical_id(doi=doi, arxiv_id=arxiv_id, title=title),
        updated_at=datetime.now(timezone.utc),
    )
    if paper_id is not None:
        p.id = paper_id
    apply_identity_to_paper(p, PaperWork(title=title, doi=doi, arxiv_id=arxiv_id))
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


class _EmptySimilarityProvider:
    name = "empty_test"

    async def score_pairs(self, papers, *, ws_id):
        return []


@pytest.fixture(autouse=True)
def _cleanup_papers(session):
    """避免本模块测试数据污染其他集成测试的共享库。"""
    yield
    for ref in session.exec(select(PaperReference)).all():
        session.delete(ref)
    for paper in session.exec(select(Paper)).all():
        session.delete(paper)
    session.commit()


@pytest.fixture(autouse=True)
def _mock_similarity(monkeypatch):
    monkeypatch.setattr(
        "backend.services.deep_research.graph_builder.get_similarity_provider",
        lambda _cfg: _EmptySimilarityProvider(),
    )


def test_upsert_and_resolve_cited_links(session):
    cited = _make_paper(session, title="Shared Target", doi="10.1000/shared.c")
    citing = _make_paper(session, title="Citing Paper A")

    upsert_references(session, citing.id, [
        ReferenceInput(cited_doi="10.1000/shared.c", frequency=2, source="grobid"),
    ])
    rows = list_by_citing(session, citing.id)
    assert len(rows) == 1
    assert rows[0].cited_paper_id is None

    updated = resolve_cited_links(session, citing_paper_id=citing.id)
    assert updated == 1
    rows = list_by_citing(session, citing.id)
    assert rows[0].cited_paper_id == cited.id


def test_title_only_reference_does_not_auto_link(session):
    cited = _make_paper(session, title="Noisy Parsed Target", doi="10.1000/strong")
    citing = _make_paper(session, title="Citing Paper")
    upsert_references(session, citing.id, [
        ReferenceInput(cited_title="Noisy Parsed Target"),
    ])

    assert resolve_cited_links(session, citing_paper_id=citing.id) == 0
    assert list_by_citing(session, citing.id)[0].cited_paper_id is None
    assert cited.id is not None


def test_title_only_reference_deduplicates_normalized_variants(session):
    citing = _make_paper(session, title="Citing Paper")
    upsert_references(session, citing.id, [
        ReferenceInput(cited_title="Graph Retrieval: A Survey", frequency=1),
        ReferenceInput(cited_title=" graph retrieval a survey ", frequency=3),
    ])

    rows = list_by_citing(session, citing.id)
    assert len(rows) == 1
    assert rows[0].frequency == 3


def test_a_and_b_both_cite_c(session):
    paper_a = _make_paper(session, title="Paper A")
    paper_b = _make_paper(session, title="Paper B")
    paper_c = _make_paper(session, title="Paper C", arxiv_id="2301.00001")

    upsert_references(session, paper_a.id, [ReferenceInput(cited_paper_id=paper_c.id, frequency=1)])
    upsert_references(session, paper_b.id, [ReferenceInput(cited_paper_id=paper_c.id, frequency=3)])

    refs = [
        *list_by_citing(session, paper_a.id),
        *list_by_citing(session, paper_b.id),
    ]
    papers = [paper_a, paper_b, paper_c]
    graph = asyncio.run(build_graph(
        1, papers,
        similarity_threshold=0.78,
        enable_shared_method=False,
        references=refs,
    ))

    cites = [e for e in graph["edges"] if e["kind"] == "cites"]
    assert len(cites) == 2
    pairs = {(e["source"], e["target"]) for e in cites}
    assert pairs == {
        (f"paper:{paper_a.id}", f"paper:{paper_c.id}"),
        (f"paper:{paper_b.id}", f"paper:{paper_c.id}"),
    }


def test_context_attached_to_correct_cites_edge(session):
    paper_a = _make_paper(session, title="Seeder A")
    paper_b = _make_paper(session, title="Seeder B")
    paper_c = _make_paper(session, title="Target C", doi="10.1000/target.c")

    ctx_a = [{
        "section": "Introduction",
        "context": "We build upon prior work [1].",
        "intent": "background",
    }]
    ctx_b = [{
        "section": "Experiments",
        "context": "We compare against Target C [2].",
        "intent": "baseline",
    }]

    upsert_references(session, paper_a.id, [
        ReferenceInput(cited_paper_id=paper_c.id, contexts=ctx_a, frequency=1),
    ])
    upsert_references(session, paper_b.id, [
        ReferenceInput(cited_paper_id=paper_c.id, contexts=ctx_b, frequency=2),
    ])

    refs = [
        *list_by_citing(session, paper_a.id),
        *list_by_citing(session, paper_b.id),
    ]
    graph = asyncio.run(build_graph(
        1, [paper_a, paper_b, paper_c],
        similarity_threshold=0.78,
        enable_shared_method=False,
        references=refs,
    ))

    cites = {e["source"]: e for e in graph["edges"] if e["kind"] == "cites"}
    edge_a = cites[f"paper:{paper_a.id}"]
    edge_b = cites[f"paper:{paper_b.id}"]

    assert edge_a["target"] == f"paper:{paper_c.id}"
    assert edge_a["contexts"][0]["section"] == "Introduction"
    assert edge_a["contexts"][0]["intent"] == "background"

    assert edge_b["target"] == f"paper:{paper_c.id}"
    assert edge_b["contexts"][0]["section"] == "Experiments"
    assert edge_b["contexts"][0]["intent"] == "baseline"


def test_parent_tree_differs_from_cites_graph(session):
    """C 的 parent_paper_id 指向 A，但 B 也引用 C → cites 图与发现树不同。"""
    paper_a = _make_paper(session, title="Seed A", level=1)
    paper_b = _make_paper(session, title="Seed B", level=1)
    # 发现谱系：C 由 A 扩展下载
    paper_c = _make_paper(
        session,
        title="Expanded C",
        arxiv_id="2301.00002",
        parent_paper_id=paper_a.id,
        source="expanded",
        level=2,
    )

    upsert_references(session, paper_a.id, [ReferenceInput(cited_paper_id=paper_c.id)])
    upsert_references(session, paper_b.id, [ReferenceInput(cited_paper_id=paper_c.id)])

    refs = [
        *list_by_citing(session, paper_a.id),
        *list_by_citing(session, paper_b.id),
    ]
    graph = asyncio.run(build_graph(
        1, [paper_a, paper_b, paper_c],
        similarity_threshold=0.78,
        enable_shared_method=False,
        references=refs,
    ))

    cites = [e for e in graph["edges"] if e["kind"] == "cites"]
    cite_sources = {e["source"] for e in cites}
    assert cite_sources == {f"paper:{paper_a.id}", f"paper:{paper_b.id}"}

    # parent_paper_id 树只有 A→C，不应再产生 cites 边（已弃用 parent 推断）
    parent_only_edges = [
        e for e in cites
        if e["source"] == f"paper:{paper_c.parent_paper_id}"
        and e["target"] == f"paper:{paper_c.id}"
        and e["source"] not in cite_sources
    ]
    assert parent_only_edges == []
    assert len(cites) == 2


def test_metadata_only_paper_can_be_cited_graph_node(session):
    citing = _make_paper(session, title="Available Citer")
    metadata_only = _make_paper(
        session,
        title="Metadata Only Target",
        doi="10.1000/metadata",
        download_status="unavailable",
    )
    upsert_references(session, citing.id, [
        ReferenceInput(cited_paper_id=metadata_only.id, cited_doi=metadata_only.doi),
    ])

    graph = asyncio.run(build_graph(
        1,
        [citing, metadata_only],
        similarity_threshold=0.78,
        enable_shared_method=False,
        references=list_by_citing(session, citing.id),
    ))

    node_ids = {node["id"] for node in graph["nodes"]}
    cites = [edge for edge in graph["edges"] if edge["kind"] == "cites"]
    assert f"paper:{metadata_only.id}" in node_ids
    assert [(edge["source"], edge["target"]) for edge in cites] == [
        (f"paper:{citing.id}", f"paper:{metadata_only.id}")
    ]


def test_unresolved_cited_skipped_in_graph(session):
    """未入库且无 cited_paper_id 的引用不建边（占位策略：跳过）。"""
    paper_a = _make_paper(session, title="Only Citer")
    upsert_references(session, paper_a.id, [
        ReferenceInput(cited_doi="10.9999/not.in.db", cited_title="Ghost Paper"),
    ])

    refs = list_by_citing(session, paper_a.id)
    graph = asyncio.run(build_graph(
        1, [paper_a],
        similarity_threshold=0.78,
        enable_shared_method=False,
        references=refs,
    ))
    cites = [e for e in graph["edges"] if e["kind"] == "cites"]
    assert cites == []


def test_build_graph_loads_references_from_session(session):
    paper_a = _make_paper(session, title="Loader A")
    paper_c = _make_paper(session, title="Loader C", doi="10.1000/loader.c")
    upsert_references(session, paper_a.id, [ReferenceInput(cited_doi="10.1000/loader.c")])
    resolve_cited_links(session, citing_paper_id=paper_a.id)

    graph = asyncio.run(build_graph(
        1, [paper_a, paper_c],
        similarity_threshold=0.78,
        enable_shared_method=False,
        session=session,
    ))
    cites = [e for e in graph["edges"] if e["kind"] == "cites"]
    assert len(cites) == 1
    assert cites[0]["source"] == f"paper:{paper_a.id}"
    assert cites[0]["target"] == f"paper:{paper_c.id}"
