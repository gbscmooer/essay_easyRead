"""八阶段 workflow 的修正/补充（任务书：用户对检索过程补充、修正）。"""

import asyncio

import pytest
from sqlmodel import Session, select

from backend.models.exploration import ExplorationRun


def _parent(engine, **overrides) -> int:
    papers = [
        {"key": "doi:10.1/a", "title": "Surgical VLM", "doi": "10.1/a", "abstract": "surgical video",
         "relevance_status": "confirmed", "cluster": 0, "_evidence_no": "E1"},
        {"key": "arxiv:2401.1", "title": "Endoscopy LLM", "arxiv_id": "2401.1", "abstract": "endoscopy"},
        {"key": "title:noid", "title": "Paper without ids", "abstract": "x"},
    ]
    with Session(engine) as s:
        run = ExplorationRun(question="多模态大模型用于手术视频理解", depth="quick", status="done",
                             workflow_version="legacy_v1",
                             options={"workspace_id": None, "seed_paper_ids": [], "search_online": True},
                             result={"papers": papers, "synthesis": {"verdict": {"status": "emerging"}}},
                             **overrides)
        s.add(run)
        s.commit()
        s.refresh(run)
        return run.id


@pytest.fixture
def no_worker(monkeypatch, engine):
    from backend.routers import exploration
    started = []
    monkeypatch.setattr(exploration, "get_engine", lambda: engine)

    async def fake_run(run_id, **kwargs):
        return None

    monkeypatch.setattr(exploration, "run_exploration", fake_run)
    monkeypatch.setattr(exploration, "start_worker", lambda run_id, coro: (coro.close(), started.append(run_id)))
    return started


