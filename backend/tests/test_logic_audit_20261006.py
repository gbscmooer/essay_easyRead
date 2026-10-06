# ruff: noqa: F811
"""Offline regression scenarios for the verified logic audit."""

import asyncio
import io
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.exc import IntegrityError
from backend.services import ai_config
from backend.tests.test_llm_profiles import profile_env  # noqa: F401
from backend.tests.test_exploration_resume import resume_env, _make_run  # noqa: F401
from backend.tests.test_question_loop import loop_env  # noqa: F401


@pytest.mark.parametrize("legacy", ["analysis", "translate", "flat_key"])
def test_legacy_profile_adoption_preserves_provider(profile_env, legacy):
    path, key_path = profile_env
    fields = {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key": "stub-legacy",
    }
    if legacy == "flat_key":
        ai_config.dump_file_config({"ui": {"theme": "dark"}}, str(path))
        ai_config.dump_file_config(
            {**fields, "unrelated": {"keep": True}}, str(key_path)
        )
    else:
        ai_config.dump_file_config({legacy: fields, "ui": {"theme": "dark"}}, str(path))
    before = ai_config.get_llm_config()
    state = ai_config.ensure_default_llm_profile()
    assert state["active_id"] and len(state["profiles"]) == 1
    after = ai_config.get_llm_config()
    assert all(
        after[k] == before[k]
        for k in ("api_key", "base_url", "model", "provider", "api_format")
    )
    assert ai_config.load_config()["ui"]["theme"] == "dark"
    assert ai_config.ensure_default_llm_profile() == state
    if legacy == "flat_key":
        assert ai_config.read_file_config(str(key_path))["unrelated"] == {"keep": True}
        ai_config.save_config_key({"api_key": "new-stub"}, str(key_path))
        assert ai_config.load_config_key()["api_key"] == "new-stub"


def test_toml_profiles_custom_theme_roundtrip(profile_env):
    ai_config.upsert_llm_profile(
        {
            "name": '中文 "档案"',
            "model": "offline",
            "api_key": "stub",
            "proxy": {"enabled": True},
        }
    )
    cfg = ai_config.load_config()
    cfg["ui"]["custom_themes"] = [
        {"id": "custom:one", "name": "深色\n主题", "colors": {"bg-primary": "#010203"}}
    ]
    ai_config.save_config(cfg)
    content, _, _ = ai_config.export_config("toml", mask_keys=True)
    parsed = ai_config.parse_config_content(content, fmt="toml")
    assert parsed["llm_profiles"]["profiles"][0]["name"] == '中文 "档案"'
    assert parsed["ui"]["custom_themes"] == cfg["ui"]["custom_themes"]
    ai_config.import_config(content, fmt="toml")
    assert len(ai_config.get_llm_profiles()["profiles"]) == 1


@pytest.mark.asyncio
async def test_history_cap_reserves_before_concurrent_requests(monkeypatch):
    from backend.services.deep_research import explorer

    calls = []

    async def history(doi):
        calls.append(doi)
        await asyncio.sleep(0)
        return {"counts_by_year": [{"year": 2025, "cited_by_count": 1}]}

    monkeypatch.setattr(
        "backend.services.deep_research.providers.openalex.get_shared_openalex_provider",
        lambda: SimpleNamespace(get_citation_history=history),
    )
    papers = [{"doi": f"10.1234/{i}"} for i in range(40)]
    await explorer.enrich_papers(papers, history_cap=3)
    assert calls == ["10.1234/0", "10.1234/1", "10.1234/2"]


def test_report_failure_keeps_checkpoint_and_resume(resume_env, monkeypatch):
    from backend.services.deep_research import explorer
    from backend.models.exploration import ExplorationRun

    engine, events, provider = resume_env
    run_id = _make_run(engine)
    original = explorer.build_survey_report

    async def fail(*args, **kwargs):
        if kwargs.get("stop_after"):
            return await original(*args, **kwargs)
        raise RuntimeError("offline report fault")

    monkeypatch.setattr(explorer, "build_survey_report", fail)
    asyncio.run(explorer.run_exploration(run_id))
    with Session(engine) as session:
        run = session.get(ExplorationRun, run_id)
        assert run.status == "failed" and run.checkpoint
        assert (
            "extract" in run.checkpoint["completed_stages"]
            and run.result["report_error"]
        )
    assert not any(name == "done" for name, _ in events)
    searches = provider.search_calls
    monkeypatch.setattr(explorer, "build_survey_report", original)
    asyncio.run(explorer.run_exploration(run_id, resume=True))
    assert provider.search_calls == searches
    with Session(engine) as session:
        assert session.get(ExplorationRun, run_id).status == "done"
        assert session.get(ExplorationRun, run_id).checkpoint is None


