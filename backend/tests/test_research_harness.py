import pytest

from sqlmodel import Session

from backend.models.analysis import AnalysisResult
from backend.models.paper import Paper
from backend.models.research import ResearchGraph, ResearchRun
from backend.models.workspace import Workspace, WorkspacePaper


@pytest.fixture
def harness_seed(session: Session):
    ws = Workspace(name="Harness Workspace")
    paper_a = Paper(
        filename="rag_agents.pdf",
        pdf_path="/tmp/rag_agents.pdf",
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
        filename="graph_lit.pdf",
        pdf_path="/tmp/graph_lit.pdf",
        file_size=100,
        title="Graph Reasoning for Scientific Literature",
        authors="B. Scholar",
        venue="ICML",
        year=2024,
        status="done",
        source="uploaded",
        full_text="graph reasoning scientific literature survey",
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
            one_sentence="Uses retrieved paper evidence to ground agents.",
            problem="Agents hallucinate without paper evidence.",
            key_contributions=["Evidence-grounded workflow", "Citation-aware brief"],
            outline=[],
            method={
                "overview": "Retrieve evidence then build structured brief.",
                "pipeline": "search -> retrieve -> brief",
                "key_modules": [],
            },
            experiments={
                "datasets": ["Local paper workspace"],
                "baselines": ["Ungrounded agent"],
                "main_results": "",
                "ablation": "",
            },
            code_mapping=[],
            interview_qa=[],
            limitations="Needs a populated workspace.",
            related_papers=[],
        )
    )
    session.add(
        AnalysisResult(
            paper_id=paper_b.id,
            title=paper_b.title,
            authors=paper_b.authors,
            venue=paper_b.venue,
            year=paper_b.year,
            one_sentence="Links papers via citation and semantic edges.",
            problem="Literature graphs are hard to navigate.",
            key_contributions=["Citation graph", "Neighborhood expansion"],
            outline=[],
            method={"overview": "Build graph then expand neighbors.", "pipeline": "", "key_modules": []},
            experiments={"datasets": ["OpenAlex sample"], "baselines": [], "main_results": "", "ablation": ""},
            code_mapping=[],
            interview_qa=[],
            limitations="Depends on metadata quality.",
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
                {
                    "source": f"paper:{paper_a.id}",
                    "target": f"paper:{paper_b.id}",
                    "kind": "cites",
                    "weight": 1.0,
                }
            ],
            stats={"node_count": 2, "edge_count": 1},
        )
    )
    session.commit()
    return {"workspace": ws, "paper_a": paper_a, "paper_b": paper_b}


def _patch_shared_engine(monkeypatch, engine):
    import backend.services.database as database

    monkeypatch.setattr(database, "_shared_engine", engine)


@pytest.mark.asyncio
async def test_literature_brief_workflow_soft_fails_evidence(monkeypatch, engine, harness_seed):
    _patch_shared_engine(monkeypatch, engine)
    monkeypatch.setattr(
        "backend.services.deep_research.vector_store.workspace_index_available",
        lambda _ws_id: False,
    )

    async def unexpected_embed(_text):
        raise AssertionError("soft-fail path must not load an embedding model")

    monkeypatch.setattr("backend.services.deep_research.embedder.embed_query", unexpected_embed)

    async def fake_goal_planner(goal, workflow):
        assert goal == "evidence grounded coding agent"
        assert workflow == "literature_brief"
        return {
            "search_query": "retrieval augmented generation code agents",
            "keywords": ["RAG", "code agents"],
            "deliverables": ["literature brief"],
            "target_paper_count": 5,
            "planner": "llm",
            "model": "fake-planner",
        }

    from backend.services.research_harness import run_research_workflow

    result = await run_research_workflow(
        workflow="literature_brief",
        goal="evidence grounded coding agent",
        workspace_id=harness_seed["workspace"].id,
        plan_goal=fake_goal_planner,
    )

    assert result["status"] == "completed"
    assert result["harness"]["engine"] == "langgraph"
    assert result["artifacts"]["markdown"]
    assert "Literature Brief" in result["artifacts"]["markdown"]
    assert "langgraph/literature_brief" in result["artifacts"]["markdown"]
    assert harness_seed["paper_a"].id in result["artifacts"]["selected_paper_ids"]
    assert result["artifacts"]["goal_plan"]["planner"] == "llm"
    assert result["harness"]["planner"] == "llm"
    assert "Planned query: retrieval augmented generation code agents" in result["artifacts"]["markdown"]
    steps = {item["step"]: item["status"] for item in result["journal"]}
    assert steps["search_papers"] == "ok"
    assert steps["load_paper_contexts"] == "ok"
    assert steps["retrieve_evidence"] == "soft_fail"
    assert steps["export_brief"] == "ok"


