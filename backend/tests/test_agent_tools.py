import asyncio
import json

import pytest

from sqlmodel import Session

from backend.models.analysis import AnalysisResult
from backend.models.paper import Paper
from backend.models.research import ResearchGraph, ResearchRun
from backend.models.workspace import Workspace, WorkspacePaper


@pytest.fixture
def agent_seed(session: Session):
    ws = Workspace(name="Agent Workspace")
    paper_a = Paper(
        filename="retrieval_augmented_generation.pdf",
        pdf_path="/tmp/rag.pdf",
        file_size=100,
        title="Retrieval Augmented Generation for Code Agents",
        authors="A. Researcher",
        venue="NeurIPS",
        year=2025,
        status="done",
        source="uploaded",
        full_text="retrieval augmented generation evidence grounded code agent",
    )
    paper_b = Paper(
        filename="graph_reasoning.pdf",
        pdf_path="/tmp/graph.pdf",
        file_size=100,
        title="Graph Reasoning for Scientific Literature",
        authors="B. Scholar",
        venue="ICML",
        year=2024,
        status="done",
        source="uploaded",
    )
    session.add(ws)
    session.add(paper_a)
    session.add(paper_b)
    session.commit()
    session.refresh(ws)
    session.refresh(paper_a)
    session.refresh(paper_b)

    session.add(WorkspacePaper(workspace_id=ws.id, paper_id=paper_a.id))
    session.add(WorkspacePaper(workspace_id=ws.id, paper_id=paper_b.id))
    session.add(
        AnalysisResult(
            paper_id=paper_a.id,
            title=paper_a.title,
            authors=paper_a.authors,
            venue=paper_a.venue,
            year=paper_a.year,
            one_sentence="Uses retrieved paper evidence to ground code-agent development.",
            problem="Code agents hallucinate implementation details without paper evidence.",
            key_contributions=["Evidence-grounded coding workflow", "Citation-aware development brief"],
            outline=[],
            method={
                "overview": "Retrieve evidence, load paper context, then build a development brief.",
                "pipeline": "search -> retrieve -> brief",
                "key_modules": [],
            },
            experiments={"datasets": ["Local paper workspace"], "baselines": ["Ungrounded agent"], "main_results": "", "ablation": ""},
            code_mapping=[{"concept": "Evidence pack", "likely_implementation": "agent_tools.retrieve_evidence"}],
            interview_qa=[],
            limitations="Requires a populated workspace index.",
            related_papers=[],
        )
    )
    run = ResearchRun(workspace_id=ws.id, mode="local", status="done")
    session.add(run)
    session.commit()
    session.refresh(run)
    session.add(
        ResearchGraph(
            workspace_id=ws.id,
            run_id=run.id,
            nodes=[
                {"id": f"paper:{paper_a.id}", "label": paper_a.title, "meta": {"importance": 0.9}},
                {"id": f"paper:{paper_b.id}", "label": paper_b.title, "meta": {"importance": 0.7}},
            ],
            edges=[
                {"source": f"paper:{paper_a.id}", "target": f"paper:{paper_b.id}", "kind": "cites", "weight": 1.0}
            ],
            stats={"node_count": 2, "edge_count": 1},
        )
    )
    session.commit()
    return {"workspace": ws, "paper_a": paper_a, "paper_b": paper_b}


def _patch_shared_engine(monkeypatch, engine):
    import backend.services.database as database

    monkeypatch.setattr(database, "_shared_engine", engine)


