"""Regression tests for library lifecycle, partial analysis and source-bound standalone QA."""

import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select, create_engine

from backend.models.paper import Paper
from backend.models.workspace import Workspace, WorkspacePaper
from backend.models.analysis import AnalysisResult
from backend.models.note import Note
from backend.models.annotation import Annotation
from backend.models.research import ResearchRun, ResearchGraph, PaperReference
from backend.services import database
from backend.services.material_lifecycle import MaterialBusyError


@pytest.fixture
def material_env(engine, monkeypatch, tmp_path):
    from backend.routers import workspaces, research, analysis, papers
    from backend.services import analysis_runner, material_sources, agent_tools
    from backend.services.deep_research import vector_store
    import chromadb

    monkeypatch.setattr(database, "get_engine", lambda: engine)
    for module in (
        workspaces,
        research,
        analysis,
        papers,
        analysis_runner,
        material_sources,
        agent_tools,
    ):
        monkeypatch.setattr(module, "get_engine", lambda: engine)
    monkeypatch.setattr(
        vector_store,
        "_client",
        chromadb.PersistentClient(path=str(tmp_path / "chroma")),
    )
    monkeypatch.setattr(analysis_runner, "_jobs", {})
    monkeypatch.setattr(
        analysis_runner, "_cache_path", lambda pid: str(tmp_path / f"cache-{pid}.json")
    )
    with Session(engine) as session:
        p = Paper(
            filename="original.pdf",
            pdf_path="",
            file_size=1,
            title="Original title",
            authors="Known author",
            venue="Known venue",
            year=2024,
        )
        other = Paper(filename="other.pdf", pdf_path="", file_size=1)
        ws = Workspace(name="Current material")
        session.add_all([p, other, ws])
        session.commit()
        for row in (p, other, ws):
            session.refresh(row)
        session.add_all(
            [
                WorkspacePaper(workspace_id=ws.id, paper_id=p.id),
                WorkspacePaper(workspace_id=ws.id, paper_id=other.id),
            ]
        )
        session.commit()
        return p.id, other.id, ws.id


def index_one(ws_id, pid):
    from backend.services.deep_research.vector_store import upsert_chunks

    upsert_chunks(
        ws_id,
        [f"{pid}:body:0"],
        ["original evidence"],
        [[1.0, 0.0, 0.0]],
        [
            {
                "paper_id": pid,
                "paper_title": "Original",
                "section_name": "Body",
                "chunk_index": 0,
                "page_hint": "",
            }
        ],
    )


def test_delete_cleans_children_vectors_graph_cache_and_never_reuses_id(
    material_env, engine
):
    from backend.services.deep_research.vector_store import list_workspace_chunks
    from backend.services.analysis_runner import save_cache, load_cache

    pid, other, wsid = material_env
    index_one(wsid, pid)
    save_cache(pid, {"one_sentence": "old cached data"})
    with Session(engine) as session:
        run = ResearchRun(workspace_id=wsid, mode="local", status="done")
        session.add(run)
        session.commit()
        session.refresh(run)
        session.add_all(
            [
                Note(paper_id=pid, content="private note"),
                Annotation(paper_id=pid),
                AnalysisResult(
                    paper_id=pid,
                    title="Old",
                    venue="Known",
                    year=2024,
                    one_sentence="Summary",
                    problem="Question",
                    limitations="Limited",
                ),
                ResearchGraph(
                    workspace_id=wsid, run_id=run.id, nodes=[{"id": f"paper:{pid}"}]
                ),
                PaperReference(
                    citing_paper_id=other, cited_paper_id=pid, ref_title="Old"
                ),
                PaperReference(citing_paper_id=pid, ref_title="Outgoing"),
            ]
        )
        session.commit()
        database.delete_paper(session, pid)
    with Session(engine) as session:
        for model in (Note, Annotation, AnalysisResult, WorkspacePaper):
            assert not session.exec(select(model).where(model.paper_id == pid)).all()
        assert not session.exec(
            select(ResearchGraph).where(ResearchGraph.workspace_id == wsid)
        ).all()
        assert session.exec(select(PaperReference)).one().cited_paper_id is None
        # Delete the highest ID as well: plain SQLite rowid would reuse it.
        database.delete_paper(session, other)
        new = Paper(filename="new.pdf", pdf_path="", file_size=1)
        session.add(new)
        session.commit()
        session.refresh(new)
        assert new.id > other
    assert list_workspace_chunks(wsid) == []
    assert load_cache(pid) == {}