@pytest.mark.asyncio
async def test_rebuild_preserves_other_chunks_and_failure_does_not_drop(monkeypatch):
    from backend.services.deep_research.stages import embed

    old = {
        "id": "1:body:0",
        "text": "old",
        "paper_id": 1,
        "paper_title": "Old",
        "section_name": "Body",
        "chunk_index": 0,
        "page_hint": 1,
    }
    new = {**old, "id": "2:body:0", "paper_id": 2, "text": "new"}
    calls = []
    monkeypatch.setattr(embed, "get_embedding_config", lambda: {"model": "new-model"})
    monkeypatch.setattr(embed, "collection_model", lambda _: "old-model")
    monkeypatch.setattr(embed, "get_existing_ids", lambda _: {old["id"]})
    monkeypatch.setattr(embed, "list_workspace_chunks", lambda *a, **kw: [old])
    monkeypatch.setattr(embed, "get_or_create", lambda *a, **kw: None)
    monkeypatch.setattr(
        embed, "drop_workspace_collection", lambda _: calls.append("drop")
    )
    monkeypatch.setattr(
        embed, "upsert_chunks", lambda *a, **kw: calls.append(kw["ids"])
    )

    async def vectors(texts):
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(embed, "embed_texts", vectors)
    assert [x async for x in embed.stage_embed(1, [new], False)] == [2]
    assert calls == ["drop", [old["id"], new["id"]]]
    calls.clear()

    async def failed(texts):
        raise RuntimeError("offline embedding fault")

    monkeypatch.setattr(embed, "embed_texts", failed)
    with pytest.raises(RuntimeError):
        _ = [x async for x in embed.stage_embed(1, [new], True)]
    assert calls == []


def test_dense_hit_uses_real_chroma_id(monkeypatch):
    from backend.services.deep_research import vector_store, hybrid_retriever

    class Collection:
        def query(self, **kw):
            return {
                "ids": [["12:body:3"]],
                "documents": [["same"]],
                "metadatas": [
                    [{"paper_id": 12, "section_name": "Body", "chunk_index": 3}]
                ],
                "distances": [[0.1]],
            }

    monkeypatch.setattr(
        vector_store,
        "_get_client",
        lambda: SimpleNamespace(get_collection=lambda _: Collection()),
    )
    hit = vector_store.query_workspace(1, [1.0])[0]
    assert hit["chunk_id"] == "12:body:3"
    ranks = hybrid_retriever.fuse_ranked_pools(
        {"dense": [hit["chunk_id"]], "bm25": ["12:body:3"]}
    )
    assert len(ranks) == 1 and set(ranks[0][2]) == {"dense", "bm25"}


@pytest.mark.asyncio
async def test_cache_eviction_does_not_remove_inflight_inputs(monkeypatch):
    from backend.services.deep_research import literature_retrieval as retrieval

    retrieval._vectors.clear()
    waiting, release = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(
        retrieval, "get_embedding_config", lambda: {"model": "stub", "batch_size": 1}
    )

    async def vectors(texts):
        if texts == ["second"]:
            waiting.set()
            await release.wait()
        return [[1.0, 2.0] for _ in texts]

    monkeypatch.setattr(retrieval, "embed_texts", vectors)
    first = asyncio.create_task(retrieval.document_vectors(["first", "second"]))
    await waiting.wait()
    await retrieval.document_vectors([f"other-{i}" for i in range(4097)])
    release.set()
    assert (await first).shape == (2, 2)
    retrieval._vectors.clear()


