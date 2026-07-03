from datetime import datetime, timezone

from sqlmodel import Session


def test_workspace_insights(client, session):
    from backend.models.paper import Paper
    from backend.models.research import ResearchGraph, ResearchRun
    from backend.models.workspace import Workspace, WorkspacePaper

    ws = Workspace(name="Insight WS")
    session.add(ws)
    session.commit()
    session.refresh(ws)

    run = ResearchRun(workspace_id=ws.id, mode="local", status="done")
    session.add(run)
    session.commit()
    session.refresh(run)

    papers = [
        Paper(filename="p1.pdf", pdf_path="/tmp/p1.pdf", file_size=1, title="Paper One", status="done", source="uploaded"),
        Paper(filename="p2.pdf", pdf_path="/tmp/p2.pdf", file_size=1, title="Paper Two", status="pending", source="expanded", graph_digest={"summary": "digest"}),
        Paper(filename="p3.pdf", pdf_path="/tmp/p3.pdf", file_size=1, title="Paper Three", status="pending", source="uploaded"),
    ]
    for paper in papers:
        session.add(paper)
    session.commit()
    for paper in papers:
        session.refresh(paper)
        session.add(WorkspacePaper(workspace_id=ws.id, paper_id=paper.id))
    session.commit()

    graph = ResearchGraph(
        workspace_id=ws.id,
        run_id=run.id,
        nodes=[
            {"id": f"paper:{papers[0].id}", "kind": "paper", "label": "Paper One", "meta": {"importance": 0.9, "cluster": 0, "cluster_label": "方法创新"}},
            {"id": f"paper:{papers[1].id}", "kind": "paper", "label": "Paper Two", "meta": {"importance": 0.5, "cluster": 0, "cluster_label": "方法创新"}},
            {"id": f"paper:{papers[2].id}", "kind": "paper", "label": "Paper Three", "meta": {"importance": 0.1, "cluster": 1, "cluster_label": "数据集"}},
        ],
        edges=[
            {"source": f"paper:{papers[0].id}", "target": f"paper:{papers[1].id}", "kind": "similar", "weight": 0.8},
            {"source": f"paper:{papers[1].id}", "target": f"paper:{papers[2].id}", "kind": "similar", "weight": 0.7},
        ],
        stats={"modularity": 0.42},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(graph)
    session.commit()

    r = client.get(f"/api/workspaces/{ws.id}/insights")
    assert r.status_code == 200
    data = r.json()

    assert data["workspace_id"] == ws.id
    assert data["graph_available"] is True
    assert data["read_counts"] == {"full": 1, "digest": 1, "none": 1}
    assert data["cluster_count"] >= 1
    assert len(data["clusters"]) >= 1
    assert data["top_papers"][0]["paper_id"] == papers[0].id
    assert data["bridge_papers"][0]["paper_id"] == papers[1].id
    assert data["next_reads"][0]["paper_id"] == papers[2].id
