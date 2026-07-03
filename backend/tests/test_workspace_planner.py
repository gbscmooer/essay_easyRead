from datetime import datetime, timezone


def _analysis_for(paper, *, problem, method, datasets, baselines, contributions, limitations=""):
    from backend.models.analysis import AnalysisResult

    return AnalysisResult(
        paper_id=paper.id,
        title=paper.title,
        authors="A, B",
        venue=paper.venue or "",
        year=paper.year or 0,
        one_sentence=problem,
        problem=problem,
        key_contributions=contributions,
        outline=[],
        method={
            "overview": method,
            "pipeline": "",
            "key_modules": [{"name": method.split()[0], "role": "Core module", "innovation": method}],
        },
        experiments={
            "datasets": datasets,
            "baselines": baselines,
            "main_results": "",
            "ablation": "",
            "result_table": [],
            "figures": [],
        },
        code_mapping=[{"concept": method, "likely_implementation": "torch.nn.Module"}],
        interview_qa=[],
        limitations=limitations,
        related_papers=[],
    )


def test_workspace_reading_plan_uses_citation_prerequisites(client, session):
    from backend.models.paper import Paper
    from backend.models.research import ResearchGraph, ResearchRun
    from backend.models.workspace import Workspace, WorkspacePaper

    ws = Workspace(name="Plan WS")
    session.add(ws)
    session.commit()
    session.refresh(ws)

    run = ResearchRun(workspace_id=ws.id, mode="local", status="done")
    session.add(run)
    session.commit()
    session.refresh(run)

    foundation = Paper(
        filename="foundation.pdf",
        pdf_path="/tmp/foundation.pdf",
        file_size=1,
        title="Foundation Attention",
        venue="NeurIPS",
        year=2020,
        status="done",
        source="uploaded",
    )
    bridge = Paper(
        filename="bridge.pdf",
        pdf_path="/tmp/bridge.pdf",
        file_size=1,
        title="Efficient Attention",
        venue="ICLR",
        year=2021,
        status="done",
        source="uploaded",
    )
    target = Paper(
        filename="target.pdf",
        pdf_path="/tmp/target.pdf",
        file_size=1,
        title="Target Vision Transformer",
        venue="CVPR",
        year=2023,
        status="pending",
        source="uploaded",
        graph_digest={"summary": "Vision transformer digest", "method": "Vision attention"},
    )
    session.add(foundation)
    session.add(bridge)
    session.add(target)
    session.commit()
    for paper in (foundation, bridge, target):
        session.refresh(paper)
        session.add(WorkspacePaper(workspace_id=ws.id, paper_id=paper.id))
    session.commit()

    graph = ResearchGraph(
        workspace_id=ws.id,
        run_id=run.id,
        nodes=[
            {"id": f"paper:{foundation.id}", "kind": "paper", "label": foundation.title, "meta": {"importance": 0.9, "cluster": 0, "cluster_label": "注意力"}},
            {"id": f"paper:{bridge.id}", "kind": "paper", "label": bridge.title, "meta": {"importance": 0.7, "cluster": 0, "cluster_label": "注意力"}},
            {"id": f"paper:{target.id}", "kind": "paper", "label": target.title, "meta": {"importance": 0.4, "cluster": 0, "cluster_label": "注意力"}},
        ],
        edges=[
            {"source": f"paper:{bridge.id}", "target": f"paper:{foundation.id}", "kind": "cites", "weight": 1.0},
            {"source": f"paper:{target.id}", "target": f"paper:{bridge.id}", "kind": "cites", "weight": 1.0},
        ],
        stats={},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(graph)
    session.commit()

    response = client.get(
        f"/api/workspaces/{ws.id}/reading-plan",
        params={"target_paper_id": target.id},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["target_paper_id"] == target.id
    assert data["coverage"]["paper_count"] == 3
    assert data["coverage"]["citation_edges"] == 2

    stage_order = [
        paper["paper_id"]
        for stage in data["stages"]
        for paper in stage["papers"]
    ]
    assert stage_order == [foundation.id, bridge.id, target.id]
    assert data["stages"][1]["papers"][0]["prerequisites"] == [foundation.id]
    assert data["stages"][2]["papers"][0]["prerequisites"] == [bridge.id]


def test_workspace_survey_builds_matrix_and_draft(client, session):
    from backend.models.paper import Paper
    from backend.models.research import ResearchGraph, ResearchRun
    from backend.models.workspace import Workspace, WorkspacePaper

    ws = Workspace(name="Survey WS")
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
        title="Diffusion Segmenter",
        venue="ICCV",
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
    session.add(
        _analysis_for(
            paper_a,
            problem="Object detection with transformer decoders",
            method="Transformer decoder",
            datasets=["COCO"],
            baselines=["DETR"],
            contributions=["Set prediction", "Hungarian matching"],
            limitations="Slow convergence",
        )
    )
    session.add(
        _analysis_for(
            paper_b,
            problem="Semantic segmentation with diffusion refinement",
            method="Diffusion refinement",
            datasets=["ADE20K", "COCO"],
            baselines=["Mask2Former"],
            contributions=["Diffusion decoder", "Iterative mask refinement"],
            limitations="High sampling cost",
        )
    )
    graph = ResearchGraph(
        workspace_id=ws.id,
        run_id=run.id,
        nodes=[
            {"id": f"paper:{paper_a.id}", "kind": "paper", "label": paper_a.title, "meta": {"cluster": 0, "cluster_label": "视觉识别"}},
            {"id": f"paper:{paper_b.id}", "kind": "paper", "label": paper_b.title, "meta": {"cluster": 1, "cluster_label": "生成式分割"}},
        ],
        edges=[],
        stats={},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(graph)
    session.commit()

    response = client.get(f"/api/workspaces/{ws.id}/survey")

    assert response.status_code == 200
    data = response.json()
    assert data["paper_count"] == 2
    assert data["detail_counts"]["full"] == 2
    assert "COCO" in data["datasets"]
    assert len(data["matrix"]) == 2
    assert any(row["paper_id"] == paper_a.id and "DETR" in row["baselines"] for row in data["matrix"])
    assert {cluster["label"] for cluster in data["clusters"]} == {"视觉识别", "生成式分割"}
    assert {section["key"] for section in data["draft_sections"]} == {"overview", "methods", "experiments", "limitations"}