@pytest.mark.asyncio
async def test_remove_workspace_material_removes_real_retrieval_and_invalidates_graph(
    material_env, engine
):
    from backend.routers.workspaces import remove_paper_from_workspace
    from backend.services.deep_research.vector_store import list_workspace_chunks
    from backend.services.deep_research.hybrid_retriever import HybridRetriever

    pid, _, wsid = material_env
    index_one(wsid, pid)
    with Session(engine) as session:
        session.add(
            ResearchGraph(workspace_id=wsid, run_id=999, nodes=[{"id": f"paper:{pid}"}])
        )
        session.commit()
    await remove_paper_from_workspace(wsid, pid)
    assert list_workspace_chunks(wsid) == []
    assert not (
        await HybridRetriever(wsid).retrieve(
            "evidence", [1.0, 0.0, 0.0], use_rerank=False
        )
    ).hits
    with Session(engine) as session:
        assert session.get(Paper, pid) is not None
        assert not session.exec(select(ResearchGraph)).all()


@pytest.mark.parametrize("job_kind", ["graph", "analysis", "backfill", "exploration"])
def test_busy_delete_preserves_all_material(
    material_env, engine, monkeypatch, job_kind
):
    from backend.services import analysis_runner
    from backend.routers import research
    from backend.models.exploration import ExplorationRun

    pid, _, wsid = material_env
    if job_kind == "analysis":
        monkeypatch.setitem(
            analysis_runner._jobs, pid, analysis_runner.AnalysisJob(pid)
        )
    elif job_kind == "backfill":
        from backend.services.material_activity import backfill_jobs

        monkeypatch.setitem(backfill_jobs, wsid, {"status": "running"})
    with Session(engine) as session:
        if job_kind == "graph":
            session.add(ResearchRun(workspace_id=wsid, mode="local", status="pending"))
            session.commit()
        if job_kind == "exploration":
            session.add(
                ExplorationRun(
                    question="Preserve source",
                    status="running",
                    options={"workspace_id": wsid},
                )
            )
            session.commit()
        with pytest.raises(MaterialBusyError):
            database.delete_paper(session, pid)
        assert session.get(Paper, pid) is not None
        assert session.exec(
            select(WorkspacePaper).where(WorkspacePaper.paper_id == pid)
        ).first()