def test_segment_evidence_is_local_to_each_batch():
    from backend.services.deep_research.report_delivery import canonical_segments

    rows = [
        {
            "method": f"M{i}",
            "evidence": [{"paper_key": f"p{i}", "evidence_id": f"e{i}"}],
        }
        for i in range(25)
    ]
    _, segments = canonical_segments(
        {}, {"experimental_comparisons": rows}, {f"p{i}": i + 1 for i in range(25)}
    )
    assert segments[0]["evidence_ids"] == [f"e{i}" for i in range(24)]
    assert segments[1]["evidence_ids"] == ["e24"] and segments[1][
        "citation_numbers"
    ] == [25]


def test_partial_json_does_not_promote_nested_method():
    from backend.services.ai_analyzer import _try_parse_partial_json

    parsed = _try_parse_partial_json(
        '{"experiments":{"result_table":[{"method":"Nested"}]},"method":{"summary":"Real"},"title":'
    )
    assert parsed["method"] == {"summary": "Real"}


@pytest.mark.asyncio
async def test_concurrency_resize_updates_existing_queue():
    from backend.services.llm import client

    before = client.model_concurrency()
    try:
        client.set_model_concurrency(1)
        limiter = client._model_semaphore()
        await limiter.acquire()
        pending = asyncio.create_task(limiter.acquire())
        await asyncio.sleep(0)
        assert not pending.done()
        client.set_model_concurrency(2)
        await asyncio.wait_for(pending, 1)
        assert client._model_semaphore() is limiter
        client.set_model_concurrency(1)
        client._model_semaphore()
        blocked = asyncio.create_task(limiter.acquire())
        limiter.release()
        await asyncio.sleep(0)
        assert not blocked.done()
        limiter.release()
        await asyncio.wait_for(blocked, 1)
        limiter.release()
    finally:
        client.set_model_concurrency(before)


def test_task_completion_keeps_stop_diagnostics(loop_env):
    from backend.services.deep_research.research_loop.contracts import Action
    from backend.models.exploration import ExplorationRun

    repo, _ = loop_env
    with repo.transaction() as session:
        run = session.get(ExplorationRun, repo.run_id)
        run.checkpoint = {"stop_details": [{"code": "original"}], "no_progress": 2}
        session.add(run)
    repo.complete_task(
        *repo.claim_task(
            Action(tool="search_papers", question_id="main", query="offline")
        ),
        {},
    )
    assert repo.run().checkpoint["stop_details"] == [{"code": "original"}]
    assert repo.run().checkpoint["no_progress"] == 3


def test_db_guard_blocks_active_insert_and_terminal_resume(tmp_path):
    from backend.models.exploration import ExplorationRun
    from backend.services.database import _migrate_schema

    engine = create_engine(f"sqlite:///{tmp_path}/guard.db")
    SQLModel.metadata.create_all(engine)
    _migrate_schema(engine)
    with Session(engine) as session:
        active = ExplorationRun(question="active", status="paused")
        old = ExplorationRun(question="old", status="done")
        session.add_all([active, old])
        session.commit()
        session.add(ExplorationRun(question="raced create"))
        with pytest.raises(IntegrityError, match="EXPLORATION_RUNNING"):
            session.commit()
        session.rollback()
        old.status = "running"
        session.add(old)
        with pytest.raises(IntegrityError, match="EXPLORATION_RUNNING"):
            session.commit()
        session.rollback()
        assert session.get(ExplorationRun, active.id).status == "paused"


def test_old_v2_retry_refuses_without_state_change(client, engine):
    from backend.models.exploration import ExplorationRun

    with Session(engine) as session:
        run = ExplorationRun(
            question="old contract",
            status="partial",
            workflow_version="academic_survey_v2",
            state_version=7,
        )
        session.add(run)
        session.commit()
        run_id = run.id
    response = client.post(f"/api/exploration/runs/{run_id}/retry", json={})
    assert (
        response.status_code == 400
        and response.json()["code"] == "WORKFLOW_NOT_IMPLEMENTED"
    )
    with Session(engine) as session:
        run = session.get(ExplorationRun, run_id)
        assert run.status == "partial" and run.state_version == 7


