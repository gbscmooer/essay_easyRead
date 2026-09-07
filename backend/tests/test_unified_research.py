import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine

from backend.models.exploration import ExplorationRun
from backend.models.paper import Paper
from backend.models.workspace import Workspace, WorkspacePaper
from backend.services.research_sources import SourceError, load_seeds, merge_corpus, seed_options
from backend.routers import exploration as router


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/research.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Workspace(id=1, name="Seed study"))
        s.add(Paper(id=1, filename="a.pdf", pdf_path="/tmp/a.pdf", file_size=100, title="Seed paper", full_text="Actual full text", abstract="Abstract", doi="10.1/a"))
        s.add(Paper(id=2, filename="b.pdf", pdf_path="/tmp/b.pdf", file_size=100, title="Other paper"))
        s.commit()
        s.add(WorkspacePaper(workspace_id=1, paper_id=1))
        s.commit()
    monkeypatch.setattr(router, "get_engine", lambda: engine)
    monkeypatch.setattr(router, "_start", lambda coro: coro.close())
    app = FastAPI()
    app.include_router(router.router)
    return engine, TestClient(app)


def test_creation_snapshots_selected_seeds(env):
    engine, client = env
    response = client.post("/api/exploration/runs", json={"question": "study QA", "workspace_id": 1, "seed_paper_ids": [1], "search_online": False})
    assert response.status_code == 200
    run = response.json()
    assert run["options"]["seed_paper_ids"] == [1]
    assert run["options"]["search_online"] is False
    with Session(engine) as s:
        seeds, rows = load_seeds(s, run["options"])
        assert seeds[0]["source_text"] == "Actual full text"
        assert seeds[0]["is_seed"] is True


@pytest.mark.parametrize("body,status", [
    ({"workspace_id": 999}, 404), ({"workspace_id": 1, "seed_paper_ids": [2]}, 400),
    ({"workspace_id": 1, "seed_paper_ids": []}, 400), ({"search_online": False}, 400),
    ({"seed_paper_ids": [1]}, 400), ({"question": "  "}, 422),
])
def test_invalid_seed_scope_rejected(env, body, status):
    _, client = env
    assert client.post("/api/exploration/runs", json={"question": "research question", **body}).status_code == status


def test_merged_seed_identity_and_snapshot_preserved():
    seed = {"key": "local:1", "title": "A Study", "doi": "10.1/a", "source_text": "body", "is_seed": True}
    remote = {"key": "doi:10.1/a", "title": "A Study", "doi": "10.1/a", "abstract": "new abstract"}
    corpus = merge_corpus([seed], [remote], 1)
    assert len(corpus) == 1
    assert corpus[0]["key"] == "local:1"
    assert corpus[0]["source_text"] == "body"
    assert corpus[0]["abstract"] == "new abstract"


def test_seeded_offline_workflow_reuses_report_and_keeps_corpus(env, monkeypatch):
    engine, _ = env
    from backend.services.deep_research import explorer as ex
    from backend.services.deep_research import literature_retrieval as retrieval
    monkeypatch.setattr(ex, "get_engine", lambda: engine)
    monkeypatch.setattr("backend.services.ai_config.get_analysis_config", lambda: {})
    async def offline(*args, **kwargs):
        raise AssertionError("Literature network must not be called")
    monkeypatch.setattr(ex, "recall_papers", offline)
    monkeypatch.setattr(ex, "enrich_papers", offline)
    monkeypatch.setattr(ex, "recall_seed_neighbors", offline)
    monkeypatch.setattr(retrieval, "document_vectors", offline)
    with Session(engine) as s:
        options = seed_options(s, 1, [1], False)
        run = ExplorationRun(question="seed question", options=options)
        s.add(run)
        s.commit()
        s.refresh(run)
        run_id = run.id
    asyncio.run(ex.run_exploration(run_id))
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "done"
        assert run.result["papers"][0]["paper_id"] == 1
        assert run.result["survey_report"]["coverage"]["corpus"] == 1
        assert run.result["sources"]["seed_count"] == 1


def test_report_export_regeneration_and_busy_guard(env):
    engine, client = env
    from backend.services.deep_research.survey_report import build_survey_report
    paper = {"key": "p", "title": "A paper", "abstract": "A source", "doi": "10.1/a"}
    report = asyncio.run(build_survey_report("Research", [paper], {}, None, {}))
    with Session(engine) as s:
        run = ExplorationRun(question="Research", status="done", result={"papers": [paper], "survey_report": report})
        s.add(run)
        s.commit()
        s.refresh(run)
        run_id = run.id
    path = f"/api/exploration/runs/{run_id}/report"
    exported = client.get(path)
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    assert "default-src 'none'" in exported.headers["content-security-policy"]
    assert "<table>" in exported.text
    assert client.get(path + "?format=markdown").text.startswith("# Research")
    assert client.get(path + "?format=unknown").status_code == 422
    assert client.post(path).json()["current_stage"] == "report"
    assert client.post(path).status_code == 409
    assert client.delete(f"/api/exploration/runs/{run_id}").status_code == 409
    with Session(engine) as s:
        assert s.get(ExplorationRun, run_id).result["survey_report"] == report