def test_revise_creates_child_with_reuse_conditions_and_exclusions(client, engine, no_worker):
    parent_id = _parent(engine)
    response = client.post(f"/api/exploration/runs/{parent_id}/revise", json={
        "question": "多模态大模型用于腹腔镜手术视频理解", "expected_state_version": 0,
        "additional_questions": ["只看 2023 年以后", "关注公开数据集"],
        "exclude_asset_ids": ["doi:10.1/a", "title:noid"], "reuse_materials": True,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workflow_version"] == "legacy_v1" and body["status"] == "pending"
    assert body["question"] == "多模态大模型用于腹腔镜手术视频理解\n补充条件：只看 2023 年以后；关注公开数据集"
    assert no_worker == [body["id"]]
    with Session(engine) as s:
        child = s.get(ExplorationRun, body["id"])
        assert child.options["parent_run_id"] == parent_id
        assert child.options["reuse_from_run_id"] == parent_id
        assert child.options["revision_conditions"] == ["只看 2023 年以后", "关注公开数据集"]
        assert child.options["exclusions"] == {"dois": ["10.1/a"], "arxiv_ids": [], "titles": ["Paper without ids"]}

    versions = client.get(f"/api/exploration/runs/{body['id']}/versions").json()
    assert [row["id"] for row in versions["ancestors"]] == [parent_id]
    assert versions["current"]["conditions"] == ["只看 2023 年以后", "关注公开数据集"]
    parent_view = client.get(f"/api/exploration/runs/{parent_id}/versions").json()
    assert [row["id"] for row in parent_view["children"]] == [body["id"]]
    assert parent_view["current"]["verdict"] == "emerging"


def test_revise_rejects_unknown_exclusion_and_active_runs(client, engine, no_worker):
    parent_id = _parent(engine)
    bad = client.post(f"/api/exploration/runs/{parent_id}/revise", json={
        "question": "新问题", "expected_state_version": 0, "exclude_asset_ids": ["doi:missing"]})
    assert bad.status_code == 422 and bad.json()["code"] == "INVALID_REVISION"
    with Session(engine) as s:
        s.add(ExplorationRun(question="其他", status="paused", workflow_version="legacy_v1"))
        s.commit()
    busy = client.post(f"/api/exploration/runs/{parent_id}/revise", json={
        "question": "新问题", "expected_state_version": 0})
    assert busy.status_code == 409
    with Session(engine) as s:
        assert len(s.exec(select(ExplorationRun)).all()) == 2


def test_revise_offline_without_workspace_requires_reuse(client, engine, no_worker):
    parent_id = _parent(engine)
    bad = client.post(f"/api/exploration/runs/{parent_id}/revise", json={
        "question": "只用已有材料", "expected_state_version": 0, "search_online": False, "reuse_materials": False})
    assert bad.status_code == 422
    ok = client.post(f"/api/exploration/runs/{parent_id}/revise", json={
        "question": "只用已有材料", "expected_state_version": 0, "search_online": False})
    assert ok.status_code == 200
    with Session(engine) as s:
        assert s.get(ExplorationRun, ok.json()["id"]).options["search_online"] is False


def test_reuse_parent_corpus_rescreens_against_new_question(monkeypatch, engine):
    from backend.services.deep_research import explorer
    parent_id = _parent(engine)
    monkeypatch.setattr(explorer, "get_engine", lambda: engine)
    seen = {}

    async def fake_filter(papers, question, **kwargs):
        seen["question"] = question
        seen["fields"] = sorted(papers[0])
        return [dict(papers[1], relevance_status="confirmed")], {"batches": 1, "kept": 1, "rejected": 2,
                                                                 "rejections": ["x"]}

    monkeypatch.setattr("backend.services.deep_research.recall_engine._filter_pool", fake_filter)
    kept, audit = asyncio.run(explorer.reuse_parent_corpus(
        {"reuse_from_run_id": parent_id}, "腹腔镜手术视频", {"api_key": "k", "model": "m"}))
    assert seen["question"] == "腹腔镜手术视频"
    # 旧问题下的相关性判定与派生字段不得带入新问题
    assert "relevance_status" not in seen["fields"] and "cluster" not in seen["fields"]
    assert "_evidence_no" not in seen["fields"]
    assert [p["key"] for p in kept] == ["arxiv:2401.1"]
    assert audit == {"parent_run_id": parent_id, "reused_in": 3, "kept": 1, "batches": 1, "rejected": 2}
    none, skipped = asyncio.run(explorer.reuse_parent_corpus({"reuse_from_run_id": parent_id}, "q", None))
    assert none == [] and skipped["skipped"] == "no_llm"


def test_adjust_paused_run_invalidates_downstream_stages(client, engine):
    parent_id = _parent(engine)
    with Session(engine) as s:
        run = s.get(ExplorationRun, parent_id)
        run.status = "paused"
        run.current_stage = "extract"
        run.checkpoint = {"completed_stages": ["preflight", "protocol", "recall", "screen", "select"],
                          "report": {"selected_keys": ["doi:10.1/a", "arxiv:2401.1"],
                                     "extractions": {"doi:10.1/a": {"status": "examined"},
                                                     "arxiv:2401.1": {"status": "examined"}}}}
        s.add(run)
        s.commit()
    response = client.post(f"/api/exploration/runs/{parent_id}/adjust", json={
        "exclude_keys": ["doi:10.1/a"], "add_queries": ["laparoscopic video VLM"], "note": "第一篇是综述，不需要"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["completed_stages"] == ["preflight", "protocol", "recall"]
    assert body["intervention"]["invalidated_stages"] == ["screen", "select"]
    with Session(engine) as s:
        run = s.get(ExplorationRun, parent_id)
        assert run.options["exclusions"]["dois"] == ["10.1/a"]
        assert run.options["pending_user_queries"] == ["laparoscopic video VLM"]
        assert run.options["interventions"][0]["note"] == "第一篇是综述，不需要"
        # 未受影响篇目的提取缓存保留，选材清空等待重选
        assert run.checkpoint["report"] == {"extractions": {"arxiv:2401.1": {"status": "examined"}}}
    assert client.post(f"/api/exploration/runs/{parent_id}/adjust", json={}).status_code == 422


def test_adjust_requires_paused_state(client, engine):
    parent_id = _parent(engine)
    response = client.post(f"/api/exploration/runs/{parent_id}/adjust", json={"add_queries": ["more terms"]})
    assert response.status_code == 409