def test_agent_tools_search_get_and_graph(monkeypatch, engine, agent_seed):
    _patch_shared_engine(monkeypatch, engine)

    from backend.services.agent_tools import get_paper_context, graph_neighbors, search_papers

    ws = agent_seed["workspace"]
    paper = agent_seed["paper_a"]

    results = search_papers("evidence grounded coding", workspace_id=ws.id, limit=5)
    assert results["total"] == 1
    assert results["items"][0]["paper_id"] == paper.id
    assert "contributions" in results["items"][0]["matched_fields"]

    context = get_paper_context(paper.id)
    assert context["analysis_available"] is True
    assert context["profile"]["code_mapping"][0]["concept"] == "Evidence pack"

    neighbors = graph_neighbors(ws.id, paper.id, depth=1, limit=10)
    assert neighbors["center"]["id"] == f"paper:{paper.id}"
    assert [node["id"] for node in neighbors["nodes"]] == [f"paper:{paper.id}", f"paper:{agent_seed['paper_b'].id}"]
    assert neighbors["edges"][0]["kind"] == "cites"


@pytest.mark.asyncio
async def test_retrieve_evidence_uses_existing_vector_index(monkeypatch, engine, agent_seed):
    _patch_shared_engine(monkeypatch, engine)

    async def fake_embed_query(text):
        assert "code agent" in text
        return [0.1, 0.2]

    def fake_query_workspace(ws_id, vec, top_k=5, paper_ids=None):
        assert ws_id == agent_seed["workspace"].id
        assert vec == [0.1, 0.2]
        return [
            {
                "paper_id": agent_seed["paper_a"].id,
                "paper_title": agent_seed["paper_a"].title,
                "section_name": "Method",
                "snippet": "Retrieve evidence before editing code.",
                "score": 0.91,
                "chunk_id": "chunk-1",
            }
        ]

    async def verified(hits):
        return hits
    monkeypatch.setattr("backend.services.material_sources.verified_pdf_hits", verified)
    monkeypatch.setattr("backend.services.deep_research.embedder.embed_query", fake_embed_query)
    monkeypatch.setattr("backend.services.deep_research.vector_store.workspace_index_available", lambda _ws_id: True)
    monkeypatch.setattr("backend.services.deep_research.vector_store.query_workspace", fake_query_workspace)

    from backend.services.agent_tools import retrieve_evidence

    evidence = await retrieve_evidence(agent_seed["workspace"].id, "code agent evidence", top_k=3)
    assert evidence["evidence"][0]["paper_title"] == agent_seed["paper_a"].title
    assert evidence["evidence"][0]["source"] == "workspace_vector_index"


@pytest.mark.asyncio
async def test_retrieve_evidence_checks_index_before_embedding(monkeypatch, engine, agent_seed):
    _patch_shared_engine(monkeypatch, engine)
    calls = {"embed": 0, "query": 0}

    async def unexpected_embed(_text):
        calls["embed"] += 1
        raise AssertionError("embedding model must not load without an index")

    def unexpected_query(*_args, **_kwargs):
        calls["query"] += 1
        raise AssertionError("missing index must not be queried or created")

    monkeypatch.setattr("backend.services.deep_research.vector_store.workspace_index_available", lambda _ws_id: False)
    monkeypatch.setattr("backend.services.deep_research.embedder.embed_query", unexpected_embed)
    monkeypatch.setattr("backend.services.deep_research.vector_store.query_workspace", unexpected_query)

    from backend.services.agent_tools import AgentToolError, retrieve_evidence

    with pytest.raises(AgentToolError) as exc_info:
        await retrieve_evidence(agent_seed["workspace"].id, "offline evidence")

    assert exc_info.value.code == "CHROMA_NOT_INITIALIZED"
    assert calls == {"embed": 0, "query": 0}


def test_vector_store_missing_index_check_is_read_only(monkeypatch):
    from backend.services.deep_research import vector_store

    calls = {"get": 0, "create": 0}

    class MissingCollectionClient:
        def get_collection(self, _name):
            calls["get"] += 1
            raise RuntimeError("collection does not exist")

        def get_or_create_collection(self, **_kwargs):
            calls["create"] += 1
            raise AssertionError("read-only check must not create a collection")

    monkeypatch.setattr(vector_store, "_get_client", lambda: MissingCollectionClient())

    assert vector_store.workspace_index_available(12345) is False
    with pytest.raises(RuntimeError, match="collection does not exist"):
        vector_store.query_workspace(12345, [0.1, 0.2])
    assert calls == {"get": 2, "create": 0}