def test_legacy_migration_archives_orphans_reserves_ids_and_keeps_report(tmp_path):
    from backend.services.storage_integrity import migrate_material_integrity

    engine = create_engine("sqlite:///" + str(tmp_path / "legacy.db"))
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE papers (\n id INTEGER NOT NULL, parent_paper_id INTEGER, title TEXT, PRIMARY KEY (id))"
        )
        conn.exec_driver_sql(
            "CREATE TABLE workspaces (\n id INTEGER NOT NULL, name TEXT, PRIMARY KEY (id))"
        )
        conn.exec_driver_sql("CREATE UNIQUE INDEX uq_test_title ON papers(title)")
        conn.exec_driver_sql(
            "CREATE TABLE notes(id INTEGER PRIMARY KEY, paper_id INTEGER, content TEXT)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE workspace_papers(id INTEGER PRIMARY KEY, paper_id INTEGER, workspace_id INTEGER)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE paper_references(id INTEGER PRIMARY KEY, citing_paper_id INTEGER, cited_paper_id INTEGER)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE exploration_runs(id INTEGER PRIMARY KEY, result TEXT)"
        )
        conn.exec_driver_sql("INSERT INTO papers VALUES(1,NULL,'kept')")
        conn.exec_driver_sql("INSERT INTO workspaces VALUES(1,'kept')")
        conn.exec_driver_sql("INSERT INTO notes VALUES(1,9,'recoverable orphan')")
        conn.exec_driver_sql("INSERT INTO workspace_papers VALUES(1,9,15)")
        conn.exec_driver_sql(
            "INSERT INTO exploration_runs VALUES(1,'immutable report')"
        )
    migrate_material_integrity(engine)
    migrate_material_integrity(engine)
    with engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM notes").scalar() == 0
        assert (
            conn.exec_driver_sql(
                "SELECT COUNT(*) FROM material_orphan_archive"
            ).scalar()
            == 2
        )
        assert (
            conn.exec_driver_sql("SELECT result FROM exploration_runs").scalar()
            == "immutable report"
        )
        conn.exec_driver_sql("INSERT INTO papers(title) VALUES('new')")
        conn.exec_driver_sql("INSERT INTO workspaces(name) VALUES('new')")
        assert conn.exec_driver_sql("SELECT MAX(id) FROM papers").scalar() == 10
        assert conn.exec_driver_sql("SELECT MAX(id) FROM workspaces").scalar() == 16
        assert conn.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE name='uq_test_title'"
        ).first()
    engine.dispose()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("method", []),
        (
            "method",
            {"overview": "Method", "pipeline": "Steps", "mermaid_chart": {"bad": True}},
        ),
        ("method", {"overview": "x", "pipeline": 1}),
        ("outline", [{"section": "x", "summary": 9}]),
        ("key_contributions", []),
        ("year", True),
        ("problem", ""),
        ("experiments", "bad"),
    ],
)
def test_invalid_analysis_fields_are_missing(field, value):
    from backend.services.analysis_integrity import valid_field

    assert not valid_field(field, value)


@pytest.mark.asyncio
async def test_truncated_analysis_is_partial_keeps_metadata_cache_and_can_complete(
    material_env, engine, monkeypatch
):
    from backend.services import (
        analysis_runner,
        ai_analyzer,
        ai_config,
        pdf_parser,
        tagging,
    )
    from backend.models.schemas import StreamEvent

    pid, _, _ = material_env
    monkeypatch.setattr(
        ai_config,
        "get_analysis_config",
        lambda: {
            "api_key": "stub",
            "base_url": "https://unused.invalid",
            "model": "stub",
        },
    )
    monkeypatch.setattr(ai_config, "get_active_analysis_prompt", lambda: "offline")
    monkeypatch.setattr(ai_config, "get_findpapers_config", lambda: {"enabled": False})
    monkeypatch.setattr(
        pdf_parser,
        "extract_pdf_content",
        lambda _: SimpleNamespace(full_text="source", truncated=True),
    )

    async def partial(*args, **kwargs):
        yield StreamEvent(
            event="field", field_name="title", data={"value": "Candidate title"}
        )
        yield StreamEvent(
            event="field",
            field_name="one_sentence",
            data={"value": "An actual available field"},
        )
        yield StreamEvent(
            event="done", data={}
        )  # Even a wrong upstream terminal must not mean complete.

    monkeypatch.setattr(ai_analyzer, "analyze_paper_stream", partial)
    job = analysis_runner.AnalysisJob(pid)
    await analysis_runner._run_analysis(job, "unused")
    assert job.done and job.status == "partial"
    assert "done" not in [event.event for event in job.events]
    assert analysis_runner.load_cache(pid) == {
        "one_sentence": "An actual available field",
        "title": "Candidate title",
    }
    with Session(engine) as session:
        paper = session.get(Paper, pid)
        assert (paper.title, paper.authors, paper.venue, paper.year) == (
            "Original title",
            "Known author",
            "Known venue",
            2024,
        )
        result = session.exec(
            select(AnalysisResult).where(AnalysisResult.paper_id == pid)
        ).one()
        assert result.completeness["status"] == "partial"
        assert result.completeness["input_truncated"] is True
        assert "problem" in result.completeness["missing_fields"]
    full = {
        "title": "Complete",
        "one_sentence": "Summary",
        "problem": "Question",
        "key_contributions": ["Contribution"],
        "outline": [{"section": "Intro", "summary": "Text", "page": "1"}],
        "method": {"overview": "Method", "pipeline": "Steps", "key_modules": []},
        "experiments": {"main_results": "No experiments", "ablation": "Not applicable"},
        "code_mapping": [],
        "interview_qa": [],
        "limitations": "Limited",
    }

    async def complete(*args, **kwargs):
        for key, value in full.items():
            yield StreamEvent(event="field", field_name=key, data={"value": value})

    async def no_tags(*args, **kwargs):
        return []

    async def no_related(*args, **kwargs):
        return []

    monkeypatch.setattr(ai_analyzer, "analyze_paper_stream", complete)
    monkeypatch.setattr(tagging, "apply_auto_tags_to_paper", no_tags)
    monkeypatch.setattr(
        "backend.services.semantic_scholar.search_related_papers", no_related
    )
    job = analysis_runner.AnalysisJob(pid)
    await analysis_runner._run_analysis(job, "unused")
    assert job.status == "done"
    assert analysis_runner.load_cache(pid) == {}
    with Session(engine) as session:
        assert session.get(Paper, pid).status == "done"


