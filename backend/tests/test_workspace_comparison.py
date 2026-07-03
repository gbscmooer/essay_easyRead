from datetime import datetime, timezone


def test_workspace_comparison(client, session):
    from backend.models.analysis import AnalysisResult
    from backend.models.paper import Paper
    from backend.models.research import ResearchGraph, ResearchRun
    from backend.models.workspace import Workspace, WorkspacePaper

    ws = Workspace(name="Compare WS")
    session.add(ws)
    session.commit()
    session.refresh(ws)

    run = ResearchRun(workspace_id=ws.id, mode="local", status="done")
    session.add(run)
    session.commit()
    session.refresh(run)

    paper_a = Paper(
        filename="a.pdf",
        pdf_path="/tmp/a.pdf",
        file_size=1,
        title="Transformer Detector",
        venue="CVPR",
        year=2022,
        status="done",
        source="uploaded",
    )
    paper_b = Paper(
        filename="b.pdf",
        pdf_path="/tmp/b.pdf",
        file_size=1,
        title="Transformer Detector v2",
        venue="CVPR",
        year=2023,
        status="done",
        source="uploaded",
    )
    session.add(paper_a)
    session.add(paper_b)
    session.commit()
    session.refresh(paper_a)
    session.refresh(paper_b)

    for paper in (paper_a, paper_b):
        session.add(WorkspacePaper(workspace_id=ws.id, paper_id=paper.id))

    analysis_a = AnalysisResult(
        paper_id=paper_a.id,
        title=paper_a.title,
        authors="A, B",
        venue=paper_a.venue,
        year=paper_a.year,
        one_sentence="We detect objects with transformer decoders.",
        problem="Object detection",
        key_contributions=["Transformer decoder", "Set prediction", "Hungarian matching"],
        outline=[],
        method={
            "overview": "Use a transformer decoder for set-based object prediction.",
            "pipeline": "Backbone -> Encoder -> Decoder -> Prediction",
            "key_modules": [
                {"name": "Transformer Decoder", "role": "Decode objects", "innovation": "Set prediction"},
            ],
        },
        experiments={
            "datasets": ["COCO"],
            "baselines": ["DETR"],
            "main_results": "",
            "ablation": "",
            "result_table": [],
            "figures": [],
        },
        code_mapping=[{"concept": "Transformer decoder", "likely_implementation": "nn.TransformerDecoder"}],
        interview_qa=[],
        limitations="",
        related_papers=[],
    )
    analysis_b = AnalysisResult(
        paper_id=paper_b.id,
        title=paper_b.title,
        authors="A, C",
        venue=paper_b.venue,
        year=paper_b.year,
        one_sentence="We improve object detection with a stronger transformer decoder.",
        problem="Object detection",
        key_contributions=["Transformer decoder", "Set prediction with refinement", "Hungarian matching"],
        outline=[],
        method={
            "overview": "Transformer decoder with refinement layers for set-based object prediction.",
            "pipeline": "Backbone -> Encoder -> Decoder -> Refinement -> Prediction",
            "key_modules": [
                {"name": "Transformer Decoder", "role": "Decode objects", "innovation": "Set prediction"},
                {"name": "Refinement Head", "role": "Refine boxes", "innovation": "Iterative updates"},
            ],
        },
        experiments={
            "datasets": ["COCO"],
            "baselines": ["DETR"],
            "main_results": "",
            "ablation": "",
            "result_table": [],
            "figures": [],
        },
        code_mapping=[{"concept": "Refinement Head", "likely_implementation": "nn.Linear + iterative box update"}],
        interview_qa=[],
        limitations="",
        related_papers=[],
    )
    session.add(analysis_a)
    session.add(analysis_b)
    session.commit()

    graph = ResearchGraph(
        workspace_id=ws.id,
        run_id=run.id,
        nodes=[
            {"id": f"paper:{paper_a.id}", "kind": "paper", "label": paper_a.title, "meta": {"cluster": 0, "cluster_label": "目标检测"}},
            {"id": f"paper:{paper_b.id}", "kind": "paper", "label": paper_b.title, "meta": {"cluster": 0, "cluster_label": "目标检测"}},
        ],
        edges=[
            {"source": f"paper:{paper_a.id}", "target": f"paper:{paper_b.id}", "kind": "similar", "weight": 0.8},
        ],
        stats={"modularity": 0.41},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(graph)
    session.commit()

    r = client.post(
        f"/api/workspaces/{ws.id}/compare",
        json={"paper_a_id": paper_a.id, "paper_b_id": paper_b.id},
    )
    assert r.status_code == 200
    data = r.json()

    assert data["workspace_id"] == ws.id
    assert data["same_cluster"] is True
    assert data["cluster_label"] == "目标检测"
    assert "similar" in data["direct_edge_kinds"]
    assert data["overall_score"] > 0.5
    assert data["reading_order"]["first_paper_id"] == paper_a.id
    assert any("COCO" in item for item in data["sections"][2]["shared"])


def test_workspace_comparison_same_paper_rejected(client, session):
    from backend.models.paper import Paper
    from backend.models.workspace import Workspace, WorkspacePaper

    ws = Workspace(name="Compare Reject WS")
    session.add(ws)
    session.commit()
    session.refresh(ws)

    paper = Paper(
        filename="only.pdf",
        pdf_path="/tmp/only.pdf",
        file_size=1,
        title="Only Paper",
        status="done",
        source="uploaded",
    )
    session.add(paper)
    session.commit()
    session.refresh(paper)
    session.add(WorkspacePaper(workspace_id=ws.id, paper_id=paper.id))
    session.commit()

    r = client.post(
        f"/api/workspaces/{ws.id}/compare",
        json={"paper_a_id": paper.id, "paper_b_id": paper.id},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "BAD_REQUEST"
