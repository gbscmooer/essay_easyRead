"""研究路由（单循环）：创建、修正与版本链、暂停调整、继续、取消、导出。worker 一律替换为假任务。"""

import pytest
from sqlmodel import Session

from backend.models.exploration import ExplorationRun

PAPERS = [
    {"key": "doi:10.1/a", "title": "Surgical VLM", "doi": "10.1/a", "abstract": "surgical video", "label": 2},
    {"key": "arxiv:2401.1", "title": "Endoscopy LLM", "arxiv_id": "2401.1", "abstract": "endoscopy", "label": 1},
    {"key": "title:noid", "title": "Paper without ids", "abstract": "x", "label": 1},
]


def _run(engine, **overrides) -> int:
    fields = {"question": "多模态大模型用于手术视频理解", "depth": "quick", "status": "done",
              "options": {"workspace_id": None, "seed_paper_ids": [], "search_online": True},
              "result": {"question": "q", "papers": PAPERS,
                         "answer": {"status": "ok", "text": "Answer [1].",
                                    "references": [{"n": 1, "key": "doi:10.1/a", "title": "Surgical VLM",
                                                    "year": 2024, "excerpts": ["surgical video"]}]}}}
    fields.update(overrides)
    with Session(engine) as s:
        run = ExplorationRun(**fields)
        s.add(run)
        s.commit()
        s.refresh(run)
        return run.id


@pytest.fixture
def launched(monkeypatch, engine):
    from backend.routers import exploration

    started = []
    monkeypatch.setattr(exploration, "get_engine", lambda: engine)
    monkeypatch.setattr(exploration, "_launch", lambda run_id, resume=False: started.append((run_id, resume)))
    return started


def test_create_launches_single_loop_with_overrides(client, launched):
    response = client.post("/api/exploration/runs", json={
        "question": "dense retrieval for long documents", "depth": "lite",
        "loop": {"expand_layers": 0}, "answer": {"drafts": 1}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pending" and launched == [(body["id"], False)]
    assert body["options"]["loop"] == {"expand_layers": 0} and body["options"]["answer"] == {"drafts": 1}
    busy = client.post("/api/exploration/runs", json={"question": "another question"})
    assert busy.status_code == 409 and busy.json()["code"] == "EXPLORATION_RUNNING"


def test_list_reports_selected_count_and_answer_status(client, engine, launched):
    _run(engine)
    rows = client.get("/api/exploration/runs").json()
    assert rows[0]["selected"] == 3 and rows[0]["answer_status"] == "ok"


def test_revise_creates_child_with_reuse_conditions_and_exclusions(client, engine, launched):
    parent_id = _run(engine)
    response = client.post(f"/api/exploration/runs/{parent_id}/revise", json={
        "question": "多模态大模型用于腹腔镜手术视频理解",
        "additional_questions": ["只看 2023 年以后", "关注公开数据集"],
        "exclude_keys": ["doi:10.1/a", "title:noid"], "reuse_materials": True})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["question"] == "多模态大模型用于腹腔镜手术视频理解" and launched == [(body["id"], False)]
    with Session(engine) as s:
        child = s.get(ExplorationRun, body["id"])
        assert child.options["parent_run_id"] == parent_id
        assert child.options["reuse_from_run_id"] == parent_id
        assert child.options["revision_conditions"] == ["只看 2023 年以后", "关注公开数据集"]
        assert child.options["exclusions"]["dois"] == ["10.1/a"]
        assert child.options["exclusions"]["titles"] == ["Paper without ids"]
    versions = client.get(f"/api/exploration/runs/{body['id']}/versions").json()
    assert [row["id"] for row in versions["ancestors"]] == [parent_id]
    parent_view = client.get(f"/api/exploration/runs/{parent_id}/versions").json()
    assert [row["id"] for row in parent_view["children"]] == [body["id"]]


def test_revise_rejects_unknown_exclusion_and_active_runs(client, engine, launched):
    parent_id = _run(engine)
    bad = client.post(f"/api/exploration/runs/{parent_id}/revise", json={
        "question": "新问题", "exclude_keys": ["doi:missing"]})
    assert bad.status_code == 422 and bad.json()["code"] == "INVALID_REVISION"
    _run(engine, question="其他", status="paused")
    busy = client.post(f"/api/exploration/runs/{parent_id}/revise", json={"question": "新问题"})
    assert busy.status_code == 409


def test_adjust_paused_run_records_exclusions_and_queries(client, engine, launched):
    run_id = _run(engine, status="paused", checkpoint={"version": "pasa_loop_v1", "papers": {
        "openalex:W9": {"key": "openalex:W9", "title": "Pool only paper"}}})
    response = client.post(f"/api/exploration/runs/{run_id}/adjust", json={
        "exclude_keys": ["arxiv:2401.1", "openalex:W9"], "add_queries": ["laparoscopic video VLM"]})
    assert response.status_code == 200, response.text
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.options["exclusions"]["arxiv_ids"] == ["2401.1"]
        assert run.options["exclusions"]["titles"] == ["Pool only paper"]
        assert run.options["pending_user_queries"] == ["laparoscopic video VLM"]
    assert client.post(f"/api/exploration/runs/{run_id}/adjust", json={}).status_code == 422


def test_adjust_requires_paused_state(client, engine, launched):
    run_id = _run(engine)
    response = client.post(f"/api/exploration/runs/{run_id}/adjust", json={"add_queries": ["more terms"]})
    assert response.status_code == 409


def test_pause_without_worker_marks_paused_then_resume_launches(client, engine, launched):
    run_id = _run(engine, status="running")
    paused = client.post(f"/api/exploration/runs/{run_id}/pause")
    assert paused.status_code == 200 and paused.json()["status"] == "paused"
    resumed = client.post(f"/api/exploration/runs/{run_id}/resume")
    assert resumed.status_code == 200 and launched == [(run_id, True)]


def test_cancel_and_export(client, engine, launched):
    run_id = _run(engine, status="paused")
    assert client.post(f"/api/exploration/runs/{run_id}/cancel").json()["status"] == "cancelled"
    md = client.get(f"/api/exploration/runs/{run_id}/report?format=markdown")
    assert md.status_code == 200 and "Answer [1]." in md.text and "Surgical VLM" in md.text
    html = client.get(f"/api/exploration/runs/{run_id}/report?format=html")
    assert html.status_code == 200 and "<h2>" in html.text