@pytest.fixture
def pdf_material(material_env, engine, tmp_path):
    import fitz

    pid, _, wsid = material_env
    path = tmp_path / "pages.pdf"
    with fitz.open() as doc:
        for text in [
            "First page original evidence.",
            "Second page contains unique method evidence.",
        ]:
            doc.new_page().insert_text((50, 50), text)
        doc.save(path)
    with Session(engine) as session:
        paper = session.get(Paper, pid)
        paper.pdf_path = str(path)
        paper.download_status = "available"
        session.add(paper)
        session.commit()
    return pid, wsid, path


@pytest.mark.asyncio
async def test_sources_reject_derived_changed_missing_and_bind_page_exactly(
    pdf_material,
):
    from backend.services.material_sources import verified_pdf_hits, file_digest

    pid, _, path = pdf_material
    hit = {
        "paper_id": pid,
        "paper_title": "Title",
        "section_name": "Body",
        "snippet": "unique method evidence.",
        "score": 0.1,
    }
    original = (await verified_pdf_hits([hit]))[0]
    assert original["page_number"] == 2
    assert original["end_char"] - original["start_char"] == len(hit["snippet"])
    assert original["document_hash"] == file_digest(path)
    for changed in (
        {"source_kind": "analysis_summary"},
        {"source_kind": "citation_context"},
        {"document_hash": "stale"},
        {"snippet": "fabricated summary"},
    ):
        assert await verified_pdf_hits([{**hit, **changed}]) == []
    path.unlink()
    assert await verified_pdf_hits([hit]) == []


def test_page_index_uses_all_pages_and_original_offsets_without_analysis_truncation():
    from backend.services.material_sources import page_chunks

    pages = ["x" * 1500, "middle original text", "last page"]
    chunks = page_chunks(
        SimpleNamespace(id=1, title="Paper"),
        SimpleNamespace(page_texts=pages, full_text="truncated"),
        "a" * 64,
    )
    assert {c["page_number"] for c in chunks} == {1, 2, 3}
    assert all(
        c["text"] == pages[c["page_number"] - 1][c["start_char"] : c["end_char"]]
        for c in chunks
    )
    assert all(c["source_kind"] == "pdf_original" for c in chunks)


@pytest.mark.parametrize(
    "fault", ["unsupported", "partial", "binding", "invalid", "empty"]
)
def test_delivery_cannot_publish_an_unverified_candidate(fault):
    from backend.services.answer_delivery import deliver_audited_answer

    candidate = "A paper fact [P1]"
    audit = {
        "status": "completed",
        "coverage_complete": True,
        "binding": {"text_hash": hashlib.sha256(candidate.encode()).hexdigest()},
        "claims": [{"label": "supported", "validation_errors": []}],
    }
    assert deliver_audited_answer(candidate, audit) == (candidate, "internally_checked")
    if fault == "unsupported":
        audit["claims"][0]["label"] = "contradicted"
    elif fault == "partial":
        audit["status"] = "partial"
    elif fault == "binding":
        audit["binding"]["text_hash"] = "wrong"
    elif fault == "invalid":
        audit["claims"][0]["validation_errors"] = ["missing original quote"]
    else:
        audit["claims"] = []
    answer, status = deliver_audited_answer(candidate, audit)
    assert candidate not in answer and status in {"withheld", "unchecked"}


