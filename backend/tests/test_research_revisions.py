"""User research revisions: immutable parent, scoped materials, new admission."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from backend.models.exploration import ExplorationRun
from backend.models.research_core import (
    ResearchAsset,
    ResearchAnswerRevision,
    ResearchBudgetEvent,
    ResearchEvidence,
    ResearchQuestion,
)
from backend.models.schemas import ResearchRevisionCreate
from backend.models.paper import Paper
from backend.models.workspace import Workspace, WorkspacePaper
from backend.services.research_event_log import RunStateError
from backend.services.research_exclusions import excluded
from backend.services.deep_research.research_loop.contracts import digest, LOOP_VERSION
from backend.services.deep_research.research_loop.revisions import (
    create_revision,
    version_view,
    admit_reused_materials,
)
from backend.services.deep_research.research_loop.repository import Repository
from backend.services.deep_research.research_loop.resources import Resources, limits_for
from backend.services.deep_research.research_loop.questions import initial_questions
from backend.services.deep_research.research_loop.tools import Tools


@pytest.fixture
def parent(engine):
    payload = {
        "paper": {
            "key": "alpha",
            "title": "Alpha: Queue method",
            "abstract": "Alpha handles a bounded queue.",
            "primary_targets": ["OldTarget"],
            "relevant_questions": ["old"],
            "relevance_reason": "old judgment",
            "public_available_at": "2024-01-01T00:00:00Z",
            "cached_answer": "must not copy",
        },
        "text": "Alpha drops oldest entries when its queue is full.",
        "source_kind": "full_text",
    }
    with Session(engine) as session:
        row = ExplorationRun(
            question="原研究问题",
            status="completed",
            depth="quick",
            state_version=7,
            workflow_version="academic_survey_v2",
            result={"survey_report": {"summary": "immutable old report"}},
            options={
                "search_online": True,
                "workflow": {
                    "loop_revision": LOOP_VERSION,
                    "protocol_options": {"answer_criteria": ["old criteria"]},
                    "budget": {"llm_calls": 12},
                    "content_cutoff": "2025-12-31T23:59:59Z",
                },
            },
        )
        session.add(row)
        session.flush()
        asset = ResearchAsset(
            run_id=row.id,
            content_kind="document",
            family_key="alpha",
            inline_payload=payload,
            content_hash=digest(payload),
            parse_status="parsed",
        )
        session.add(asset)
        session.add(
            ResearchBudgetEvent(run_id=row.id, resource="llm_tokens", delta_used=1500)
        )
        session.commit()
        session.refresh(asset)
        return row.id, asset.asset_id


def request(**changes):
    return ResearchRevisionCreate(
        question="Alpha 如何处理满队列？", expected_state_version=7, **changes
    )


def test_revision_copies_only_raw_material_and_keeps_parent_report(engine, parent):
    pid, aid = parent
    with Session(engine) as session:
        before = digest(session.get(ExplorationRun, pid).result)
        child = create_revision(
            session, pid, request(additional_questions=["还需要哪些参考数据？"])
        )
        session.expire_all()
        old = session.get(ExplorationRun, pid)
        assert (
            old.status == "completed"
            and digest(old.result) == before
            and old.state_version == 8
        )
        assert child.result is None and child.protocol_asset_id is None
        assert child.options["workflow"]["budget"] == {"llm_calls": 12}
        assert "answer_criteria" not in child.options["workflow"]["protocol_options"]
        assert child.options["workflow"]["content_cutoff"].startswith("2025-12-31")
        candidates = session.exec(
            select(ResearchAsset).where(ResearchAsset.run_id == child.id)
        ).all()
        assert len(candidates) == 1 and candidates[0].content_kind == "reuse_candidate"
        paper = candidates[0].inline_payload["paper"]
        assert all(
            name not in paper
            for name in [
                "cached_answer",
                "primary_targets",
                "relevant_questions",
                "relevance_reason",
            ]
        )
        assert candidates[0].source_record["parent_asset_id"] == aid
        for model in (ResearchEvidence, ResearchAnswerRevision, ResearchBudgetEvent):
            assert not session.exec(select(model).where(model.run_id == child.id)).all()
        assert (
            version_view(session, child.id)["ancestors"][0]["usage"][0]["used"] == 1500
        )
        assert version_view(session, pid)["children"][0]["run_id"] == child.id
        with pytest.raises(RunStateError, match="版本已变化"):
            create_revision(session, pid, request())


@pytest.mark.parametrize(
    "status,owner,version,code",
    [
        ("running", False, 7, "NEEDS_PAUSE"),
        ("paused", True, 7, "WORKER_ACTIVE"),
        ("completed", False, 6, "STALE_STATE_VERSION"),
    ],
)
def test_revision_fences_running_worker_and_stale_request(
    engine, parent, status, owner, version, code
):
    with Session(engine) as session:
        row = session.get(ExplorationRun, parent[0])
        row.status = status
        if owner:
            row.worker_owner = "live"
            row.worker_lease_expires_at = datetime.now(timezone.utc) + timedelta(
                minutes=1
            )
        session.add(row)
        session.commit()
        with pytest.raises(RunStateError) as failure:
            create_revision(
                session,
                row.id,
                request().model_copy(update={"expected_state_version": version}),
            )
        assert failure.value.code == code


def test_paused_revision_releases_slot_and_other_run_rejection_is_atomic(
    engine, parent
):
    with Session(engine) as session:
        row = session.get(ExplorationRun, parent[0])
        row.status = "paused"
        # Simulate an existing pre-guard database with duplicate active runs.
        for name in ("exploration_one_active_insert", "exploration_one_active_update"):
            session.connection().exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
        other = ExplorationRun(question="other active", status="running")
        session.add(row)
        session.add(other)
        session.commit()
        with pytest.raises(RunStateError) as failure:
            create_revision(session, row.id, request())
        assert failure.value.code == "EXPLORATION_RUNNING"
        session.rollback()
        session.refresh(row)
        assert row.status == "paused" and row.state_version == 7
        session.delete(other)
        session.commit()
        child = create_revision(session, row.id, request())
        session.expire_all()
        assert session.get(ExplorationRun, row.id).status == "cancelled"
        assert child.status == "pending"
    from backend.services.database import _migrate_schema
    _migrate_schema(engine)


def test_excluded_material_cannot_be_rediscovered_and_cross_run_ids_rejected(
    engine, parent
):
    with Session(engine) as session:
        with pytest.raises(RunStateError) as failure:
            create_revision(
                session, parent[0], request(exclude_asset_ids=["foreign-asset"])
            )
        assert failure.value.code == "INVALID_MATERIAL"
        session.rollback()
        child = create_revision(
            session, parent[0], request(exclude_asset_ids=[parent[1]])
        )
        assert excluded({"title": "Alpha: Queue method"}, child.options)
        assert child.options["workflow"]["revision"]["candidate_count"] == 0


@pytest.mark.parametrize("invalid", ["quarantined", "wrong_hash"])
def test_untrusted_material_is_not_copied(engine, parent, invalid):
    with Session(engine) as session:
        asset = session.exec(
            select(ResearchAsset).where(ResearchAsset.asset_id == parent[1])
        ).one()
        if invalid == "quarantined":
            asset.status = invalid
        else:
            asset.content_hash = "bad"
        session.add(asset)
        session.commit()
        with pytest.raises(RunStateError) as failure:
            create_revision(session, parent[0], request(search_online=False))
        assert failure.value.code == "NO_MATERIALS"
        session.rollback()
        assert session.get(ExplorationRun, parent[0]).state_version == 7


def test_uploaded_material_membership_is_checked_and_snapshotted(engine, parent):
    with Session(engine) as session:
        workspace = Workspace(name="inputs")
        paper = Paper(
            filename="input.pdf",
            pdf_path="/not-read.pdf",
            file_size=10,
            title="New method",
            full_text="New method source body",
        )
        session.add(workspace)
        session.add(paper)
        session.flush()
        session.add(WorkspacePaper(workspace_id=workspace.id, paper_id=paper.id))
        session.commit()
        child = create_revision(
            session,
            parent[0],
            request(workspace_id=workspace.id, seed_paper_ids=[paper.id]),
        )
        raw = session.exec(
            select(ResearchAsset).where(ResearchAsset.run_id == child.id)
        ).all()
        assert len(raw) == 2 and any(
            a.inline_payload["text"] == paper.full_text for a in raw
        )
        assert child.options["seed_paper_ids"] == []  # no old unchecked seed shortcut


def test_materials_are_readmitted_under_new_question_and_restart_is_idempotent(
    engine, parent, monkeypatch
):
    with Session(engine) as session:
        child = create_revision(session, parent[0], request(search_online=False))
        child_id = child.id
    repo = Repository(engine, child_id, "new-worker")
    assert repo.acquire()
    limits = limits_for("quick", {"llm_calls": 12})
    repo.initialize(initial_questions(repo.run().question), limits)
    tools = Tools(repo, Resources(repo, limits), {})
    tools.save_plan({"strategy": "material_first_v3", "targets": [], "queries": []})
    calls = []

    async def model(system, data, **kwargs):
        calls.append(data)
        assert data["question"] == "Alpha 如何处理满队列？"
        assert "OldTarget" not in str(data)
        return {
            "decisions": [
                {
                    "key": "alpha",
                    "status": "relevant",
                    "role": "method",
                    "scope": "specific",
                    "reason": "new scope",
                }
            ]
        }

    monkeypatch.setattr(tools, "model", model)
    asyncio.run(admit_reused_materials(tools))
    asyncio.run(admit_reused_materials(tools))
    assert len(calls) == 1 and len(tools.documents()) == 1
    assert tools.documents()[0].inline_payload["paper"]["relevant_questions"] == [
        "main"
    ]
    assert tools.documents()[0].source_record["parent_asset_id"] == parent[1]
    assert tools.evidence("main") == []  # reading/verification still required
    repo.release()


def test_revision_api_and_cors_do_not_start_paid_worker(
    client, engine, parent, monkeypatch
):
    from backend.routers import exploration

    monkeypatch.setattr(exploration, "get_engine", lambda: engine)
    started = []
    monkeypatch.setattr(
        exploration.loop_worker, "start", lambda run_id: started.append(run_id)
    )
    origin = {"Origin": "http://localhost:5173"}
    preflight = client.options(
        f"/api/exploration/runs/{parent[0]}/revise",
        headers={
            **origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert (
        preflight.status_code == 200
        and preflight.headers["access-control-allow-origin"] == origin["Origin"]
    )
    response = client.post(
        f"/api/exploration/runs/{parent[0]}/revise",
        json=request().model_dump(),
        headers=origin,
    )
    assert (
        response.status_code == 200
        and response.headers["access-control-allow-origin"] == origin["Origin"]
    )
    assert started == [response.json()["id"]]
    versions = client.get(
        f"/api/exploration/runs/{response.json()['id']}/versions", headers=origin
    )
    assert (
        versions.status_code == 200
        and versions.json()["ancestors"][0]["run_id"] == parent[0]
    )
    assert "text" not in versions.json()["materials"]
    stale = client.post(
        f"/api/exploration/runs/{parent[0]}/revise", json=request().model_dump()
    )
    assert stale.status_code == 409 and stale.json()["code"] == "STALE_STATE_VERSION"
