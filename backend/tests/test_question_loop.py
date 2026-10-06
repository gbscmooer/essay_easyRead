"""Offline loop engineering regression; injected models and providers, no live research."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlmodel import Session

from backend.models.exploration import ExplorationRun
from backend.models.research_core import ResearchAsset, ResearchEvidence, ResearchQuestion, ResearchTask, ResearchReportRevision
from backend.services.deep_research.research_loop.contracts import Action, AtomicFact, AnswerDraft, LOOP_VERSION, digest
from backend.services.deep_research.research_loop.repository import Repository, LeaseLost
from backend.services.deep_research.research_loop.resources import Resources, ResourceExhausted, limits_for
from backend.services.deep_research.research_loop.questions import initial_questions
from backend.services.deep_research.research_loop.evidence import validated_evidence
from backend.services.deep_research.research_loop.assessment import assess_draft
from backend.services.deep_research.research_loop.tools import Tools
from backend.services.deep_research.research_loop.controller import Controller
from backend.services.deep_research.research_loop.dossier import freeze
from backend.services.deep_research.research_loop.writer import build_report, save_report, render_html
from backend.services.deep_research.research_loop import worker
from backend.services import research_event_log
from backend.eval.workflow_comparison import score_annotations, switch_gate


@pytest.fixture
def loop_env(engine):
    with Session(engine) as session:
        run = ExplorationRun(question="Kernel A 如何处理队列已满？", workflow_version="academic_survey_v2",
            state_version=1, options={"search_online": False, "workflow": {"loop_revision": "question_loop_v6"}})
        session.add(run)
        session.commit()
        run_id = run.id
    repo = Repository(engine, run_id, "worker-a")
    assert repo.acquire()
    limits = limits_for("lite")
    repo.initialize(initial_questions(repo.run().question), limits)
    tools = Tools(repo, Resources(repo, limits), {})
    corpus = json.loads((Path(__file__).parent / "evaluation/workflow/corpus.json").read_text())
    with repo.transaction() as session:
        session.add(ResearchAsset(run_id=run_id, **tools.document_spec(corpus["papers"][0])))
    return repo, tools


def test_action_dedup_ignores_reason(loop_env):
    repo, _ = loop_env
    action = Action(tool="search_papers", question_id="main", query="bounded queue", reason="first")
    one = repo.claim_task(action)
    repo.complete_task(*one, {})
    assert repo.claim_task(action.model_copy(update={"reason": "second"})) is None
    assert len(repo.rows(ResearchTask)) == 1


def test_existing_v2_evidence_migration_keeps_old_content(engine):
    from backend.services.database import _migrate_schema
    with Session(engine) as session:
        run = ExplorationRun(question="旧证据迁移", status="done")
        session.add(run)
        session.flush()
        asset = ResearchAsset(run_id=run.id, content_kind="document", content_hash="old-hash")
        session.add(asset)
        session.flush()
        evidence = ResearchEvidence(run_id=run.id, asset_id=asset.asset_id, doc_content_hash="old-hash",
                                    spans=[{"quote": "Old evidence quote"}])
        session.add(evidence)
        session.commit()
        evidence_id = evidence.evidence_id
    new_columns = ("question_ids", "fact", "evidence_kind", "source_kind", "conditions", "missing_fields", "verification")
    with engine.connect() as conn:
        for column in new_columns:
            conn.exec_driver_sql(f"ALTER TABLE research_evidence DROP COLUMN {column}")
        conn.exec_driver_sql("ALTER TABLE research_report_revisions DROP COLUMN dossier_revision_id")
        conn.commit()
    _migrate_schema(engine)
    _migrate_schema(engine)
    with Session(engine) as session:
        from sqlmodel import select
        old = session.exec(select(ResearchEvidence).where(ResearchEvidence.evidence_id == evidence_id)).one()
        assert old.spans == [{"quote": "Old evidence quote"}] and old.doc_content_hash == "old-hash"
        assert old.question_ids == [] and old.verification == {} and old.source_kind == "unknown"


def test_worker_lease_competition_and_late_write(loop_env):
    repo, _ = loop_env
    contender = Repository(repo.engine, repo.run_id, "worker-b")
    assert not contender.acquire()
    claim = repo.claim_task(Action(tool="read_document", question_id="main", asset_id="dummy"))
    with Session(repo.engine) as session:
        run = session.get(ExplorationRun, repo.run_id)
        run.worker_lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.add(run)
        session.commit()
    assert contender.acquire()
    with pytest.raises(LeaseLost):
        repo.complete_task(*claim, {})
    assert contender.rows(ResearchTask)[0].state == "pending"


def test_pause_fences_artifact_but_settles_dispatched_cost(loop_env):
    repo, tools = loop_env
    reservation = tools.resources.reserve({"llm_tokens": 100})
    claim = repo.claim_task(Action(tool="assess_answer", question_id="main"))
    with Session(repo.engine) as session:
        research_event_log.cas_run_state(session, run_id=repo.run_id, new_status="paused")
    with pytest.raises(LeaseLost):
        repo.complete_task(*claim, {"evidence": []})
    tools.resources.settle(reservation, None, known=False)
    repo.abandon_task(claim[0])
    with Session(repo.engine) as session:
        entry = next(x for x in research_event_log.budget_snapshot(session, repo.run_id) if x["resource"] == "llm_tokens")
        assert entry["used"] == 100 and entry["reserved"] == 0 and not entry["usage_known"]


def test_reservations_recovery_and_writer_reserve(loop_env):
    repo, tools = loop_env
    limits = {**tools.resources.limits, "llm_tokens": 1000, "writing_tokens": 200}
    resources = Resources(repo, limits)
    resources.reserve({"llm_tokens": 800})
    with pytest.raises(ResourceExhausted):
        resources.reserve({"llm_tokens": 1})
    resources.account = "writing"
    resources.reserve({"llm_tokens": 200})
    repo.release()
    recovered = Repository(repo.engine, repo.run_id, "worker-b")
    assert recovered.acquire()
    with Session(repo.engine) as session:
        entry = next(x for x in research_event_log.budget_snapshot(session, repo.run_id) if x["resource"] == "llm_tokens")
        assert entry["used"] == 1000 and entry["reserved"] == 0 and not entry["usage_known"]
    resources = Resources(recovered, limits)
    resources.account = "writing"
    with pytest.raises(ResourceExhausted):
        resources.reserve({"llm_tokens": 1})


def test_quote_location_is_not_semantic_support(loop_env):
    repo, tools = loop_env
    asset = tools.documents()[0]
    fact = AtomicFact(fact="Kernel A stores everything forever.",
        quote="Kernel A stores a bounded queue and discards the oldest entry when the queue is full.")
    assert validated_evidence(repo.run_id, "main", asset, fact, {"supported": False}) is None
    assert validated_evidence(repo.run_id, "main", asset, fact.model_copy(update={"quote": "Invented quote not in paper"}), {"supported": True}) is None
    asset.inline_payload = {**asset.inline_payload, "source_kind": "abstract"}
    assert validated_evidence(repo.run_id, "main", asset, fact.model_copy(update={"evidence_kind": "comparison"}), {"supported": True}) is None


def test_missing_experimental_conditions_prevents_sufficient_answer():
    question = {"answer_criteria": ["same experiment"], "expected_evidence": ["comparison"]}
    draft = AnswerDraft(conclusion="A is faster", evidence_ids=["e"], status="supported", criteria_met=["same experiment"])
    evidence = [{"evidence_id": "e", "evidence_kind": "comparison", "conditions": {"dataset": "X"}, "missing_fields": ["setting"]}]
    answer = assess_draft(question, draft, evidence, {"supported": True, "conditions_preserved": True})
    assert answer["status"] == "insufficient"
    assert any("实验设置" in gap for gap in answer["gaps"])


def mock_model(system, data, **kwargs):
    async def result():
        if "研究控制器" in system:
            return {"tool": "propose_stop", "question_id": "main", "reason": "bad early stop"}
        if "提取" in system:
            return {"facts": [{"fact": "Kernel A 在队列满时丢弃最旧的条目。", "quote": "Kernel A stores a bounded queue and discards the oldest entry when the queue is full.", "evidence_kind": "mechanism", "conditions": {}, "missing_fields": []}]}
        if "逐项检查" in system:
            return {"checks": [{"supported": True, "relevant": True, "reason": "explicit quote"}]}
        if "仅依证据" in system:
            return {"conclusion": "Kernel A 在队列满时丢弃最旧的条目。", "evidence_ids": [data["evidence"][0]["evidence_id"]], "criteria_met": data["criteria"], "status": "supported"}
        return {"supported": True, "conditions_preserved": True, "criteria_met": data.get("criteria", []), "reason": "supported", "conflicts": []}
    return result()


def test_mock_closed_loop_and_immutable_report(loop_env, monkeypatch):
    repo, tools = loop_env
    monkeypatch.setattr(tools, "model", mock_model)
    reason = asyncio.run(Controller(tools).run())
    assert reason == "required_questions_supported"
    assert repo.rows(ResearchQuestion)[0].status == "supported"
    assert len(repo.rows(ResearchEvidence)) == 1
    dossier = freeze(tools, reason)
    report = build_report(tools, dossier)
    first = save_report(tools, dossier, report)
    assert repo.run().status == "completed"
    assert "[1]" in report["chapters"][0]["text"]
    assert dossier.content_hash == digest(dossier.manifest)
    from backend.services.deep_research.research_loop.dossier import retrieve
    assert len(retrieve(dossier.manifest, "main")) == 1
    with Session(repo.engine) as session:
        research_event_log.cas_run_state(session, run_id=repo.run_id, new_status="pending")
    repo.release()
    assert repo.acquire()
    corpus = json.loads((Path(__file__).parent / "evaluation/workflow/corpus.json").read_text())
    with repo.transaction() as session:
        session.add(ResearchAsset(run_id=repo.run_id, **tools.document_spec(corpus["papers"][1])))
    regenerated = build_report(tools, dossier)
    assert regenerated["coverage"] == report["coverage"]
    assert regenerated["references"] == report["references"]
    second = save_report(tools, dossier, regenerated)
    assert repo.run().result["papers"] == report["references"]
    revisions = repo.rows(ResearchReportRevision)
    assert revisions[0].revision_id == first and revisions[1].revision_id == second
    assert not revisions[0].is_selected and revisions[1].is_selected
    assert revisions[1].dossier_revision_id == dossier.revision_id
    report["title"] = "<script>alert(1)</script>"
    assert "<script>" not in render_html(report)


def test_cutoff_and_identity_apply_to_all_documents(loop_env):
    repo, tools = loop_env
    with repo.transaction() as session:
        run = session.get(ExplorationRun, repo.run_id)
        run.options = {**run.options, "exclusions": {"titles": ["Synthetic Kernel A"]},
                       "workflow": {"content_cutoff": "2024-01-31T23:59:59Z"}}
        session.add(run)
        session.commit()
    assert not tools.documents()
    assert not tools.allowed({"key": "b", "title": "Different", "year": 2023})
    assert tools.allowed({"key": "b", "title": "Different", "publication_date": "2024-01-01"})
    assert not tools.allowed({"key": "b", "title": "Different", "publication_date": "2024-02-01"})


def test_no_progress_checkpoint_survives_restart(loop_env):
    repo, _ = loop_env
    for idx in range(6):
        claim = repo.claim_task(Action(tool="search_papers", question_id="main", query=f"query {idx}"))
        repo.complete_task(*claim, {})
    assert repo.run().checkpoint["no_progress"] == 6
    repo.release()
    assert repo.acquire()
    tools = Tools(repo, Resources(repo, limits_for("lite")), {})
    assert asyncio.run(Controller(tools).run()) == "no_progress"


def test_api_experimental_create_validation_queries_cors(client, engine, monkeypatch):
    from backend.routers import exploration
    from backend.config import settings
    monkeypatch.setattr(exploration, "get_engine", lambda: engine)
    started = []
    monkeypatch.setattr(worker, "start", lambda run_id, **kwargs: started.append(run_id))
    body = {"question": "队列恢复机制是什么？", "workflow_version": "academic_survey_v2"}
    invalid = client.post("/api/exploration/runs", json={**body, "policy_id": "not-resolved"})
    assert invalid.status_code == 422
    origin = {"Origin": settings.frontend_origin}
    preflight = client.options("/api/exploration/runs", headers={**origin, "Access-Control-Request-Method": "POST"})
    assert preflight.status_code == 200
    response = client.post("/api/exploration/runs", json=body, headers=origin)
    assert response.status_code == 200 and started == [response.json()["id"]]
    assert response.headers["access-control-allow-origin"] == settings.frontend_origin
    run_id = response.json()["id"]
    assert client.get(f"/api/exploration/runs/{run_id}/budget").status_code == 200
    repo = Repository(engine, run_id, "worker-test")
    assert repo.acquire()
    repo.initialize(initial_questions(repo.run().question), limits_for("standard"))
    base = f"/api/exploration/runs/{run_id}"
    assert client.get(base + "/questions").json()["items"][0]["required"] is True
    assert client.get(base + "/answers").json()["items"] == []
    assert client.get(base + "/dossiers").json()["items"] == []
    repo.heartbeat(12.5)
    budget = client.get(base + "/budget").json()["resources"]
    clock = next(item for item in budget if item["resource"] == "active_seconds")
    assert clock["used"] == 12 and clock["limit"] == 35 * 60 and clock["unit"] == "second"
    for suffix in ("questions", "answers", "dossiers"):
        actual = client.get(base + "/" + suffix, headers=origin)
        assert actual.status_code == 200 and actual.headers["access-control-allow-origin"] == settings.frontend_origin
        preflight = client.options(base + "/" + suffix, headers={"Origin": settings.frontend_origin, "Access-Control-Request-Method": "GET"})
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == settings.frontend_origin


def test_independent_annotation_denominators_and_switch_guard():
    score = score_annotations({"independent_review": True, "claims": [
        {"claim_id": "a", "kind": "fact", "support": "supported"},
        {"claim_id": "b", "kind": "fact", "support": "unjudgeable"}], "answered_required_units": ["u1"]}, ["u1", "u2"])
    assert score["claim_support"] == .5 and score["required_unit_coverage"] == .5
    assert not switch_gate({}, {"evaluation_kind": "mock"})["passed"]
    with pytest.raises(ValueError):
        score_annotations({"independent_review": False}, ["u1"])


def test_real_worker_dispatches_mock_tools_and_releases_lease(loop_env, monkeypatch):
    repo, _ = loop_env
    repo.release()
    monkeypatch.setattr(worker, "get_llm_config", lambda: {})
    class MockTools(Tools):
        model = staticmethod(mock_model)
    asyncio.run(worker.run(repo.run_id, engine=repo.engine, tools_factory=MockTools))
    completed = repo.run()
    assert completed.status == "completed"
    assert completed.worker_owner is None and completed.active_seconds > 0
    assert completed.result["survey_report"]["delivery"]["narrative_source"] == "frozen_answers"


def test_resource_stop_still_delivers_empty_report(engine, monkeypatch):
    with Session(engine) as session:
        row = ExplorationRun(question="证据在哪里？", workflow_version="academic_survey_v2", state_version=1,
            options={"search_online": True, "workflow": {"loop_revision": LOOP_VERSION}})
        session.add(row)
        session.commit()
        run_id = row.id
    monkeypatch.setattr(worker, "get_llm_config", lambda: {})
    class ExhaustedTools(Tools):
        async def model(self, *args, **kwargs):
            raise ResourceExhausted("fixture budget exhausted")
    asyncio.run(worker.run(run_id, engine=engine, tools_factory=ExhaustedTools))
    run = Repository(engine, run_id, "reader").run()
    assert run.status == "partial" and run.worker_owner is None
    report = run.result["survey_report"]
    assert report["status"] == "no_results" and report["method"]["stop_reason"] == "resource_limit"
    assert not report["findings"]
    repo = Repository(engine, run_id, "observer")
    assert all(q.status == "insufficient" and q.gaps for q in repo.rows(ResearchQuestion))


def test_worker_initialization_failure_releases_lease(engine):
    with Session(engine) as session:
        run = ExplorationRun(question="初始化失败恢复", workflow_version="academic_survey_v2",
            options={"workflow": {"loop_revision": LOOP_VERSION}})
        session.add(run)
        session.commit()
        run_id = run.id
    def broken(*args):
        raise ValueError("fixture initialization")
    asyncio.run(worker.run(run_id, engine=engine, tools_factory=broken))
    row = Repository(engine, run_id, "observer").run()
    assert row.status == "failed" and row.worker_owner is None and row.worker_lease_expires_at is None


def test_quarantined_dependencies_block_copied_outputs(loop_env, client, monkeypatch):
    repo, tools = loop_env
    monkeypatch.setattr(tools, "model", mock_model)
    asyncio.run(Controller(tools).run())
    dossier = freeze(tools, "required_questions_supported")
    save_report(tools, dossier, build_report(tools, dossier))
    from backend.routers import exploration
    monkeypatch.setattr(exploration, "get_engine", lambda: repo.engine)
    with Session(repo.engine) as session:
        asset = session.get(ResearchAsset, tools.documents()[0].id)
        asset.status = "quarantined"
        session.add(asset)
        session.commit()
    for suffix in ("", "/report?format=json", "/answers"):
        assert client.get(f"/api/exploration/runs/{repo.run_id}" + suffix).status_code == 403
    assert client.get(f"/api/exploration/runs/{repo.run_id}/evidence").json()["items"] == []
    assert Controller(tools).observation()["recent_evidence"] == []
    with client.stream("GET", f"/api/exploration/runs/{repo.run_id}/stream") as response:
        stream = "".join(response.iter_text())
    assert "metrics_ready" not in stream and "报告依据当前不可读" in stream


def test_known_usage_overshoot_is_not_lost(loop_env):
    repo, _ = loop_env
    resources = Resources(repo, {**limits_for("lite"), "llm_tokens": 1000, "writing_tokens": 100})
    token = resources.reserve({"llm_tokens": 100})
    resources.settle(token, {"llm_tokens": 920})
    with pytest.raises(ResourceExhausted):
        resources.reserve({"llm_tokens": 1})


def test_source_failures_are_not_reported_as_empty_success(loop_env, monkeypatch):
    repo, tools = loop_env
    with repo.transaction() as session:
        run = session.get(ExplorationRun, repo.run_id)
        run.options = {**run.options, "search_online": True}
        session.add(run)
    from backend.services.deep_research import recall_engine
    async def failed(*args, **kwargs):
        raise TimeoutError("fixture timeout")
    monkeypatch.setattr(recall_engine, "_default_search_one", failed)
    result = asyncio.run(tools.search(Action(tool="search_papers", question_id="main", query="bounded queue")))
    audit = result["assets"][-1]["inline_payload"]
    assert len(audit["attempts"]) == 3
    assert all(row["status"] == "error" and row["hits"] == 0 for row in audit["attempts"])


def test_source_budget_stop_keeps_search_audit(loop_env, monkeypatch):
    repo, tools = loop_env
    with repo.transaction() as session:
        run = session.get(ExplorationRun, repo.run_id)
        run.options = {**run.options, "search_online": True}
        session.add(run)
    from backend.services.deep_research import recall_engine
    async def exhausted(*args, **kwargs):
        raise ResourceExhausted("fixture source quota")
    monkeypatch.setattr(recall_engine, "_default_search_one", exhausted)
    result = asyncio.run(tools.search(Action(tool="search_papers", question_id="main", query="bounded queue")))
    assert tools.resources.source_exhausted
    assert result["assets"][-1]["inline_payload"]["attempts"][0]["status"] == "resource_exhausted"


def test_specific_evidence_requirement_prevents_sufficient_answer():
    question = {"answer_criteria": ["explain limitations"], "expected_evidence": ["limitation"]}
    draft = AnswerDraft(conclusion="A has limitations", evidence_ids=["e"], status="supported", criteria_met=question["answer_criteria"])
    answer = assess_draft(question, draft, [{"evidence_id": "e", "evidence_kind": "fact"}],
                          {"supported": True, "conditions_preserved": True})
    assert answer["status"] == "insufficient" and any("limitation" in gap for gap in answer["gaps"])


def test_retry_does_not_requeue_while_worker_is_closing(loop_env, client, monkeypatch):
    from backend.routers import exploration
    repo, _ = loop_env
    monkeypatch.setattr(exploration, "get_engine", lambda: repo.engine)
    claim = repo.claim_task(Action(tool="assess_answer", question_id="main"))
    repo.complete_task(*claim, {"error_code": "MODEL_ERROR"})
    with Session(repo.engine) as session:
        research_event_log.cas_run_state(session, run_id=repo.run_id, new_status="cancelled")
    response = client.post(f"/api/exploration/runs/{repo.run_id}/retry", json={"failed_only": True})
    assert response.status_code == 409 and response.json()["code"] == "WORKER_ACTIVE"
    assert repo.run().status == "cancelled"
    repo.release()
    started = []
    def start(run_id):
        assert asyncio.get_running_loop()
        started.append(run_id)
    monkeypatch.setattr(worker, "start", start)
    response = client.post(f"/api/exploration/runs/{repo.run_id}/retry", json={"failed_only": True})
    assert response.status_code == 200 and started == [repo.run_id]
    assert repo.rows(ResearchTask)[0].state == "pending"


def test_remove_requires_stopped_worker_and_cleans_ledger(loop_env, client, monkeypatch):
    from backend.routers import exploration
    from backend.models.research_core import ResearchEvent
    repo, _ = loop_env
    monkeypatch.setattr(exploration, "get_engine", lambda: repo.engine)
    base = f"/api/exploration/runs/{repo.run_id}"
    assert client.post(base + "/remove").status_code == 409
    with Session(repo.engine) as session:
        research_event_log.cas_run_state(session, run_id=repo.run_id, new_status="cancelled")
    repo.release()
    assert client.post(base + "/remove").status_code == 200
    assert client.get(base).status_code == 404
    assert not repo.rows(ResearchAsset) and not repo.rows(ResearchQuestion) and not repo.rows(ResearchEvent)


def test_structured_output_disables_reasoning_and_repairs_once(loop_env, monkeypatch):
    from backend.services import llm
    from backend.models.research_core import ResearchEvent
    from backend.services.deep_research.research_loop.contracts import AnswerVerdict
    repo, tools = loop_env
    tools.config = {"thinking_effort": "medium"}
    calls = []
    async def response(config, **kwargs):
        calls.append(kwargs)
        kwargs["metadata_out"]["finish_reason"] = "stop"
        return {"supported": True, "conditions_preserved": True, "reason": "ok",
                "criteria_met": None if len(calls) == 1 else []}
    monkeypatch.setattr(llm, "complete_json", response)
    raw = asyncio.run(tools.model("check", {}, schema=AnswerVerdict, stage="answer_check"))
    assert raw["criteria_met"] == [] and len(calls) == 2
    assert all(c["thinking_effort"] == "off" and c["max_retries"] == 0 for c in calls)
    assert tools.config["thinking_effort"] == "medium"
    event = next(e for e in repo.rows(ResearchEvent) if e.type == "structured_output_error")
    assert event.payload["fields"] == [{"path": "criteria_met", "type": "list_type"}]
    assert "input" not in json.dumps(event.payload)


def test_truncated_json_not_accepted_even_when_parseable(loop_env, monkeypatch):
    from backend.services import llm
    repo, tools = loop_env
    calls = []
    async def response(config, **kwargs):
        calls.append(kwargs)
        kwargs["metadata_out"]["finish_reason"] = "length"
        return {"facts": []}
    monkeypatch.setattr(llm, "complete_json", response)
    with pytest.raises(ValueError):
        asyncio.run(tools.model("extract", {}))
    assert len(calls) == 2


def test_compact_search_plan_rejects_raw_chinese_and_long_and_query():
    from backend.services.deep_research.research_loop.contracts import QuerySpec
    for text in ("缺陷检测图像任务研究进展如何", "image defect detection deep learning survey research progress future directions"):
        with pytest.raises(ValueError):
            QuerySpec(question_id="main", query=text)
    assert QuerySpec(question_id="main", query="visual defect detection survey").query == "visual defect detection survey"


def test_relevance_admission_backfills_abstract_and_excludes_unrelated(loop_env, monkeypatch):
    repo, tools = loop_env
    with repo.transaction() as session:
        run = session.get(ExplorationRun, repo.run_id)
        run.options = {**run.options, 'search_online': True}
        session.add(run)
    from backend.services.deep_research import recall_engine
    calls = []
    async def backfill(dois):
        calls.append(dois)
        return {"https://doi.org/10.1234/GOOD": "This paper describes a bounded queue."}
    monkeypatch.setattr(recall_engine, "_default_backfill", backfill)
    async def classify(system, data, **kwargs):
        return {"decisions": [
            {"key": "good", "status": "relevant", "role": "survey", "reason": "queue"},
            {"key": "bad", "status": "unrelated", "role": "other", "reason": "psychology"}]}
    monkeypatch.setattr(tools, "model", classify)
    accepted, decisions, enrichment = asyncio.run(tools.admit([
        {"key": "good", "title": "Queue survey", "doi": "10.1234/good"},
        {"key": "bad", "title": "Psychological capital", "abstract": "psychology"}], "main"))
    assert [p["key"] for p in accepted] == ["good"]
    assert accepted[0]["abstract"] and calls == [["10.1234/good"]]
    assert enrichment[0]["filled"] == 1 and decisions[1]["status"] == "unrelated"


def test_missing_or_duplicate_relevance_decisions_fail_closed(loop_env, monkeypatch):
    _, tools = loop_env
    async def classify(*args, **kwargs):
        return {"decisions": [{"key": "one", "status": "relevant", "role": "other", "reason": "a"}] * 2}
    monkeypatch.setattr(tools, "model", classify)
    accepted, decisions, _ = asyncio.run(tools.admit([
        {"key": "one", "title": "Queue one", "abstract": "text"},
        {"key": "two", "title": "Queue two", "abstract": "text"}], "main"))
    assert accepted == [] and all(d["status"] == "uncertain" for d in decisions)


def test_scheduler_reads_material_before_download_without_controller_model(loop_env, monkeypatch):
    _, tools = loop_env
    def forbidden(*args, **kwargs):
        raise AssertionError("mechanical controller must not call model")
    monkeypatch.setattr(tools, "model", forbidden)
    action = asyncio.run(Controller(tools).choose())
    assert action.tool == "read_document"
    asset = tools.documents()[0]
    with tools.repo.transaction() as session:
        empty = ResearchAsset(run_id=tools.repo.run_id, **tools.document_spec(
            {"key": "empty", "title": "Title only"}))
        session.add(empty)
        session.flush()
    assert not Controller(tools).available(Action(tool="read_document", question_id="main", asset_id=empty.asset_id))
    assert action.asset_id == asset.asset_id


def test_answer_reserve_is_used_for_synthesis_not_deterministic_writer(loop_env):
    _, tools = loop_env
    resources = tools.resources
    assert resources.limits["writing_calls"] == resources.limits["writing_tokens"] == 0
    resources.charge("llm_calls", 13)
    with pytest.raises(ResourceExhausted):
        resources.before_model_attempt(estimated_output_tokens=100, estimated_input_tokens=100)
    resources.account = "answer"
    resources.before_model_attempt(estimated_output_tokens=100, estimated_input_tokens=100)
    resources.after_model_attempt(usage={"input_tokens": 100, "output_tokens": 100},
        reserved_output_tokens=100, reserved_input_tokens=100, usage_known=True)
    assert resources.remaining("llm_calls") == 1


def test_valid_extraction_survives_one_malformed_fact(loop_env, monkeypatch):
    repo, tools = loop_env
    async def extraction(system, data, **kwargs):
        if "提取" in system:
            return {"facts": [
                {"fact": "bad", "quote": "too short", "conditions": None},
                {"fact": "Kernel A 在队列满时丢弃最旧条目。",
                 "quote": "Kernel A stores a bounded queue and discards the oldest entry when the queue is full.",
                 "evidence_kind": "mechanism"}]}
        return {"checks": [{"supported": True, "reason": "explicit"}]}
    monkeypatch.setattr(tools, "model", extraction)
    result = asyncio.run(tools.read(Action(tool="read_document", question_id="main", asset_id=tools.documents()[0].asset_id)))
    assert len(result["evidence"]) == 1


def test_report_references_only_answer_sources_and_counts_readable_material(loop_env, monkeypatch):
    repo, tools = loop_env
    monkeypatch.setattr(tools, "model", mock_model)
    asyncio.run(Controller(tools).run())
    with repo.transaction() as session:
        session.add(ResearchAsset(run_id=repo.run_id, **tools.document_spec({"key": "unused", "title": "No abstract"})))
    dossier = freeze(tools, "required_questions_supported")
    report = build_report(tools, dossier)
    assert len(report["references"]) == 1 and len(report["materials"]) == 2
    assert report["coverage"]["corpus"] == 2 and report["coverage"]["with_material"] == 1
    assert report["coverage"]["used"] == report["coverage"]["examined"] == 1


def test_independent_check_can_reject_self_reported_criteria():
    question = {"answer_criteria": ["evolution", "current directions"]}
    draft = AnswerDraft(conclusion="One mechanism", evidence_ids=["ev_one"],
                        status="supported", criteria_met=question["answer_criteria"])
    verdict = {"supported": True, "conditions_preserved": True, "criteria_met": ["evolution"]}
    result = assess_draft(question, draft, [{"evidence_id": "ev_one", "evidence_kind": "fact"}], verdict)
    assert result["status"] == "insufficient" and "current directions" in result["gaps"]
    draft.conclusion = "One mechanism【ev_unknown】"
    result = assess_draft(question, draft, [{"evidence_id": "ev_one", "evidence_kind": "fact"}], verdict)
    assert result["conclusion"] == ""


def test_http_exhaustion_still_allows_local_answers(loop_env, monkeypatch):
    repo, tools = loop_env
    monkeypatch.setattr(tools, "model", mock_model)
    tools.resources.source_exhausted = True
    assert asyncio.run(Controller(tools).run()) == "required_questions_supported"
    assert repo.rows(ResearchQuestion)[0].status == "supported"


def test_old_loop_remains_readable_and_preserves_frozen_budget(loop_env):
    repo, _ = loop_env
    with repo.transaction() as session:
        run = session.get(ExplorationRun, repo.run_id)
        run.options = {"workflow": {"loop_revision": "question_loop_v1"}}
        session.add(run)
    assert worker.supported(repo.run())
    assert repo.protocol()["budget"] == limits_for("lite")


def test_same_normalized_title_does_not_use_two_reading_slots(loop_env, monkeypatch):
    repo, tools = loop_env
    original = tools.documents()[0]
    claim = repo.claim_task(Action(tool="read_document", question_id="main", asset_id=original.asset_id, query=repo.run().question))
    repo.complete_task(*claim, {})
    paper = {**original.inline_payload["paper"], "key": "alternate-version", "title": "SYNTHETIC KERNEL A"}
    with repo.transaction() as session:
        alternate = ResearchAsset(run_id=repo.run_id, **tools.document_spec(paper, text=original.inline_payload["text"], source_kind="full_text"))
        session.add(alternate)
        session.flush()
    assert not Controller(tools).available(Action(tool="read_document", question_id="main", asset_id=alternate.asset_id))
    # A full text can still add evidence after the abstract.
    with repo.transaction() as session:
        source = session.get(ResearchAsset, original.id)
        source.inline_payload = {**source.inline_payload, "source_kind": "abstract"}
        session.add(source)
    assert Controller(tools).available(Action(tool="read_document", question_id="main", asset_id=alternate.asset_id))


def test_supplemental_search_returns_to_reading_before_next_query(loop_env, monkeypatch):
    repo, tools = loop_env
    monkeypatch.setattr(tools, "model", mock_model)
    original = tools.documents()[0]
    claim = repo.claim_task(Action(tool="read_document", question_id="main", asset_id=original.asset_id, query=repo.run().question))
    repo.complete_task(*claim, asyncio.run(tools.read(Action(tool="read_document", question_id="main", asset_id=original.asset_id))))
    claim = repo.claim_task(Controller(tools).assessment_action("main"))
    answer = asyncio.run(tools.assess(Controller(tools).assessment_action("main")))
    answer["answer"]["status"], answer["answer"]["gaps"] = "insufficient", ["need another method"]
    repo.complete_task(*claim, answer)
    tools.save_plan({"queries": [{"question_id": "main", "query": "second query"},
                                  {"question_id": "main", "query": "third query"}], "answer_units": ["method"]})
    with repo.transaction() as session:
        run = session.get(ExplorationRun, repo.run_id)
        run.options = {**run.options, "search_online": True}
        session.add(run)
    action = Controller(tools).fallback()
    assert action.tool == "search_papers" and action.query == "second query"
    claim = repo.claim_task(action)
    repo.complete_task(*claim, {"assets": [tools.document_spec({"key": "new", "title": "New method", "abstract": "A new bounded queue method."})]})
    assert Controller(tools).fallback().tool == "read_document"


def test_search_plan_enforces_two_queries_per_question():
    from backend.services.deep_research.research_loop.contracts import SearchPlan
    with pytest.raises(ValueError):
        SearchPlan(queries=[{"question_id": "main", "query": "valid query " + str(n)} for n in range(3)], answer_units=["research evolution"])


def test_fulltext_tries_doi_alternative_after_stale_provider_link(loop_env, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from backend.services import paper_download, pdf_parser
    from backend.services.deep_research import pdf_downloader
    repo, tools = loop_env
    paper = {"key": "doi:10.1234/test", "doi": "10.1234/test", "title": "Bounded Queue Study",
             "abstract": "The study discusses queue limits.", "pdf_url": "https://publisher.invalid/old.pdf"}
    with repo.transaction() as session:
        asset = ResearchAsset(run_id=repo.run_id, **tools.document_spec(paper))
        session.add(asset)
        session.flush()
    path = tmp_path / "temporary.pdf"
    path.write_bytes(b"fake PDF")
    calls = []
    async def download(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(path=str(path) if "open.example" in url else None)
    async def alternate(doi):
        return "https://open.example/paper.pdf"
    monkeypatch.setattr(pdf_downloader, "download_pdf_detailed", download)
    monkeypatch.setattr(paper_download, "_resolve_openalex_by_doi", alternate)
    monkeypatch.setattr(pdf_parser, "extract_pdf_content", lambda p: SimpleNamespace(full_text="Bounded Queue Study\nComplete supported text."))
    result = asyncio.run(tools.fetch(Action(tool="fetch_document", question_id="main", asset_id=asset.asset_id)))
    assert calls == [paper["pdf_url"], "https://open.example/paper.pdf"]
    assert result["assets"][0]["inline_payload"]["source_kind"] == "full_text"
    assert not path.exists()


def test_rejected_synthesis_delivers_only_checked_atomic_facts(loop_env, monkeypatch):
    repo, tools = loop_env
    monkeypatch.setattr(tools, "model", mock_model)
    action = Action(tool="read_document", question_id="main", asset_id=tools.documents()[0].asset_id)
    claim = repo.claim_task(action)
    repo.complete_task(*claim, asyncio.run(tools.read(action)))
    async def rejected(system, data, **kwargs):
        if "仅依证据" in system:
            return {"conclusion": "All systems store unlimited data forever.", "status": "supported",
                    "evidence_ids": [data["evidence"][0]["evidence_id"]], "criteria_met": data["criteria"]}
        return {"supported": False, "conditions_preserved": False, "criteria_met": [], "reason": "invented generalization", "conflicts": []}
    monkeypatch.setattr(tools, "model", rejected)
    result = asyncio.run(tools.assess(Controller(tools).assessment_action("main")))["answer"]
    assert result["status"] == "insufficient" and "unlimited" not in result["conclusion"]
    assert "丢弃最旧" in result["conclusion"]
    assert result["assessment"]["semantic_check"]["supported"] is False
    assert result["assessment"]["partial_projection"] == "checked_atomic_facts_v1"


def test_frozen_rejected_answer_projects_facts_without_upgrading_status(loop_env, monkeypatch):
    repo, tools = loop_env
    monkeypatch.setattr(tools, "model", mock_model)
    asyncio.run(Controller(tools).run())
    dossier = freeze(tools, "resource_limit")
    dossier.manifest["answers"][0].update(conclusion="", status="insufficient", conditions=["invented condition"])
    report = build_report(tools, dossier)
    assert report["status"] == "partial" and report["quality_status"] == "needs_review"
    assert "已核验的要点" in report["chapters"][0]["text"]
    assert "invented condition" not in report["chapters"][0]["text"]
    assert "[1]" in report["chapters"][0]["text"]


def test_abstract_gap_fetches_once_then_reads_new_fulltext(loop_env, monkeypatch):
    repo, tools = loop_env
    original = tools.documents()[0]
    with repo.transaction() as session:
        asset = session.get(ResearchAsset, original.id)
        paper = {**asset.inline_payload["paper"], "pdf_url": "https://open.example/queue.pdf"}
        asset.inline_payload = {**asset.inline_payload, "source_kind": "abstract", "paper": paper}
        asset.content_hash = digest(asset.inline_payload)
        session.add(asset)
        run = session.get(ExplorationRun, repo.run_id)
        run.options = {**run.options, "search_online": True}
        session.add(run)
    monkeypatch.setattr(tools, "model", mock_model)
    action = Action(tool="read_document", question_id="main", asset_id=original.asset_id, query=repo.run().question)
    claim = repo.claim_task(action)
    repo.complete_task(*claim, asyncio.run(tools.read(action)))
    claim = repo.claim_task(Controller(tools).assessment_action("main"))
    answer = asyncio.run(tools.assess(Controller(tools).assessment_action("main")))
    answer["answer"].update(status="insufficient", gaps=["need full text"])
    repo.complete_task(*claim, answer)
    tools.save_plan({"queries": [{"question_id": "main", "query": "another search"}], "answer_units": ["method"]})
    next_action = Controller(tools).fallback()
    assert next_action.tool == "fetch_document"
    claim = repo.claim_task(next_action)
    repo.complete_task(*claim, {"assets": [tools.document_spec(paper, text=original.inline_payload["text"], source_kind="full_text")]})
    assert Controller(tools).fallback().tool == "read_document"


def test_missing_coverage_is_not_a_literature_conflict():
    question = {"answer_criteria": ["explain method"]}
    draft = AnswerDraft(conclusion="A mechanism", evidence_ids=["ev_one"], status="supported", criteria_met=question["answer_criteria"])
    result = assess_draft(question, draft, [{"evidence_id": "ev_one", "evidence_kind": "fact"}],
        {"supported": True, "conditions_preserved": True, "conflicts": ["No performance comparison"]})
    assert result["status"] == "insufficient" and not result["conflicts"]
    evidence = [{"evidence_id": eid, "evidence_kind": "fact"} for eid in ("ev_one", "ev_two")]
    result = assess_draft(question, draft, evidence,
        {"supported": True, "conditions_preserved": True, "conflicts": ["Conflict between ev_one and ev_two"]})
    assert result["status"] == "conflicted"


def test_faithful_but_uninformative_fact_does_not_become_answer_evidence(loop_env):
    repo, tools = loop_env
    asset = tools.documents()[0]
    fact = AtomicFact(fact="The paper discusses queues.",
        quote="Kernel A stores a bounded queue and discards the oldest entry when the queue is full.")
    assert validated_evidence(repo.run_id, "main", asset, fact, {"supported": True, "relevant": False}) is None


def test_v3_keeps_frozen_action_keys_and_batch_membership(loop_env):
    repo, _ = loop_env
    action = Action(tool='read_document', question_id='main', asset_id='one')
    expected = digest({'protocol_hash': repo.protocol()['hash'], 'tool': 'read_document',
                       'question_id': 'main', 'query': '', 'asset_id': 'one'})
    assert action.key(repo.protocol()['hash']) == expected
    batch = Action(tool='read_documents', question_id='main', asset_ids=['one', 'two'])
    assert batch.key('p') == batch.model_copy(update={'asset_ids': ['two', 'one']}).key('p')
    assert batch.key('p') != batch.model_copy(update={'asset_ids': ['one', 'three']}).key('p')


def test_batch_read_rejects_borrowed_quotes_and_unknown_sources(loop_env, monkeypatch):
    repo, tools = loop_env
    first = tools.documents()[0]
    text = 'Queue B blocks the producer until a slot becomes available.'
    with repo.transaction() as session:
        second = ResearchAsset(run_id=repo.run_id, **tools.document_spec(
            {'key': 'b', 'title': 'Queue B', 'abstract': text}))
        session.add(second)
        session.flush()
    calls = []
    async def batch_model(system, data, **kwargs):
        calls.append(data)
        if '提取' in system:
            return {'facts': [
                {'asset_id': second.asset_id, 'fact': 'B等待空位', 'quote': text, 'evidence_kind': 'mechanism'},
                {'asset_id': first.asset_id, 'fact': 'A等待空位', 'quote': text, 'evidence_kind': 'mechanism'},
                {'asset_id': 'unknown', 'fact': '未知来源', 'quote': text}]}
        return {'checks': [{'supported': True, 'relevant': True, 'reason': 'directly stated'}]}
    monkeypatch.setattr(tools, 'model', batch_model)
    result = asyncio.run(tools.read_batch(Action(tool='read_documents', question_id='main',
                        asset_ids=[first.asset_id, second.asset_id])))
    assert len(calls) == 2 and len(calls[1]['facts']) == 1
    assert len(result['evidence']) == 1 and result['evidence'][0]['asset_id'] == second.asset_id
    claim = repo.claim_task(Action(tool='read_documents', question_id='main', asset_ids=[first.asset_id, second.asset_id]))
    repo.complete_task(*claim, result)
    assert not Controller(tools).available(Action(tool='read_document', question_id='main', asset_id=second.asset_id))
    dossier = freeze(tools, 'resource_limit')
    assert set(dossier.manifest['examined_assets']) == {first.asset_id, second.asset_id}


def test_batch_read_cannot_repeat_titles_or_empty_material(loop_env):
    repo, tools = loop_env
    original = tools.documents()[0]
    with repo.transaction() as session:
        duplicate = ResearchAsset(run_id=repo.run_id, **tools.document_spec(
            {**original.inline_payload['paper'], 'key': 'different-id'}, text=original.inline_payload['text']))
        empty = ResearchAsset(run_id=repo.run_id, **tools.document_spec({'key': 'empty', 'title': 'Empty'}))
        session.add(duplicate)
        session.add(empty)
        session.flush()
    controller = Controller(tools)
    assert not controller.available(Action(tool='read_documents', question_id='main', asset_ids=[original.asset_id, duplicate.asset_id]))
    assert not controller.available(Action(tool='read_documents', question_id='main', asset_ids=[original.asset_id, empty.asset_id]))


def test_material_first_collects_before_synthesis_and_reads_in_batches(loop_env):
    from backend.services.deep_research.research_loop.scheduling import material_first
    repo, tools = loop_env
    with repo.transaction() as session:
        run = session.get(ExplorationRun, repo.run_id)
        run.depth = 'lite'
        run.options = {**run.options, 'search_online': True}
        session.add(run)
        for n in range(5):
            session.add(ResearchAsset(run_id=repo.run_id, **tools.document_spec(
                {'key': str(n), 'title': f'Source {n}', 'abstract': f'Source {n} directly explains queue management.'})))
    tools.save_plan({'strategy': 'material_first_v1', 'queries': [{'question_id': 'main', 'query': 'queue management'},
                      {'question_id': 'main', 'query': 'bounded buffer'}], 'answer_units': ['机制']})
    controller = Controller(tools)
    for query in ('queue management', 'bounded buffer'):
        action = material_first(controller)
        assert action.tool == 'search_papers' and action.query == query
        claim = repo.claim_task(action)
        repo.complete_task(*claim, {})
    action = material_first(controller)
    assert action.tool == 'read_documents' and len(action.asset_ids) == 3
    claim = repo.claim_task(action)
    repo.complete_task(*claim, {})
    next_action = material_first(controller)
    assert next_action.tool == 'read_documents' and not set(next_action.asset_ids) & set(action.asset_ids)


def test_english_material_queries_include_body_directions(loop_env):
    repo, tools = loop_env
    asset = tools.documents()[0]
    body = '\n\n'.join(['Abstract\nThis paper studies bounded queues.'] +
         [f'Unrelated appendix {n}\nAuxiliary implementation data.' for n in range(18)] +
         ['Discussion\nFuture directions include adaptive queue admission and dynamic buffer limits.'])
    asset.inline_payload = {**asset.inline_payload, 'text': body}
    tools.save_plan({'queries': [{'question_id': 'main', 'query': 'bounded queues'}]})
    excerpt = tools.reading_material(asset, repo.rows(ResearchQuestion)[0])
    assert 'adaptive queue admission' in excerpt


def test_pdf_page_windows_retrieve_body_instead_of_introduction():
    from backend.services.deep_research.research_loop.material import body_material, paper_description
    intro = ('This survey reviews research methods and discusses future directions. ' * 70)
    text = '\n\n'.join([intro,
        '--- Page 2 ---\n3 TAXONOMY\nFeature based methods use a normal memory bank. ' + 'Implementation context. ' * 130,
        '--- Page 3 ---\n4 METHODS\nNamedMethod stores local feature vectors and detects deviations. ' + 'Technical context. ' * 120,
        '--- Page 4 ---\n5 CHALLENGES\nFuture directions concern missing training examples and domain change. ' + 'Discussion context. ' * 120,
        'REFERENCES\n' + 'Feature taxonomy references without task facts. ' * 80])
    material = body_material('doc', text, ['defect detection'], max_chars=12000)
    assert 'NamedMethod stores local feature vectors' in material
    assert 'Feature based methods use a normal memory bank' in material
    assert 'Future directions concern missing training examples' in material
    assert 'references without task facts' not in material
    assert len(material) <= 12000
    assert paper_description('该综述详细介绍了十二种方法。')
    assert not paper_description('特征方法使用正常样本的记忆库检测异常。')


def test_uncited_opening_cannot_bypass_supported_body():
    question = {'answer_criteria': ['mechanism']}
    opening = 'All research has replaced the original algorithm with a newer universal system. ' * 2
    draft = AnswerDraft(conclusion=opening + '\n\nA drops the oldest entry【ev_one】',
        evidence_ids=['ev_one'], status='supported', criteria_met=['mechanism'])
    result = assess_draft(question, draft, [{'evidence_id': 'ev_one', 'evidence_kind': 'fact'}],
        {'supported': True, 'conditions_preserved': True, 'criteria_met': ['mechanism']})
    assert not result['conclusion'] and result['status'] == 'insufficient'
    assert '答案包含未绑定证据的事实段落' in result['gaps']


def test_old_report_regeneration_keeps_its_workflow_identity(loop_env, monkeypatch):
    repo, tools = loop_env
    with repo.transaction() as session:
        protocol = session.get(ResearchAsset, repo.asset(repo.run().protocol_asset_id).id)
        protocol.inline_payload = {**protocol.inline_payload, 'execution_revision': 'question_loop_v2'}
        session.add(protocol)
    monkeypatch.setattr(tools, 'model', mock_model)
    asyncio.run(Controller(tools).run())
    dossier = freeze(tools, 'required_questions_supported')
    report = build_report(tools, dossier)
    assert report['workflow_revision'] == 'question_loop_v2'
    save_report(tools, dossier, report)
    assert repo.run().result['loop']['revision'] == 'question_loop_v2'


def test_paragraph_support_preserves_good_parts_and_structured_citations():
    from backend.services.deep_research.research_loop.answering import checked_paragraphs, project_paragraphs
    raw = {'paragraphs': [
        {'criterion_index': 0, 'title': 'Mechanism', 'text': 'A drops the oldest entry.', 'evidence_ids': ['ev_one']},
        {'criterion_index': 0, 'title': 'Unfounded timeline', 'text': 'All systems replaced A.', 'evidence_ids': ['ev_one']},
        {'criterion_index': 0, 'title': 'Unknown', 'text': 'A is best.', 'evidence_ids': ['ev_unknown']}]}
    checks = [{'supported': True, 'relevant': True}, {'supported': False, 'relevant': True}, {'supported': True, 'relevant': True}]
    accepted = checked_paragraphs(raw, checks, {'ev_one': {}}, ['mechanism'])
    text, ids = project_paragraphs(accepted)
    assert len(accepted) == 1 and ids == ['ev_one']
    assert '【ev_one】' in text and 'replaced' not in text and 'best' not in text


def test_v3_answer_checks_support_and_coverage_separately(loop_env, monkeypatch):
    repo, tools = loop_env
    monkeypatch.setattr(tools, 'model', mock_model)
    read = Action(tool='read_document', question_id='main', asset_id=tools.documents()[0].asset_id)
    claim = repo.claim_task(read)
    repo.complete_task(*claim, asyncio.run(tools.read(read)))
    tools.save_plan({'strategy': 'material_first_v2', 'queries': [], 'answer_units': ['机制']})
    eid = tools.evidence('main')[0]['evidence_id']
    stages = []
    async def answers(system, data, **kwargs):
        stages.append(kwargs['stage'])
        if kwargs['stage'] == 'answer':
            return {'paragraphs': [{'criterion_index': 0, 'title': '机制', 'text': 'Kernel A 队列满时丢弃最旧的条目。', 'evidence_ids': [eid]}], 'conditions': []}
        if kwargs['stage'] == 'answer_check':
            assert 'criteria' not in data
            return {'checks': [{'supported': True, 'relevant': True, 'reason': 'direct quote'}], 'conditions_preserved': True, 'conflicts': []}
        assert 'proofs' not in data
        return {'met_indices': [], 'gaps': ['缺少另一必答内容']}
    monkeypatch.setattr(tools, 'model', answers)
    result = asyncio.run(tools.assess(Controller(tools).assessment_action('main')))['answer']
    assert stages == ['answer', 'answer_check', 'answer_coverage']
    assert result['conclusion'] and '【' + eid + '】' in result['conclusion']
    assert result['status'] == 'insufficient' and result['assessment']['semantic_check']['supported'] is True


def test_v4_named_technique_requires_name_in_supporting_span(loop_env):
    repo, tools = loop_env
    asset = tools.documents()[0]
    fact = AtomicFact(fact='CNN uses normal memory.', quote=asset.inline_payload['text'])
    assert validated_evidence(repo.run_id, 'main', asset, fact,
        {'supported': True, 'relevant': True}, reviewer_version='question_loop_v4') is None
    from backend.services.deep_research.research_loop.material import paper_description
    assert paper_description('该综述将方法归纳为五类但未给类别内容。')
    assert paper_description('算法细节在讨论传统算法之后才展开讨论。')
    assert not paper_description('PatchCore 使用正常特征记忆库检测偏差。')


def test_v5_own_method_alias_does_not_license_unrelated_techniques(loop_env):
    repo, tools = loop_env
    asset = tools.documents()[0]
    quote = 'Our method stores a bounded queue and discards the oldest entry when full.'
    asset.inline_payload = {'source_kind': 'full_text', 'text': quote,
                            'paper': {'title': 'AlphaNet: Bounded queues', 'primary_targets': ['AlphaNet']}}
    fact = AtomicFact(fact='AlphaNet 在队列满时丢弃最旧条目。', quote=quote)
    supported = {'supported': True, 'relevant': True}
    assert validated_evidence(repo.run_id, 'main', asset, fact, supported, reviewer_version='question_loop_v5')
    assert not validated_evidence(repo.run_id, 'main', asset, fact, supported, reviewer_version='question_loop_v4')
    assert not validated_evidence(repo.run_id, 'main', asset,
        fact.model_copy(update={'fact': 'AlphaNet 使用 CNN 队列。'}), supported, reviewer_version='question_loop_v5')
    asset.inline_payload['paper']['title'] = 'Unrelated method'
    assert not validated_evidence(repo.run_id, 'main', asset, fact, supported, reviewer_version='question_loop_v5')
    asset.inline_payload = {'source_kind': 'full_text',
        'text': 'We can introduce a learnable object-aware prompt as an alternative design.',
        'paper': {'title': 'AlphaNet: An object-agnostic model', 'primary_targets': ['AlphaNet']}}
    alternative = AtomicFact(fact='AlphaNet 使用物体感知提示。', quote=asset.inline_payload['text'])
    assert not validated_evidence(repo.run_id, 'main', asset, alternative, supported, reviewer_version='question_loop_v5')


def test_anchored_reading_binds_exact_quote_and_rejects_unknown_passage(loop_env, monkeypatch):
    repo, tools = loop_env
    tools.save_plan({'strategy': 'material_first_v3', 'queries': [], 'reading_queries': []})
    asset = tools.documents()[0]
    stages = []
    selected = {}
    async def model(system, data, **kwargs):
        stages.append(kwargs['stage'])
        if kwargs['stage'] == 'extraction_batch':
            material = data['materials'][0]
            anchor = material['passages'][0]
            assert 'quote' not in anchor and anchor['text'] in asset.inline_payload['text']
            selected['quote'] = anchor['text']
            return {'facts': [
                {'asset_id': asset.asset_id, 'passage_id': anchor['passage_id'], 'fact': 'Kernel A 在队列满时丢弃最旧条目。'},
                {'asset_id': asset.asset_id, 'passage_id': 'unknown', 'fact': 'Kernel A 永久保留全部条目。'}]}
        assert len(data['facts']) == 1 and data['passages'][0]['quote'] == selected['quote']
        assert data['document_overviews'][0]['asset_id'] == asset.asset_id
        assert data['document_overviews'][0]['overview'] == asset.inline_payload['text'][:2300]
        assert '[QUOTE]' in data['passages'][0]['adjacent_context']
        assert kwargs.get('thinking_effort', 'off') == 'off' and kwargs['max_tokens'] == 2000
        return {'checks': [{'supported': True, 'relevant': True, 'reason': 'bounded queue'}]}
    monkeypatch.setattr(tools, 'model', model)
    result = asyncio.run(tools.read_batch(Action(tool='read_documents', question_id='main', asset_ids=[asset.asset_id])))
    assert stages == ['extraction_batch', 'evidence_check_batch']
    assert len(result['evidence']) == 1
    assert result['evidence'][0]['spans'][0]['quote'] == selected['quote']
    assert result['usage']['extraction_diagnostics']['invalid_anchor'] == 1


def test_paragraph_global_condition_rejection_cannot_become_supported(loop_env, monkeypatch):
    repo, tools = loop_env
    monkeypatch.setattr(tools, 'model', mock_model)
    action = Action(tool='read_document', question_id='main', asset_id=tools.documents()[0].asset_id)
    claim = repo.claim_task(action)
    repo.complete_task(*claim, asyncio.run(tools.read(action)))
    tools.save_plan({'strategy': 'material_first_v3', 'queries': [], 'answer_units': ['机制']})
    eid = tools.evidence('main')[0]['evidence_id']
    async def model(system, data, **kwargs):
        if kwargs['stage'] == 'answer':
            return {'paragraphs': [{'criterion_index': 0, 'title': '机制', 'text': 'Kernel A 丢弃最旧条目。', 'evidence_ids': [eid]}], 'conditions': ['Invented guarantee']}
        if kwargs['stage'] == 'answer_check':
            return {'checks': [{'supported': True, 'relevant': True}], 'conditions_preserved': False, 'conflicts': []}
        return {'met_indices': list(range(len(data['criteria']))), 'gaps': []}
    monkeypatch.setattr(tools, 'model', model)
    answer = asyncio.run(tools.assess(Controller(tools).assessment_action('main')))['answer']
    assert answer['status'] == 'insufficient'
    assert any('全局范围或条件' in gap for gap in answer['gaps'])


def test_one_document_check_exhaustion_keeps_other_checked_facts(loop_env, monkeypatch):
    repo, tools = loop_env
    first = tools.documents()[0]
    spec = tools.document_spec({'key': 'second-source', 'title': 'Second source',
        'abstract': 'Kernel A discards the oldest entry when its bounded queue is full.'})
    with repo.transaction() as session:
        session.add(ResearchAsset(run_id=repo.run_id, **spec))
    second = next(a for a in tools.documents() if a.asset_id != first.asset_id)
    tools.save_plan({'strategy': 'material_first_v3', 'queries': []})
    async def model(system, data, **kwargs):
        if kwargs['stage'] == 'extraction_batch':
            return {'facts': [{'asset_id': m['asset_id'], 'passage_id': m['passages'][0]['passage_id'],
                'fact': 'Kernel A 在队列满时丢弃最旧条目。'} for m in data['materials']]}
        if data['document_overviews'][0]['asset_id'] == second.asset_id:
            raise ResourceExhausted('answer reserve')
        return {'checks': [{'supported': True, 'relevant': True}]}
    monkeypatch.setattr(tools, 'model', model)
    result = asyncio.run(tools.read_batch(Action(tool='read_documents', question_id='main', asset_ids=[first.asset_id, second.asset_id])))
    assert len(result['evidence']) == 1 and result['evidence'][0]['asset_id'] == first.asset_id
    assert result['usage']['examined_assets'] == [first.asset_id]
    assert result['usage']['extraction_diagnostics']['unchecked_assets'][0]['asset_id'] == second.asset_id


def test_default_policy_is_program_checked_after_domain_coverage(loop_env, monkeypatch):
    repo, tools = loop_env
    monkeypatch.setattr(tools, 'model', mock_model)
    action = Action(tool='read_document', question_id='main', asset_id=tools.documents()[0].asset_id)
    claim = repo.claim_task(action)
    repo.complete_task(*claim, asyncio.run(tools.read(action)))
    generic = repo.rows(ResearchQuestion)[0].answer_criteria[0]
    with repo.transaction() as session:
        q = session.get(ResearchQuestion, repo.rows(ResearchQuestion)[0].id)
        q.answer_criteria = ['解释机制', generic]
        session.add(q)
    tools.save_plan({'strategy': 'material_first_v2', 'queries': [], 'answer_units': ['解释机制']})
    eid = tools.evidence('main')[0]['evidence_id']
    async def model(system, data, **kwargs):
        if kwargs['stage'] == 'answer':
            return {'paragraphs': [{'criterion_index': 0, 'title': '机制', 'text': 'Kernel A 队列满时丢弃最旧条目。', 'evidence_ids': [eid]}], 'conditions': []}
        if kwargs['stage'] == 'answer_check':
            return {'checks': [{'supported': True, 'relevant': True}], 'conditions_preserved': True, 'conflicts': []}
        return {'met_indices': [0], 'gaps': []}
    monkeypatch.setattr(tools, 'model', model)
    answer = asyncio.run(tools.assess(Controller(tools).assessment_action('main')))['answer']
    assert answer['status'] == 'supported' and answer['assessment']['default_policy_checked']


def test_declared_unanswered_requirement_overrides_false_positive_coverage(loop_env, monkeypatch):
    repo, tools = loop_env
    monkeypatch.setattr(tools, 'model', mock_model)
    action = Action(tool='read_document', question_id='main', asset_id=tools.documents()[0].asset_id)
    repo.complete_task(*repo.claim_task(action), asyncio.run(tools.read(action)))
    with repo.transaction() as session:
        q = session.get(ResearchQuestion, repo.rows(ResearchQuestion)[0].id)
        q.answer_criteria = ['说明队列满的处理及是否更新参数']
        session.add(q)
    tools.save_plan({'strategy': 'material_first_v3', 'queries': [], 'feedback_policy': 'gap_followup_v2'})
    eid = tools.evidence('main')[0]['evidence_id']
    async def model(system, data, **kwargs):
        if kwargs['stage'] == 'answer':
            assert '当前提取证据未覆盖不等于论文未说明' in system
            return {'paragraphs': [{'criterion_index': 0, 'title': '队列机制',
                'text': 'Kernel A 队列满时丢弃最旧条目；参数更新条件当前提取证据未覆盖。', 'evidence_ids': [eid]}],
                'conditions': [], 'unresolved_requests': [{'criterion_index': 0, 'missing_requirement': 'Kernel A 是否更新参数尚未回答'}]}
        if kwargs['stage'] == 'answer_check':
            assert '仅在顶层' in system
            return {'checks': [{'supported': True, 'relevant': True}], 'conditions_preserved': True, 'conflicts': []}
        assert data['unresolved_requests'][0]['criterion_index'] == 0
        return {'met_indices': [0], 'gaps': [], 'next_steps': []}
    monkeypatch.setattr(tools, 'model', model)
    answer = asyncio.run(tools.assess(Controller(tools).assessment_action('main')))['answer']
    assert answer['status'] == 'insufficient'
    assert 'Kernel A 是否更新参数尚未回答' in answer['gaps']
    assert answer['assessment']['criteria_met'] == []
    assert answer['assessment']['coverage']['met_indices'] == []


def test_focused_plan_cannot_invent_named_targets(loop_env, monkeypatch):
    repo, tools = loop_env
    async def model(*args, **kwargs):
        return {'queries': [{'question_id': 'main', 'query': 'queue management'}], 'answer_units': ['机制'],
                'targets': [{'question_id': 'main', 'name': 'InventedModel'}], 'reading_queries': ['training inference']}
    monkeypatch.setattr(tools, 'model', model)
    with pytest.raises(ValueError, match='用户明确点名'):
        asyncio.run(tools.make_plan(initial_questions(repo.run().question)))


def test_non_english_reading_navigation_falls_back_to_technical_roles(loop_env, monkeypatch):
    repo, tools = loop_env
    async def model(*args, **kwargs):
        return {'queries': [{'question_id': 'main', 'query': 'bounded queue'}],
                'answer_units': ['队列机制'], 'targets': [], 'reading_queries': ['训练测试条件', '参考样本']}
    monkeypatch.setattr(tools, 'model', model)
    plan = asyncio.run(tools.make_plan(initial_questions(repo.run().question)))
    assert len(plan['reading_queries']) == 4
    assert all(' ' in q and q.isascii() for q in plan['reading_queries'])


def test_unique_named_primary_identity_skips_repeated_model_screening(loop_env, monkeypatch):
    _, tools = loop_env
    tools.save_plan({'strategy': 'material_first_v3', 'targets': [{'question_id': 'main', 'name': 'AlphaNet'}]})
    async def unexpected(*args, **kwargs):
        raise AssertionError('unambiguous primary identity needs no model screening')
    monkeypatch.setattr(tools, 'model', unexpected)
    papers = [{'key': 'primary', 'title': 'AlphaNet: A bounded queue', 'abstract': 'Our method stores a queue.'},
        {'key': 'related', 'title': 'AlphaNet-3D: Another model', 'abstract': 'We compare with AlphaNet.'}]
    admitted, decisions, _ = asyncio.run(tools.admit(papers, 'main'))
    assert [p['key'] for p in admitted] == ['primary']
    assert admitted[0]['primary_targets'] == ['AlphaNet']
    assert decisions[1]['status'] == 'uncertain'


def test_workflow_deepseek_low_effort_is_explicit_and_stage_local(loop_env, monkeypatch):
    _, tools = loop_env
    tools.config = {'provider': 'custom', 'api_format': 'openai_chat', 'model': 'deepseek-flash', 'api_key': 'test-key'}
    requests = []
    async def complete(config, **kwargs):
        requests.append(kwargs)
        return {'value': 'ok'}
    monkeypatch.setattr('backend.services.llm.complete_json', complete)
    asyncio.run(tools.model('check', {}, thinking_effort='low'))
    asyncio.run(tools.model('ordinary', {}))
    assert requests[0]['extra_body'] == {'reasoning_effort': 'low'}
    assert requests[1]['extra_body'] is None and requests[1]['thinking_effort'] == 'off'
    assert 'thinking_effort' not in tools.config


def test_focused_body_navigation_prioritizes_own_method_section():
    from backend.services.deep_research.research_loop.material import body_material
    intro = '1. Introduction\n' + ('normal training inference background ' * 100) + '\n\n'
    method = '2. AlphaNet\nOur model uses frozen features and learned projection layers.\n\n'
    discussion = '3. Experiments\n' + ('normal training inference evaluation ' * 100)
    passages = body_material('asset', intro + method + discussion, [], focus_queries=['training frozen projection'],
        primary_targets=['AlphaNet'], return_passages=True, max_chars=2200)
    assert any('learned projection layers' in p['text'] for p in passages)
    assert all(p['text'] in intro + method + discussion for p in passages)
    expanded = body_material('asset', intro + method + discussion, [], focus_queries=['training frozen projection'],
        primary_targets=['AlphaNet'], return_passages=True, max_chars=8000)
    positions = [(int(p['passage_id'][2:]), len(p['text'])) for p in expanded]
    assert all(start + length <= positions[i + 1][0] for i, (start, length) in enumerate(positions[:-1]))


def test_named_targets_are_separate_queries_not_an_and_expression(loop_env):
    from backend.services.deep_research.research_loop.scheduling import material_first
    repo, tools = loop_env
    with repo.transaction() as session:
        run = session.get(ExplorationRun, repo.run_id)
        run.options = {**run.options, 'search_online': True}
        session.add(run)
        for name in ('AlphaNet', 'BetaNet'):
            session.add(ResearchAsset(run_id=repo.run_id, **tools.document_spec(
                {'key': name, 'title': name, 'abstract': 'Uses normal references.'})))
    tools.save_plan({'strategy': 'material_first_v3', 'targets': [{'question_id': 'main', 'name': n} for n in ('AlphaNet', 'BetaNet')],
                     'queries': [{'question_id': 'main', 'query': 'queue management'}]})
    controller = Controller(tools)
    for name in ('AlphaNet', 'BetaNet'):
        action = material_first(controller)
        assert action.tool == 'search_papers' and action.query == name
        claim = repo.claim_task(action)
        repo.complete_task(*claim, {'assets': [tools.document_spec({'key': name, 'title': name,
            'abstract': 'Uses normal references.', 'primary_targets': [name]})]})
    action = material_first(controller)
    assert action.tool == 'read_documents'  # Both primary targets exist; broad search is unnecessary.


def _arxiv_feed(version='2301.12345v2', updated='2024-01-02T00:00:00Z', title='AlphaNet'):
    return f'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <id>http://arxiv.org/abs/{version}</id><title>{title}</title>
      <published>2023-01-01T00:00:00Z</published><updated>{updated}</updated>
      <summary>AlphaNet uses reference images.</summary><author><name>A. Author</name></author>
      <link title="pdf" href="http://arxiv.org/pdf/{version}"/></entry></feed>'''


def test_arxiv_version_dates_are_bound_to_their_own_entry():
    from backend.services.deep_research.research_loop.retrieval import dated_entries, before_cutoff
    import xml.etree.ElementTree as ET
    first, second = ET.fromstring(_arxiv_feed()), ET.fromstring(_arxiv_feed('2302.54321v3', '2026-01-01T00:00:00Z', 'BetaNet'))
    first.append(list(second)[0])
    papers = dated_entries(ET.tostring(first, encoding='unicode'))
    assert papers[0]['arxiv_version'] == '2301.12345v2'
    assert papers[1]['title'] == 'BetaNet' and papers[1]['public_available_at'].startswith('2026')
    assert papers[0]['pdf_url'].endswith('2301.12345v2')
    assert before_cutoff(papers[0]['public_available_at'], '2025-12-31')
    assert not before_cutoff(papers[1]['public_available_at'], '2025-12-31')
    assert not before_cutoff('unknown', '2025-12-31')


def test_cutoff_download_uses_attested_old_version_not_latest(loop_env, monkeypatch):
    from backend.services.deep_research.research_loop import retrieval
    queries = []
    async def request(query):
        queries.append(query)
        return _arxiv_feed('2301.12345v1', '2023-01-01T00:00:00Z') if 'id_list' in query else _arxiv_feed(updated='2026-01-01T00:00:00Z')
    monkeypatch.setattr(retrieval.arxiv_client, '_arxiv_request', request)
    papers = asyncio.run(retrieval.search_arxiv('AlphaNet', cutoff='2025-12-31'))
    assert len(queries) == 2 and papers[0]['arxiv_version'].endswith('v1')
    assert papers[0]['pdf_url'].endswith('v1')
    repo, tools = loop_env
    with repo.transaction() as session:
        run = session.get(ExplorationRun, repo.run_id)
        run.options = {**run.options, 'workflow': {**run.options['workflow'], 'content_cutoff': '2025-12-31'}}
        session.add(run)
        asset = ResearchAsset(run_id=repo.run_id, **tools.document_spec(papers[0]))
        session.add(asset)
    from backend.services.deep_research import pdf_downloader as download_module
    from types import SimpleNamespace
    urls = []
    async def download(url, **kwargs):
        urls.append(url)
        return SimpleNamespace(path=None)
    monkeypatch.setattr(download_module, 'download_pdf_detailed', download)
    asyncio.run(tools.fetch(Action(tool='fetch_document', question_id='main', asset_id=asset.asset_id)))
    assert urls == ['https://arxiv.org/pdf/2301.12345v1']


def test_cutoff_unattested_version_cannot_download(loop_env, monkeypatch):
    repo, tools = loop_env
    from backend.services.deep_research.research_loop import retrieval
    with repo.transaction() as session:
        run = session.get(ExplorationRun, repo.run_id)
        run.options = {**run.options, 'workflow': {**run.options['workflow'], 'content_cutoff': '2025-12-31'}}
        session.add(run)
        asset = ResearchAsset(run_id=repo.run_id, **tools.document_spec({'key': 'arxiv:2301.12345', 'title': 'AlphaNet',
            'arxiv_id': '2301.12345', 'arxiv_version': '2301.12345v2', 'publication_date': '2024-01-01', 'abstract': 'Reference images.'}))
        session.add(asset)
    async def metadata(version):
        return None
    monkeypatch.setattr(retrieval, 'version_metadata', metadata)
    result = asyncio.run(tools.fetch(Action(tool='fetch_document', question_id='main', asset_id=asset.asset_id)))
    assert result['error_code'] == 'VERSION_DATE_UNKNOWN'


def test_inference_projection_is_explicit_and_cited():
    from backend.services.deep_research.research_loop.answering import project_paragraphs
    text, ids = project_paragraphs([{'title': '选择', 'text': '有正常参考时可验证该方法。', 'kind': 'inference', 'evidence_ids': ['ev_a']}])
    assert '依据材料的推断' in text and '【ev_a】' in text and ids == ['ev_a']


def prepare_gap_case(repo, tools, *, policy="gap_followup_v1", extra_steps=()):
    from backend.services.deep_research.research_loop.feedback import freeze_followups
    tools.save_plan({'strategy': 'material_first_v3', 'feedback_policy': policy,
                     'queries': [], 'reading_queries': ['training frozen parameters']})
    original = tools.documents()[0]
    quote = 'Kernel A stores a bounded queue and discards the oldest entry when the queue is full.'
    with repo.transaction() as session:
        asset = session.get(ResearchAsset, original.id)
        asset.inline_payload = {**asset.inline_payload, 'source_kind': 'full_text',
            'text': quote + '\n' + ('Background filler about unrelated literature. ' * 200) +
                    '\nImplementation details\nKernel A freezes all encoder parameters and builds a reference queue from normal target images.\n' +
                    ('Unrelated descriptions. ' * 100)}
        asset.content_hash = digest(asset.inline_payload)
        session.add(asset)
    asset = repo.asset(original.asset_id)
    fact = AtomicFact(fact='Kernel A 在队列满时丢弃最旧条目。', quote=quote)
    ev = validated_evidence(repo.run_id, 'main', asset, fact, {'supported': True, 'relevant': True})
    read = Action(tool='read_documents', question_id='main', asset_ids=[asset.asset_id], query=repo.run().question[:500])
    repo.complete_task(*repo.claim_task(read), {'evidence': [ev]})
    assessment = Controller(tools).assessment_action('main')
    answer = assess_draft(repo.rows(ResearchQuestion)[0].model_dump(),
        AnswerDraft(conclusion=fact.fact + '【' + ev['evidence_id'] + '】', evidence_ids=[ev['evidence_id']],
                    gaps=['需要训练条件'], status='insufficient'), [ev], {'supported': True, 'conditions_preserved': True})
    proposed = [{'tool': 'read_documents', 'asset_ids': [asset.asset_id], 'query': 'training frozen parameters', 'reason': '补足训练条件'}]
    assets = freeze_followups(tools, assessment, answer, [*proposed, *extra_steps])
    repo.complete_task(*repo.claim_task(assessment), {'answer': answer, 'assets': assets})
    return asset, read, assets[0]['inline_payload']['steps'][0]


def test_gap_followup_reads_same_paper_new_passage_and_revises(loop_env, monkeypatch):
    repo, tools = loop_env
    asset, initial, step = prepare_gap_case(repo, tools)
    controller = Controller(tools)
    assert not controller.available(initial)
    followup = asyncio.run(controller.choose())
    assert followup.gap_key and followup.model_dump() == step
    seen = []
    async def model(system, data, **kwargs):
        seen.append(kwargs['stage'])
        if kwargs['stage'] == 'extraction_batch':
            assert data['remaining_gaps']
            passages = data['materials'][0]['passages']
            quote = next(p for p in passages if 'freezes all encoder' in p['text'])
            assert not any(tools.evidence('main')[0]['spans'][0]['quote'] in p['text'] for p in passages)
            return {'facts': [{'asset_id': asset.asset_id, 'passage_id': quote['passage_id'],
                              'fact': 'Kernel A 冻结编码器参数并用正常目标图片建立参考队列。'}]}
        if kwargs['stage'] == 'evidence_check_batch':
            return {'checks': [{'supported': True, 'relevant': True, 'reason': 'explicit frozen parameters'}]}
        if kwargs['stage'] == 'answer':
            eid = next(f['evidence_id'] for f in data['facts'] if '冻结' in f['fact'])
            return {'paragraphs': [{'criterion_index': 0, 'title': '机制与条件',
                'text': 'Kernel A 冻结编码器参数并用正常目标图片建立参考队列。', 'evidence_ids': [eid]}], 'conditions': []}
        if kwargs['stage'] == 'answer_check':
            return {'checks': [{'supported': True, 'relevant': True, 'reason': 'explicit'}], 'conditions_preserved': True, 'conflicts': []}
        return {'met_indices': [0], 'gaps': [], 'next_steps': []}
    monkeypatch.setattr(tools, 'model', model)
    assert asyncio.run(controller.run()) == 'required_questions_supported'
    assert repo.rows(ResearchQuestion)[0].status == 'supported'
    assert len(tools.evidence('main')) == 2
    assert seen == ['extraction_batch', 'evidence_check_batch', 'answer', 'answer_check', 'answer_coverage']
    assert not controller.available(followup)


def test_gap_followup_cannot_bypass_dedup_or_frozen_plan(loop_env):
    repo, tools = loop_env
    _, _, spec = prepare_gap_case(repo, tools)
    action = Action.model_validate(spec)
    controller = Controller(tools)
    assert controller.available(action)
    assert not controller.available(action.model_copy(update={'focus_queries': ['unrelated topic']}))
    assert not controller.available(action.model_copy(update={'gap_key': 'made-up'}))
    assert not controller.available(action.model_copy(update={'asset_ids': ['unknown']}))
    repo.complete_task(*repo.claim_task(action), {'evidence': []})
    assert not controller.available(action)
    assert asyncio.run(controller.choose()) is None


def test_gap_rounds_are_bounded_and_unknown_material_rejected(loop_env):
    from backend.services.deep_research.research_loop.feedback import freeze_followups, plans
    repo, tools = loop_env
    _, _, spec = prepare_gap_case(repo, tools)
    action = Controller(tools).assessment_action('main')
    answer = {'status': 'insufficient', 'gaps': ['need training'], 'assessment': {}}
    unknown = [{'tool': 'read_documents', 'asset_ids': ['unknown'], 'query': 'training frozen parameters', 'reason': 'need training'}]
    frozen = freeze_followups(tools, action, answer, unknown)
    assert frozen[0]['inline_payload']['steps'] == []
    with repo.transaction() as session:
        session.add(ResearchAsset(run_id=repo.run_id, **frozen[0]))
    assert len(plans(tools, 'main')) == 2
    assert freeze_followups(tools, action, answer, [spec]) == []
    assert answer['assessment']['followup_stop_reason'] == 'gap_round_limit'


def test_frozen_action_key_preserved_with_new_optional_fields():
    old_payload = {'tool': 'read_documents', 'question_id': 'main', 'query': 'mechanism', 'asset_id': '', 'asset_ids': ['b', 'a']}
    key = digest({'protocol_hash': 'original', **{**old_payload, 'asset_ids': ['a', 'b']}})
    assert Action(**old_payload).key('original') == key
    assert Action(**old_payload, focus_queries=['training frozen parameters']).key('original') != key


def test_gap_round_reads_both_sources_before_reassessment_and_survives_recovery(loop_env):
    repo, tools = loop_env
    text = 'Kernel B builds its reference queue from normal target images without updating network parameters.'
    with repo.transaction() as session:
        second = ResearchAsset(run_id=repo.run_id, **tools.document_spec(
            {'key': 'second', 'title': 'Synthetic Kernel B'}, text=text, source_kind='full_text'))
        session.add(second)
        session.flush()
    extra = {'tool': 'read_documents', 'asset_ids': [second.asset_id], 'query': 'reference queue parameter training', 'reason': '补足B条件'}
    first, _, step = prepare_gap_case(repo, tools, policy='gap_followup_v2', extra_steps=[extra])
    controller = Controller(tools)
    first_read = asyncio.run(controller.choose())
    assert first_read.asset_ids == [first.asset_id]
    q = 'Kernel A freezes all encoder parameters and builds a reference queue from normal target images.'
    fact = AtomicFact(fact='Kernel A 冻结参数并建立正常参考队列。', quote=q)
    ev = validated_evidence(repo.run_id, 'main', first, fact, {'supported': True, 'relevant': True})
    repo.complete_task(*repo.claim_task(first_read), {'evidence': [ev]})
    # Reconstruct from persisted state: new evidence does not abandon B's pending step.
    recovered = Controller(Tools(repo, tools.resources, {}))
    second_read = asyncio.run(recovered.choose())
    assert second_read.tool == 'read_documents' and second_read.asset_ids == [second.asset_id]
    assert second_read.gap_key == step['gap_key']
    second_fact = AtomicFact(fact='Kernel B 用正常目标图片建队列，不更新参数。', quote=text)
    second_ev = validated_evidence(repo.run_id, 'main', second, second_fact, {'supported': True, 'relevant': True})
    repo.complete_task(*repo.claim_task(second_read), {'evidence': [second_ev]})
    next_action = asyncio.run(recovered.choose())
    assert next_action.tool == 'assess_answer'
    assert next_action.query == digest(sorted(e['evidence_id'] for e in tools.evidence('main')))
    # Advancing the answer makes the old plan stale even if one forges a different key.
    answer = {'status': 'insufficient', 'conclusion': '', 'kind': 'fact', 'evidence_ids': [],
              'conditions': [], 'conflicts': [], 'gaps': ['new gap'], 'assessment': {}}
    repo.complete_task(*repo.claim_task(next_action), {'answer': answer})
    assert not recovered.available(second_read)


def test_gap_round_can_finish_after_one_step_fails(loop_env):
    repo, tools = loop_env
    with repo.transaction() as session:
        second = ResearchAsset(run_id=repo.run_id, **tools.document_spec(
            {'key': 'second', 'title': 'Second method'}, text='Second method builds a reference queue.', source_kind='full_text'))
        session.add(second)
        session.flush()
    extra = {'tool': 'read_documents', 'asset_ids': [second.asset_id], 'query': 'reference queue training', 'reason': '补足B条件'}
    first, _, _ = prepare_gap_case(repo, tools, policy='gap_followup_v2', extra_steps=[extra])
    controller = Controller(tools)
    action = asyncio.run(controller.choose())
    assert action.asset_ids == [first.asset_id]
    repo.complete_task(*repo.claim_task(action), {'error_code': 'MODEL_FAILED', 'error_detail': 'synthetic failure'})
    assert asyncio.run(controller.choose()).asset_ids == [second.asset_id]


def test_gap_budget_respects_source_count_and_answer_reserve(loop_env):
    from backend.services.deep_research.research_loop.feedback import can_execute
    _, tools = loop_env
    tools.resources.charge('llm_calls', 10)
    one = Action(tool='read_documents', question_id='main', asset_ids=['one'])
    two = one.model_copy(update={'asset_ids': ['one', 'two']})
    assert can_execute(tools.resources, one)
    assert not can_execute(tools.resources, two)
    tools.resources.charge('llm_tokens', 38000)
    assert not can_execute(tools.resources, one)


def test_answer_support_deduplicates_quotes_without_borrowing_sources():
    from backend.services.deep_research.research_loop.answering import support_materials
    base = {'asset_id': 'source-one', 'doc_content_hash': 'hash-one', 'fact': 'condition',
            'spans': [{'quote': 'The parameters are frozen.'}], 'source_kind': 'full_text', 'conditions': {}}
    known = {'ev_a': base, 'ev_b': {**base, 'fact': 'mechanism'},
             'ev_c': {**base, 'asset_id': 'source-two', 'doc_content_hash': 'hash-two'}}
    papers = {e: {'title': 'Paper ' + known[e]['asset_id']} for e in known}
    proofs, passages = support_materials(set(known), known, papers)
    assert len(passages) == 2 and len(proofs) == 3
    lookup = {p['evidence_id']: p for p in proofs}
    assert lookup['ev_a']['passage_ids'] == lookup['ev_b']['passage_ids']
    assert lookup['ev_c']['passage_ids'] != lookup['ev_a']['passage_ids']
    assert all('quote' not in proof for proof in proofs)
    assert {p['asset_id'] for p in passages} == {'source-one', 'source-two'}


def test_sampler_preserves_complete_abstract_after_long_author_list():
    from backend.services.deep_research.research_loop.material import body_material
    text = 'Authors\n' + 'Long affiliation information. ' * 60 + '\nABSTRACT\n' + ('Technical abstract content. ' * 30) + \
           'We propose AlphaNet using auxiliary training, without target samples.\n1\nINTRODUCTION\n' + ('Background. ' * 300)
    passages = body_material('a', text, ['anomaly detection'], focus_queries=['training target samples'],
                             max_chars=5000, return_passages=True)
    assert any(p['text'].startswith('ABSTRACT') and 'without target samples' in p['text'] for p in passages)
    assert sum(len(p['text']) + 2 for p in passages) <= 5000