@pytest.mark.asyncio
async def test_rag_respects_current_membership_request_topk_and_returns_original_provenance(
    pdf_material, material_env, engine, monkeypatch
):
    from backend.routers import research
    from backend.models.schemas import RagQueryRequest
    from backend.services.deep_research.hybrid_retriever import (
        HybridRetrieveResult,
        HybridRetriever,
    )

    pid, wsid, _ = pdf_material
    _, other, _ = material_env
    observed = {}

    async def embed(question):
        return [1.0, 0.0]

    async def retrieve(self, question, vec, **kwargs):
        observed.update(kwargs)
        return HybridRetrieveResult(
            hits=[
                {
                    "paper_id": pid,
                    "paper_title": "Title",
                    "section_name": "Body",
                    "snippet": "unique method evidence.",
                    "score": 0.1,
                    "chunk_id": "original",
                }
            ],
            debug=[],
        )

    async def generate(question, hits, config):
        assert hits[0]["source_kind"] == "pdf_original"
        return "Answer [P1]", "internally_checked", {"status": "completed"}

    monkeypatch.setattr("backend.services.deep_research.embedder.embed_query", embed)
    monkeypatch.setattr(
        "backend.services.deep_research.vector_store.workspace_index_available",
        lambda _: True,
    )
    monkeypatch.setattr(HybridRetriever, "retrieve", retrieve)
    monkeypatch.setattr(
        "backend.services.ai_config.get_analysis_config", lambda: {"api_key": "stub"}
    )
    monkeypatch.setattr(
        "backend.services.ai_config.get_reranker_config",
        lambda: {"enabled": False, "top_n": 19},
    )
    monkeypatch.setattr(
        "backend.services.research_answer.generate_audited_answer", generate
    )
    result = await research.query(
        wsid, RagQueryRequest(question="question", top_k=2, paper_ids=[pid, 999999])
    )
    assert (
        observed["top_k"] == 2
        and observed["paper_ids"] == [pid]
        and observed["original_only"] is True
    )
    assert (
        result.citations[0].page_number == 2
        and len(result.citations[0].document_hash) == 64
    )
    assert result.citations[0].metadata_only is False
    with pytest.raises(Exception) as error:
        await research.query(
            wsid, RagQueryRequest(question="question", paper_ids=[999999])
        )
    assert error.value.status_code == 404


def test_display_bridges_never_enter_insight_statistics():
    from backend.services.workspace_insights import _semantic_graph

    nodes = [{"id": f"paper:{i}", "kind": "paper"} for i in range(3)]
    graph = _semantic_graph(
        nodes,
        [
            {
                "source": "paper:0",
                "target": "paper:1",
                "kind": "similar",
                "weight": 0.8,
            },
            {
                "source": "paper:1",
                "target": "paper:2",
                "kind": "similar",
                "weight": 1,
                "bridge": True,
            },
        ],
    )
    assert graph.has_edge("paper:0", "paper:1") and not graph.has_edge(
        "paper:1", "paper:2"
    )


@pytest.mark.asyncio
async def test_workspace_delete_clears_graph_runs_index_preserves_library_and_report(
    material_env, engine
):
    from backend.routers.workspaces import delete_workspace
    from backend.services.deep_research.vector_store import workspace_index_available
    from backend.models.exploration import ExplorationRun

    pid, other, wsid = material_env
    index_one(wsid, pid)
    with Session(engine) as session:
        run = ResearchRun(workspace_id=wsid, mode="local", status="done")
        session.add(run)
        session.commit()
        session.refresh(run)
        session.add(ResearchGraph(workspace_id=wsid, run_id=run.id))
        session.add(
            ExplorationRun(
                question="Keep frozen report",
                status="done",
                result={"report": "immutable"},
            )
        )
        session.commit()
    await delete_workspace(wsid)
    with Session(engine) as session:
        assert session.get(Workspace, wsid) is None
        assert (
            session.get(Paper, pid) is not None
            and session.get(Paper, other) is not None
        )
        assert not session.exec(select(ResearchGraph)).all()
        assert not session.exec(select(ResearchRun)).all()
        assert session.exec(select(ExplorationRun)).one().result == {
            "report": "immutable"
        }
    assert not workspace_index_available(wsid)


