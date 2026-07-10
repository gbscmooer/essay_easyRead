"""paper_identity 匹配与登记集成测试。"""

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from backend.models.paper import Paper
from backend.models.workspace import Workspace, WorkspacePaper
from backend.services import paper_identity
from backend.services.paper_identity import (
    MatchResult,
    PaperWork,
    find_or_register,
    match_local,
    register_stub,
    weak_fuzzy_match,
)


def _make_paper(
    session: Session,
    *,
    title: str,
    authors: str | None = None,
    year: int | None = None,
    doi: str | None = None,
    arxiv_id: str | None = None,
    pdf_path: str = "/tmp/fake.pdf",
    download_status: str = "available",
) -> Paper:
    from backend.services.paper_identity import apply_identity_to_paper, build_canonical_id

    p = Paper(
        filename=f"{title[:40]}.pdf",
        pdf_path=pdf_path,
        file_size=100,
        title=title,
        authors=authors,
        year=year,
        doi=doi,
        arxiv_id=arxiv_id,
        download_status=download_status,
        canonical_id=build_canonical_id(doi=doi, arxiv_id=arxiv_id, title=title),
        updated_at=datetime.now(timezone.utc),
    )
    apply_identity_to_paper(p, PaperWork(title=title, authors=authors, year=year, doi=doi, arxiv_id=arxiv_id))
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def test_same_doi_does_not_create_second_paper(session):
    _make_paper(session, title="Paper A", doi="10.1234/abc", authors="Alice Smith", year=2020)

    work = PaperWork(title="Paper A duplicate", doi="10.1234/abc", authors="Alice Smith", year=2020)
    paper, is_new = find_or_register(session, work)
    assert is_new is False

    count = len(session.exec(select(Paper)).all())
    assert count == 1
    assert paper.doi == "10.1234/abc"


def test_same_arxiv_does_not_create_second_paper(session):
    _make_paper(session, title="Transformer", arxiv_id="1706.03762", authors="Vaswani", year=2017)

    work = PaperWork(title="Attention paper", arxiv_id="1706.03762", authors="Vaswani", year=2017)
    paper, is_new = find_or_register(session, work)
    assert is_new is False
    assert paper.arxiv_id == "1706.03762"
    dup_count = len(session.exec(select(Paper).where(Paper.arxiv_id == "1706.03762")).all())
    assert dup_count == 1


def test_similar_title_author_year_conflict_rejected(session):
    _make_paper(
        session,
        title="Graph Neural Networks for Node Classification",
        authors="Alice Wang",
        year=2020,
    )

    work = PaperWork(
        title="Graph Neural Networks for Node Classification!",
        authors="Bob Chen",
        year=2020,
    )
    result = match_local(session, work)
    assert result.matched is False

    work2 = PaperWork(
        title="Graph Neural Networks for Node Classification!",
        authors="Alice Wang",
        year=2018,
    )
    result2 = match_local(session, work2)
    assert result2.matched is False

    # 三者均满足才弱匹配
    work3 = PaperWork(
        title="Graph Neural Networks for Node Classification!",
        authors="Alice Wang",
        year=2021,
    )
    result3 = match_local(session, work3)
    assert result3.matched is True
    assert result3.method == "fuzzy"


def test_weak_fuzzy_rejects_title_only(session):
    existing = _make_paper(
        session,
        title="Deep Learning for Vision",
        authors="Alice",
        year=2020,
    )
    work = PaperWork(title="Deep Learning for Vision", authors="Bob", year=2020)
    ok, _ = weak_fuzzy_match(work, existing)
    assert ok is False


def test_register_stub_links_workspace(session):
    ws = Workspace(name="test-ws")
    session.add(ws)
    session.commit()
    session.refresh(ws)

    work = PaperWork(title="Stub Paper", doi="10.9999/stub")
    paper, is_new = find_or_register(session, work, workspace_id=ws.id)
    assert is_new is True
    assert paper.download_status == "unavailable"
    assert paper.pdf_path == ""

    wp = session.exec(
        select(WorkspacePaper)
        .where(WorkspacePaper.workspace_id == ws.id)
        .where(WorkspacePaper.paper_id == paper.id)
    ).first()
    assert wp is not None


def test_register_stub_sets_identity_fields(session):
    work = PaperWork(
        title="Identity Test",
        doi="10.5555/identity",
        arxiv_id="2301.00001",
        authors="Carol Lee",
        year=2023,
    )
    paper = register_stub(session, work)
    assert paper.canonical_id == "doi:10.5555/identity"
    assert paper.doi == "10.5555/identity"
    assert paper.title_normalized == "identity test"


def test_register_or_link_recovers_from_concurrent_canonical_insert(session, monkeypatch):
    existing = _make_paper(
        session,
        title="Concurrent Winner",
        doi="10.5555/race",
        authors="Alice",
        year=2024,
    )
    real_match = paper_identity.match_local
    calls = 0

    def miss_then_match(current_session, work):
        nonlocal calls
        calls += 1
        if calls == 1:
            return MatchResult(None, False)
        return real_match(current_session, work)

    def lose_unique_race(_session, _work):
        raise IntegrityError("INSERT INTO papers", {}, RuntimeError("unique"))

    monkeypatch.setattr(paper_identity, "match_local", miss_then_match)
    monkeypatch.setattr(paper_identity, "register_stub", lose_unique_race)

    paper, is_new, action = paper_identity.register_or_link(
        session,
        PaperWork(title="Concurrent Duplicate", doi="10.5555/race"),
    )

    assert paper.id == existing.id
    assert is_new is False
    assert action == "matched"