@pytest.mark.asyncio
async def test_survey_path_workflow(monkeypatch, engine, harness_seed):
    _patch_shared_engine(monkeypatch, engine)

    from backend.services.research_harness import run_research_workflow

    result = await run_research_workflow(
        workflow="survey_path",
        goal="build a short survey and reading path",
        workspace_id=harness_seed["workspace"].id,
    )

    assert result["status"] == "completed"
    assert result["harness"]["engine"] == "langgraph"
    assert result["artifacts"]["reading_plan"] is not None
    assert result["artifacts"]["survey"] is not None
    assert "Reading Path" in result["artifacts"]["markdown"]
    assert "Comparison Matrix" in result["artifacts"]["markdown"]
    assert "langgraph/survey_path" in result["artifacts"]["markdown"]
    assert any(item["step"] == "reading_plan_build" and item["status"] == "ok" for item in result["journal"])


@pytest.mark.asyncio
async def test_literature_brief_falls_back_when_goal_planner_fails(monkeypatch, engine, harness_seed):
    _patch_shared_engine(monkeypatch, engine)
    monkeypatch.setattr(
        "backend.services.deep_research.vector_store.workspace_index_available",
        lambda _ws_id: False,
    )

    async def failing_planner(_goal, _workflow):
        raise RuntimeError("planner unavailable")

    from backend.services.research_harness import run_research_workflow

    result = await run_research_workflow(
        workflow="literature_brief",
        goal="retrieval augmented generation",
        workspace_id=harness_seed["workspace"].id,
        plan_goal=failing_planner,
    )

    assert result["status"] == "completed"
    assert result["artifacts"]["goal_plan"]["planner"] == "deterministic"
    assert result["artifacts"]["goal_plan"]["search_query"] == "retrieval augmented generation"
    parse_entry = next(item for item in result["journal"] if item["step"] == "parse_goal")
    assert parse_entry["status"] == "soft_fail"


def test_research_workflows_http(client, monkeypatch, engine, harness_seed):
    _patch_shared_engine(monkeypatch, engine)

    listed = client.get("/api/agent-tools/workflows")
    assert listed.status_code == 200
    names = {item["name"] for item in listed.json()["workflows"]}
    assert {"literature_brief", "survey_path"} <= names

    response = client.post(
        "/api/agent-tools/runs",
        json={
            "workflow": "survey_path",
            "goal": "survey package",
            "workspace_id": harness_seed["workspace"].id,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["artifacts"]["markdown"]


def test_research_workflow_http_maps_missing_workspace_to_404(client, monkeypatch, engine):
    _patch_shared_engine(monkeypatch, engine)

    response = client.post(
        "/api/agent-tools/runs",
        json={
            "workflow": "survey_path",
            "goal": "survey package",
            "workspace_id": 999999,
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "工作区不存在", "code": "NOT_FOUND"}


def test_research_workflow_http_maps_empty_workspace_to_404(client, monkeypatch, engine, session):
    _patch_shared_engine(monkeypatch, engine)
    workspace = Workspace(name="Empty Harness Workspace")
    session.add(workspace)
    session.commit()
    session.refresh(workspace)

    response = client.post(
        "/api/agent-tools/runs",
        json={
            "workflow": "literature_brief",
            "goal": "survey package",
            "workspace_id": workspace.id,
        },
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "工作区内没有可用论文，请先导入或关联论文",
        "code": "NO_PAPERS",
    }


def test_research_workflow_cors_preflight(client):
    from backend.config import settings

    response = client.options(
        "/api/agent-tools/runs",
        headers={
            "Origin": settings.frontend_origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == settings.frontend_origin
    assert "POST" in response.headers["access-control-allow-methods"]


def test_manifest_includes_research_tools(client):
    response = client.get("/api/agent-tools/manifest")
    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["tools"]}
    assert {"reading_plan_build", "survey_build"} <= names


def test_workflow_specs_declare_langgraph_engine(client):
    response = client.get("/api/agent-tools/workflows")
    assert response.status_code == 200
    for item in response.json()["workflows"]:
        assert item["engine"] == "langgraph"