@pytest.mark.asyncio
async def test_rebuild_cannot_restore_chunks_from_removed_members(
    material_env, engine, monkeypatch
):
    from backend.services.deep_research.stages import embed

    pid, other, wsid = material_env
    with Session(engine) as session:
        link = session.exec(
            select(WorkspacePaper).where(WorkspacePaper.paper_id == other)
        ).one()
        session.delete(link)
        session.commit()

    def chunk(pid):
        return {
            "id": f"{pid}:body:0",
            "paper_id": pid,
            "paper_title": "Paper",
            "section_name": "Body",
            "chunk_index": 0,
            "page_hint": "",
            "text": "Text",
        }

    writes = []
    monkeypatch.setattr(embed, "collection_model", lambda _: "old")
    monkeypatch.setattr(embed, "get_embedding_config", lambda: {"model": "new"})
    monkeypatch.setattr(
        embed, "list_workspace_chunks", lambda *a, **kw: [chunk(pid), chunk(other)]
    )
    monkeypatch.setattr(embed, "drop_workspace_collection", lambda _: None)
    monkeypatch.setattr(embed, "get_or_create", lambda *a, **kw: None)
    monkeypatch.setattr(
        embed, "upsert_chunks", lambda *a, **kw: writes.extend(kw["metas"])
    )

    async def vectors(texts):
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(embed, "embed_texts", vectors)
    assert [
        count async for count in embed.stage_embed(wsid, [chunk(other)], False)
    ] == [1]
    assert {meta["paper_id"] for meta in writes} == {pid}


@pytest.mark.asyncio
async def test_analyzer_emits_partial_for_truncated_json_and_ignores_wrong_fields(
    monkeypatch,
):
    from backend.services import ai_analyzer

    async def stream(*args, **kwargs):
        yield '{"one_sentence":"Available", "method":[], "problem":"cut off'

    monkeypatch.setattr(ai_analyzer, "stream_text", stream)
    events = [
        event
        async for event in ai_analyzer.analyze_paper_stream(
            "source", "stub", "unused", "stub"
        )
    ]
    assert events[-1].event == "partial"
    assert [event.field_name for event in events if event.event == "field"] == [
        "one_sentence"
    ]
    assert "method" in events[-1].data["missing_fields"]


@pytest.mark.asyncio
async def test_source_position_is_exact_even_when_text_repeats(
    pdf_material, monkeypatch
):
    from backend.services import material_sources
    from backend.services.material_sources import verified_pdf_hits

    pid, _, _ = pdf_material
    monkeypatch.setattr(
        material_sources,
        "extract_pdf_content",
        lambda _: SimpleNamespace(page_texts=["same text", "same text"]),
    )
    hit = {"paper_id": pid, "snippet": "same text", "score": 0.1}
    assert await verified_pdf_hits([hit]) == []  # Legacy source location is ambiguous.
    exact = {**hit, "page_number": 2, "start_char": 0, "end_char": 9}
    assert (await verified_pdf_hits([exact]))[0]["page_number"] == 2
    assert await verified_pdf_hits([{**exact, "end_char": 8}]) == []