def test_agent_tools_http_manifest(client):
    response = client.get("/api/agent-tools/manifest")
    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["tools"]}
    assert {"paper_search", "paper_get", "paper_retrieve_evidence", "graph_neighbors", "dev_brief"} <= names


def test_agent_tools_http_error_shape(client):
    response = client.get("/api/agent-tools/workspaces/999999/papers")
    assert response.status_code == 404
    assert response.json() == {"detail": "工作区不存在", "code": "NOT_FOUND"}


def test_agent_reading_plan_rejects_paper_outside_workspace(client, monkeypatch, engine, agent_seed):
    _patch_shared_engine(monkeypatch, engine)

    response = client.post(
        f"/api/agent-tools/workspaces/{agent_seed['workspace'].id}/reading-plan",
        params={"target_paper_id": 999999},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "论文不在该工作区中", "code": "NOT_FOUND"}


@pytest.mark.asyncio
async def test_call_agent_tool_maps_reading_plan_domain_error(monkeypatch, engine, agent_seed):
    _patch_shared_engine(monkeypatch, engine)

    from backend.services.agent_tools import AgentToolError, call_agent_tool

    with pytest.raises(AgentToolError) as exc_info:
        await call_agent_tool(
            "reading_plan_build",
            {
                "workspace_id": agent_seed["workspace"].id,
                "target_paper_id": 999999,
            },
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "NOT_FOUND"
    assert exc_info.value.message == "论文不在该工作区中"


def test_mcp_reading_plan_returns_domain_error(monkeypatch, engine, agent_seed):
    _patch_shared_engine(monkeypatch, engine)

    from backend.mcp_server import handle_request

    result = handle_request(
        "tools/call",
        {
            "name": "reading_plan_build",
            "arguments": {
                "workspace_id": agent_seed["workspace"].id,
                "target_paper_id": 999999,
            },
        },
    )

    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"]) == {
        "detail": "论文不在该工作区中",
        "code": "NOT_FOUND",
    }


def test_mcp_tools_list_contains_new_and_legacy_tools():
    from backend.mcp_server import handle_request

    result = handle_request("tools/list", {})
    names = {tool["name"] for tool in result["tools"]}
    assert "paper_search" in names
    assert "query_workspace_rag" in names


def test_mcp_tool_call_returns_domain_error_without_traceback():
    from backend.mcp_server import handle_request

    result = handle_request("tools/call", {"name": "paper_get", "arguments": {}})

    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload == {"detail": "paper_id 不能为空", "code": "BAD_REQUEST"}
    assert "Traceback" not in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_event_bus_removes_cancelled_subscriber():
    from backend.services.deep_research.event_bus import EventBus

    bus = EventBus()
    stream = bus.subscribe()
    task = asyncio.create_task(stream.__anext__())
    await asyncio.sleep(0)

    assert len(bus._subscribers) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await stream.aclose()

    assert bus._subscribers == []


@pytest.mark.asyncio
async def test_research_stream_heartbeat_keeps_subscription_alive():
    from backend.models.schemas import StreamEvent
    from backend.routers.research import _stream_bus_with_heartbeat
    from backend.services.deep_research.event_bus import EventBus

    bus = EventBus()
    stream = bus.subscribe()
    gen = _stream_bus_with_heartbeat(stream, timeout=0.01)

    heartbeat = await gen.__anext__()
    assert heartbeat == ": keepalive heartbeat\n\n"
    assert len(bus._subscribers) == 1

    await bus.publish(StreamEvent(event="done", data={"run_id": 1}))
    event_chunk = await gen.__anext__()

    assert "event: done" in event_chunk
    assert '"run_id": 1' in event_chunk
    await gen.aclose()