@pytest.mark.asyncio
async def test_reindex_double_request_is_rejected(monkeypatch):
    from backend.routers import papers

    blocker = asyncio.Event()

    async def job(*args):
        await blocker.wait()

    monkeypatch.setattr(
        "backend.services.auto_indexer.count_reindex_targets", lambda _: 3
    )
    monkeypatch.setattr("backend.services.auto_indexer.reindex_library", job)
    monkeypatch.setattr(papers, "_reindex_task", None)
    monkeypatch.setattr(papers, "_reindex_schedule_lock", asyncio.Lock())
    results = await asyncio.gather(
        papers.reindex_library_endpoint(False),
        papers.reindex_library_endpoint(False),
        return_exceptions=True,
    )
    assert sum(isinstance(r, dict) for r in results) == 1
    assert next(r for r in results if not isinstance(r, dict)).status_code == 409
    blocker.set()
    await papers._reindex_task


def test_theme_post_preserves_model_and_cors(client, profile_env, monkeypatch):
    monkeypatch.setattr(
        "backend.routers.analysis._trusted_local_config_request", lambda _: True
    )
    cfg = ai_config.load_config()
    cfg["llm"]["model"] = "offline-kept"
    ai_config.save_config(cfg)
    from backend.config import settings

    cors = client.options(
        "/api/analysis/config/theme",
        headers={
            "Origin": settings.frontend_origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert (
        cors.status_code == 200
        and cors.headers["access-control-allow-origin"] == settings.frontend_origin
    )
    response = client.post("/api/analysis/config/theme", json={"theme": "dark"})
    assert response.status_code == 200 and response.json()["ui"]["theme"] == "dark"
    assert ai_config.get_llm_config()["model"] == "offline-kept"
    assert (
        client.post("/api/analysis/config/theme", json={"theme": "missing"}).status_code
        == 422
    )


def test_mcp_handler_failure_preserves_id(monkeypatch):
    from backend import mcp_server

    responses = []
    monkeypatch.setattr(
        mcp_server.sys,
        "stdin",
        io.StringIO('{"jsonrpc":"2.0","id":42,"method":"broken"}\ninvalid\n'),
    )
    monkeypatch.setattr(
        mcp_server,
        "handle_request",
        lambda *args: (_ for _ in ()).throw(RuntimeError("stub")),
    )
    monkeypatch.setattr(mcp_server, "_write_response", responses.append)
    mcp_server.main()
    assert responses[0]["id"] == 42 and responses[0]["error"]["code"] == -32603
    assert responses[1]["id"] is None and responses[1]["error"]["code"] == -32700


def test_bibtex_go_line_numbers_and_unique_coverage():
    from backend.services.bibtex import _escape
    from backend.services.import_parser import _parse_go
    from backend.services.deep_research.report_evidence import _subquestions

    assert _escape(r"A\B{C}") == r"A\textbackslash{}B\{C\}"
    imports = _parse_go(
        'package x\n\nimport "fmt"\nimport (\n    "os"\n    alias "net/http"\n)\n'
    )
    assert [(i.module, i.line_number) for i in imports] == [
        ("fmt", 3),
        ("os", 5),
        ("net/http", 6),
    ]
    assert (
        _subquestions(
            "experimental_comparisons", {"hardware": "GPU"}, "latency 1ms"
        ).count("runtime_efficiency")
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "source", "expected"),
    [
        ("pending", "openalex", "new"),
        ("pending", "manual", "old"),
        ("done", "openalex", "old"),
    ],
)
async def test_reindex_respects_manual_and_completed_metadata(
    engine, monkeypatch, status, source, expected
):
    from backend.models.paper import Paper
    from backend.services import auto_indexer

    monkeypatch.setattr(auto_indexer, "_get_engine", lambda: engine)
    monkeypatch.setattr(auto_indexer, "_extract", lambda _: ("text", {}, "first"))

    async def metadata(*args):
        return {"title": "new", "authors": "new-author", "year": 2025}

    monkeypatch.setattr(auto_indexer, "_extract_meta", metadata)
    with Session(engine) as session:
        paper = Paper(
            filename="stub.pdf",
            pdf_path="stub.pdf",
            file_size=10,
            title="old",
            status=status,
            bibliography={"field_sources": {"title": source}},
        )
        session.add(paper)
        session.commit()
        pid = paper.id
    await auto_indexer.auto_index(pid, "stub.pdf", force_meta=True, overwrite=True)
    with Session(engine) as session:
        paper = session.get(Paper, pid)
        assert (
            paper.title == expected
            and paper.status == status
            and paper.index_status == "indexed"
        )
        if expected == "new":
            assert paper.bibliography["field_sources"]["title"] == "grobid"


@pytest.mark.asyncio
async def test_download_policy_error_isolated_and_terminal(session, monkeypatch):
    from backend.services.paper_identity import PaperWork, register_stub
    from backend.services import paper_download
    from backend.models.paper import Paper

    paper = register_stub(
        session, PaperWork(title="Policy-blocked", arxiv_id="2302.00002")
    )

    async def url(*args):
        return "https://example.test/policy.pdf"

    async def blocked(*args):
        raise ValueError("excluded")

    monkeypatch.setattr(paper_download, "resolve_pdf_url", url)
    monkeypatch.setattr(
        "backend.services.deep_research.pdf_downloader.download_pdf", blocked
    )
    assert await paper_download.download_for_paper(session, paper.id) is None
    assert session.get(Paper, paper.id).download_status == "failed"


@pytest.mark.asyncio
async def test_scopus_forward_uses_same_single_page_cap(monkeypatch):
    from backend.services.deep_research.providers.scopus import ScopusProvider

    provider = ScopusProvider(api_key="stub")
    params = []

    async def get(path, arguments):
        params.append(arguments)
        return {"search-results": {"entry": []}}

    monkeypatch.setattr(provider, "_get", get)
    await provider.get_paper_citations("123", limit=200)
    assert params[0]["count"] == 25
    await provider.aclose()


def test_doi_merge_and_reference_punctuation():
    from backend.services.research_sources import merge_corpus
    from backend.services.deep_research.ref_parser import extract_references

    corpus = merge_corpus(
        [],
        [
            {"key": "a", "title": "First", "doi": "http://dx.doi.org/10.1234/abc"},
            {"key": "b", "title": "Other", "doi": "doi:10.1234/abc"},
        ],
        20,
    )
    assert len(corpus) == 1
    refs = extract_references(
        "\nReferences\n[1] Name (doi.org/10.1234/abc).\n[2] Name doi.org/10.1234/(valid)\n"
    )
    assert [r["doi"] for r in refs] == ["10.1234/abc", "10.1234/(valid)"]


def test_neighbors_include_edges_between_boundary_nodes(monkeypatch):
    from backend.services import agent_tools

    graph = {
        "nodes": [{"id": f"paper:{i}"} for i in range(1, 4)],
        "edges": [
            {"source": "paper:1", "target": "paper:2", "kind": "cites"},
            {"source": "paper:1", "target": "paper:3", "kind": "cites"},
            {"source": "paper:2", "target": "paper:3", "kind": "related"},
        ],
    }
    # Use the actual canonical node id expected by the tool.
    node_id = agent_tools._paper_node_id
    graph = {
        "nodes": [{"id": node_id(i)} for i in range(1, 4)],
        "edges": [
            {"source": node_id(1), "target": node_id(2), "kind": "cites"},
            {"source": node_id(1), "target": node_id(3), "kind": "cites"},
            {"source": node_id(2), "target": node_id(3), "kind": "related"},
        ],
    }
    monkeypatch.setattr(agent_tools, "get_workspace_graph", lambda _: graph)
    result = agent_tools.graph_neighbors(1, 1, depth=1)
    assert len(result["nodes"]) == 3 and len(result["edges"]) == 3
    assert (
        len(agent_tools.graph_neighbors(1, 1, depth=1, edge_kinds=["cites"])["edges"])
        == 2
    )


@pytest.mark.asyncio
async def test_terminal_drains_tail_after_child_exit(monkeypatch):
    from backend.services import terminal

    closed = asyncio.Event()

    class Session:
        def __init__(self, cwd):
            self.eof, self.process, self.is_alive = False, None, False
            self.reads = 0

        async def spawn(self):
            pass

        def read(self):
            self.reads += 1
            if self.reads == 1:
                return b"last output"
            self.eof = True
            return b""

        def close(self):
            pass

    class Socket:
        def __init__(self):
            self.sent = []

        async def send_bytes(self, data):
            self.sent.append(data)

        async def close(self):
            closed.set()

        async def receive(self):
            await closed.wait()
            return {"type": "websocket.disconnect"}

    monkeypatch.setattr(terminal, "TerminalSession", Session)
    socket = Socket()
    await asyncio.wait_for(terminal.handle_terminal_websocket(socket, "/tmp"), 2)
    assert socket.sent == [b"last output"]


@pytest.mark.asyncio
async def test_resume_queued_task_still_requires_current_authorization(
    loop_env, monkeypatch
):
    from backend.services.deep_research.research_loop.contracts import Action
    from backend.services.deep_research.research_loop.controller import Controller
    from backend.models.research_core import ResearchTask

    repo, tools = loop_env  # search_online=False
    task_id, _ = repo.claim_task(
        Action(tool="search_papers", question_id="main", query="offline")
    )
    repo.abandon_task(task_id)
    controller = Controller(tools)

    async def none():
        return None

    async def forbidden(action):
        raise AssertionError("unauthorized queued search must not execute")

    monkeypatch.setattr(controller, "choose", none)
    monkeypatch.setattr(tools, "execute", forbidden)
    await controller.run()
    task = next(t for t in repo.rows(ResearchTask) if t.task_id == task_id)
    assert task.state == "skipped" and task.error_code == "ACTION_NO_LONGER_AVAILABLE"


@pytest.mark.asyncio
async def test_action_budget_event_binds_the_current_claim(loop_env, monkeypatch):
    from backend.services.deep_research.research_loop.contracts import Action
    from backend.services.deep_research.research_loop.controller import Controller
    from backend.models.research_core import ResearchBudgetEvent, ResearchTask
    from backend.models.exploration import ExplorationRun

    repo, tools = loop_env
    with repo.transaction() as session:
        run = session.get(ExplorationRun, repo.run_id)
        run.options = {**run.options, "search_online": True}
        session.add(run)
    action = Action(tool="search_papers", question_id="main", query="offline")
    task_id, _ = repo.claim_task(action)
    repo.abandon_task(task_id)
    controller = Controller(tools)

    async def none():
        return None

    async def success(action):
        return {}

    monkeypatch.setattr(controller, "choose", none)
    monkeypatch.setattr(tools, "execute", success)
    await controller.run()
    rows = [e for e in repo.rows(ResearchBudgetEvent) if e.resource == "actions"]
    assert rows and all(e.task_id == task_id for e in rows)
    assert (
        next(t for t in repo.rows(ResearchTask) if t.task_id == task_id).state
        == "succeeded"
    )


@pytest.mark.asyncio
async def test_late_value_error_is_execution_failure(loop_env, monkeypatch):
    from backend.services.deep_research.research_loop import worker
    from backend.services.deep_research.research_loop.controller import Controller
    from backend.models.research_core import ResearchDossierRevision

    repo, tools = loop_env
    repo.release()

    async def broken(self):
        raise ValueError("late invalid payload")

    monkeypatch.setattr(Controller, "run", broken)
    monkeypatch.setattr(worker, "get_llm_config", lambda: {"api_key": ""})
    await worker.run(repo.run_id, engine=repo.engine)
    with Session(repo.engine) as session:
        from sqlmodel import select

        dossier = session.exec(
            select(ResearchDossierRevision).where(
                ResearchDossierRevision.run_id == repo.run_id
            )
        ).one()
        assert dossier.stop_reason == "execution_failed"


def test_active_run_database_conflict_is_http_409():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.main import register_http_error_handlers

    app = FastAPI()
    register_http_error_handlers(app)

    @app.post("/activate")
    def activate():
        raise IntegrityError("update", {}, Exception("EXPLORATION_RUNNING"))

    response = TestClient(app).post("/activate")
    assert response.status_code == 409
    assert response.json()["code"] == "EXPLORATION_RUNNING"