@pytest.mark.asyncio
async def test_rag_does_not_publish_after_material_is_removed(
    pdf_material, engine, monkeypatch
):
    from backend.routers import research
    from backend.models.schemas import RagQueryRequest
    from backend.services.deep_research.hybrid_retriever import (
        HybridRetrieveResult,
        HybridRetriever,
    )
    from fastapi import HTTPException

    pid, wsid, _ = pdf_material

    async def embed(question):
        return [1.0, 0.0]

    async def retrieve(*args, **kwargs):
        return HybridRetrieveResult(
            hits=[
                {
                    "paper_id": pid,
                    "paper_title": "Title",
                    "section_name": "Body",
                    "snippet": "unique method evidence.",
                    "score": 0.1,
                }
            ],
            debug=[],
        )

    async def generate(*args):
        with Session(engine) as session:
            link = session.exec(
                select(WorkspacePaper).where(WorkspacePaper.paper_id == pid)
            ).one()
            session.delete(link)
            session.commit()
        return "Should not be delivered", "internally_checked", {}

    monkeypatch.setattr("backend.services.deep_research.embedder.embed_query", embed)
    monkeypatch.setattr(
        "backend.services.deep_research.vector_store.workspace_index_available",
        lambda _: True,
    )
    monkeypatch.setattr(HybridRetriever, "retrieve", retrieve)
    monkeypatch.setattr(
        "backend.services.ai_config.get_analysis_config", lambda: {"api_key": "stub"}
    )
    monkeypatch.setattr(
        "backend.services.research_answer.generate_audited_answer", generate
    )
    with pytest.raises(HTTPException) as error:
        await research.query(wsid, RagQueryRequest(question="Question"))
    assert (
        error.value.status_code == 409
        and error.value.detail["code"] == "MATERIAL_CHANGED"
    )


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_nonworkflow_cors_preflight(method):
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.config import settings

    # No lifespan: never migrate or start jobs against the user's database.
    client = TestClient(app)
    response = client.options(
        "/api/research/workspaces/1/query",
        headers={
            "Origin": settings.frontend_origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == settings.frontend_origin


@pytest.mark.asyncio
async def test_incremental_index_prunes_previous_document_only_after_success(
    material_env, monkeypatch
):
    from backend.services.deep_research.stages import embed
    from backend.services.deep_research.vector_store import list_workspace_chunks

    pid, _, wsid = material_env
    index_one(wsid, pid)
    monkeypatch.setattr(embed, "get_embedding_config", lambda: {"model": "stub"})

    async def vectors(texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(embed, "embed_texts", vectors)
    new = {
        "id": f"{pid}:pdf:new:1:0",
        "paper_id": pid,
        "paper_title": "Paper",
        "section_name": "Body",
        "chunk_index": 0,
        "page_hint": "1",
        "text": "new original",
        "source_kind": "pdf_original",
    }
    assert [count async for count in embed.stage_embed(wsid, [new], False)] == [1]
    assert [c["id"] for c in list_workspace_chunks(wsid)] == [new["id"]]

    async def failed(texts):
        raise RuntimeError("offline fault")

    monkeypatch.setattr(embed, "embed_texts", failed)
    with pytest.raises(RuntimeError):
        _ = [
            count
            async for count in embed.stage_embed(
                wsid, [{**new, "id": f"{pid}:pdf:next:1:0"}], False
            )
        ]
    assert [c["id"] for c in list_workspace_chunks(wsid)] == [new["id"]]


@pytest.mark.asyncio
async def test_code_analysis_cannot_write_orphans_after_paper_deletion(
    material_env, engine, monkeypatch, tmp_path
):
    from backend.routers import code
    from backend.services import code_analyzer, ai_config
    from backend.models.code_analysis import CodeAnalysis

    pid, _, _ = material_env
    monkeypatch.setattr(code, "get_engine", lambda: engine)
    monkeypatch.setattr(ai_config, "get_analysis_config", lambda: {"api_key": ""})
    monkeypatch.setattr(code_analyzer, "build_file_tree", lambda _: [])
    monkeypatch.setattr(code_analyzer, "select_core_files", lambda *args: [])
    monkeypatch.setattr(code_analyzer, "analyze_imports", lambda *args: ([], {}))
    with Session(engine) as session:
        paper = session.get(Paper, pid)
        paper.code_status = "ready"
        paper.code_path = str(tmp_path)
        session.add(paper)
        session.commit()
    response = await code.analyze_code(pid)
    with Session(engine) as session:
        database.delete_paper(session, pid)
    events = "".join([event async for event in response.body_iterator])
    assert "event: error" in events and "event: done" not in events
    with Session(engine) as session:
        assert not session.exec(select(CodeAnalysis)).all()
