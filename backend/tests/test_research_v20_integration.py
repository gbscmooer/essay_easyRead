"""Offline v20 dispatch, frozen envelopes and real API projections."""

import asyncio
from copy import deepcopy

import pytest
from sqlmodel import Session

from backend.config import settings
from backend.models.exploration import ExplorationRun
from backend.models.research_core import ResearchAsset, ResearchEvent
from backend.services import llm
from backend.services.deep_research.research_loop import worker
from backend.services.deep_research.research_loop.contracts import LOOP_VERSION, digest
from backend.services.deep_research.research_loop.controller import Controller
from backend.services.deep_research.research_loop.intent_v20 import IntentDraft
from backend.services.deep_research.research_loop.questions import initial_questions
from backend.services.deep_research.research_loop.repository import Repository
from backend.services.deep_research.research_loop.resources import Resources, limits_for
from backend.services.deep_research.research_loop.tools import Tools
from backend.tests.test_research_planning_v20 import OfflinePlanner, QUESTION


def environment(engine, revision="question_loop_v20", frozen=None):
    with Session(engine) as session:
        row = ExplorationRun(question=QUESTION, depth="quick", workflow_version="academic_survey_v2", state_version=1,
            options={"search_online": False, "workflow": {"loop_revision": revision}})
        session.add(row)
        session.commit()
        rid = row.id
    repo = Repository(engine, rid, "v20-integration")
    assert repo.acquire()
    specs = initial_questions(QUESTION)
    budget = frozen or limits_for("quick", revision=revision)
    repo.initialize(specs, budget)
    tools = Tools(repo, Resources(repo, deepcopy(budget)), {
        "provider": "custom", "api_format": "openai_chat", "base_url": "https://example.invalid/v1",
        "api_key": "offline-stub-key", "model": "offline-model",
    })
    return repo, tools, specs


@pytest.mark.parametrize("depth", ["lite", "quick"])
def test_v20_new_envelope_is_explicit_and_v19_defaults_remain_frozen(depth):
    current = limits_for(depth, revision="question_loop_v20")
    historical = limits_for(depth, revision="question_loop_v19")
    assert LOOP_VERSION == "question_loop_v20"
    if depth == "quick":
        assert historical == {"llm_tokens": 240000, "llm_calls": 45, "http_requests": 60,
            "actions": 44, "wall_clock_minutes": 30, "writing_tokens": 0, "writing_calls": 0}
        assert current == {"llm_tokens": 360000, "llm_calls": 65, "http_requests": 90,
            "actions": 64, "wall_clock_minutes": 35, "writing_tokens": 0, "writing_calls": 0}
    else:
        assert historical["llm_tokens"] == 120000 and historical["llm_calls"] == 25
        assert current["llm_tokens"] == 240000 and current["llm_calls"] == 45
    assert limits_for(depth, revision="question_loop_v18")["llm_tokens"] < historical["llm_tokens"]
    assert limits_for(depth, revision="question_loop_v19") == historical


def test_reinitialization_never_replaces_a_frozen_old_budget_with_current_defaults(engine):
    frozen = limits_for("quick", {"llm_tokens": 77777, "llm_calls": 9}, revision="question_loop_v19")
    repo, _, specs = environment(engine, "question_loop_v19", frozen)
    original = deepcopy(repo.protocol())
    repo.initialize(specs, limits_for("quick", revision="question_loop_v20"))
    assert repo.protocol() == original
    assert repo.protocol()["budget"] == frozen


def test_resuming_worker_uses_saved_v19_budget_even_if_latest_defaults_are_larger(engine, monkeypatch):
    frozen = limits_for("quick", {"llm_tokens": 77777, "llm_calls": 9}, revision="question_loop_v19")
    repo, _, _ = environment(engine, "question_loop_v19", frozen)
    protocol_before = deepcopy(repo.protocol())
    repo.release()
    seen = []

    async def inspect(self):
        seen.append(deepcopy(self.resources.limits))
        assert self.resources.limits == frozen
        return "no_actionable_gap"

    async def forbid_paid(*args, **kwargs):
        raise AssertionError("offline resume must not call a model")

    monkeypatch.setattr(worker, "get_llm_config", lambda: {})
    monkeypatch.setattr(Controller, "run", inspect)
    monkeypatch.setattr(llm, "complete_json", forbid_paid)
    asyncio.run(worker.run(repo.run_id, engine=engine))
    assert seen == [frozen]
    assert repo.protocol() == protocol_before
    assert repo.run().worker_owner is None


@pytest.mark.parametrize("revision,precise", [("question_loop_v20", True), ("question_loop_v19", False)])
def test_tools_schema_recovery_gives_v20_exact_missing_path_and_preserves_old_feedback(engine, monkeypatch, revision, precise):
    repo, tools, _ = environment(engine, revision)
    captured = []
    clause = {"clause_id": "C1", "kind": "request", "requests": ["解释队列如何保持顺序"], "scope": [], "targets": []}

    async def complete(config, **kwargs):
        captured.append(deepcopy({key: value for key, value in kwargs.items() if key != "metadata_out"}))
        kwargs["metadata_out"]["finish_reason"] = "stop"
        if len(captured) == 1:
            omitted = deepcopy(clause)
            del omitted["targets"]
            return {"clauses": [omitted]}
        return {"clauses": [clause]}

    monkeypatch.setattr(llm, "complete_json", complete)
    data = {"source_clauses": [{"clause_id": "C1", "source_quote": QUESTION}]}
    result = asyncio.run(tools.model("仅保留原句需求", data, schema=IntentDraft, stage="planning", max_tokens=4000))
    assert result == {"clauses": [clause]}
    assert len(captured) == 2 and captured[0]["user"] == captured[1]["user"]
    feedback = captured[1]["system"][len(captured[0]["system"]):]
    assert '"path": "clauses.0.targets"' in feedback if precise else '"path"' not in feedback
    if precise:
        assert '"type": "missing"' in feedback
    assert all(call["max_retries"] == 0 for call in captured)
    diagnostics = [row.payload for row in repo.rows(ResearchEvent) if row.type == "structured_output_error"]
    assert diagnostics == [{"stage": "planning", "error_code": "ValidationError",
        "fields": [{"path": "clauses.0.targets", "type": "missing"}], "finish_reason": "stop"}]
    assert "offline-stub-key" not in str(diagnostics)


def test_v20_make_plan_and_save_plan_bind_the_new_protocol_without_falling_back(engine, monkeypatch):
    repo, tools, specs = environment(engine)
    planner = OfflinePlanner()
    monkeypatch.setattr(tools, "model", planner.model)
    plan = asyncio.run(tools.make_plan(specs))
    assert planner.calls == ["planning", "planning_check", "planning_queries"]
    assert plan["intent_coverage"]["method"] == "source_request_navigation_v6"
    tools.save_plan(plan)
    frozen = tools.plan()
    assert frozen["protocol_hash"] == repo.protocol()["hash"]
    assert frozen["execution_revision"] == "question_loop_v20"
    asset = next(asset for asset in repo.rows(ResearchAsset) if asset.content_kind == "research_plan")
    assert asset.content_hash == digest(frozen)


@pytest.mark.parametrize("fault", [None, "plan_hash", "protocol_hash", "plan_binding", "plan_revision", "run_revision", "unknown_method"])
def test_actual_questions_api_projects_new_requirements_scope_and_rejects_bad_versions(engine, client, monkeypatch, fault):
    from backend.routers import exploration
    repo, tools, specs = environment(engine)
    planner = OfflinePlanner()
    monkeypatch.setattr(tools, "model", planner.model)
    plan = asyncio.run(tools.make_plan(specs))
    tools.save_plan(plan)
    if fault:
        with repo.transaction() as session:
            active = next(asset for asset in repo.rows(ResearchAsset) if asset.content_kind == "research_plan")
            if fault == "plan_hash":
                active.content_hash = "tampered"
            elif fault == "protocol_hash":
                proto = repo.asset(repo.run().protocol_asset_id)
                proto.content_hash = "tampered"
                session.add(proto)
            elif fault == "run_revision":
                row = session.get(ExplorationRun, repo.run_id)
                options = deepcopy(row.options)
                options["workflow"]["loop_revision"] = "question_loop_v19"
                row.options = options
                session.add(row)
            else:
                data = deepcopy(active.inline_payload)
                if fault == "plan_binding":
                    data["protocol_hash"] = "other-protocol"
                elif fault == "plan_revision":
                    data["execution_revision"] = "question_loop_v19"
                else:
                    data["intent_coverage"]["method"] = "future_unknown"
                active.inline_payload, active.content_hash = data, digest(data)
            session.add(active)
    monkeypatch.setattr(exploration, "get_engine", lambda: engine)
    path = f"/api/exploration/runs/{repo.run_id}/questions"
    origin = {"Origin": settings.frontend_origin}
    preflight = client.options(path, headers={**origin, "Access-Control-Request-Method": "GET"})
    response = client.get(path, headers=origin)
    assert preflight.status_code == response.status_code == 200
    assert response.headers["access-control-allow-origin"] == settings.frontend_origin
    payload = response.json()
    assert payload["legacy"] is False and payload["run_id"] == repo.run_id
    row = payload["items"][0]
    if fault:
        assert row["requirements"] == [] and row["scope_constraints"] == []
        assert row["reading_role"] is None
    else:
        assert row["requirements"] == [
            {key: unit[key] for key in ("unit_id", "requirement", "subject", "phase", "mode", "source_quote", "source_origin", "response_kind")}
            for unit in plan["requirement_ledger"]["main"]
        ]
        assert [unit["response_kind"] for unit in row["requirements"]] == ["fact", "inference"]
        assert row["scope_constraints"] == ["只讨论单线程，不做性能排名"]
        assert row["reading_role"] == "mechanism"
        for unit in row["requirements"]:
            assert unit["source_quote"] in QUESTION

@pytest.mark.parametrize('mode', ['unreviewed', 'source_failed', 'empty'])
def test_report_distinguishes_retrieval_failure_from_verified_empty_sources(engine, mode):
    from backend.services.deep_research.research_loop.dossier import freeze
    from backend.services.deep_research.research_loop.writer import build_report
    repo, tools, _ = environment(engine)
    record = {'recall_phase':'initial', 'attempts':[{'provider':'openalex', 'query':'queue',
               'status':'error' if mode=='source_failed' else 'ok_empty', 'hits':0}],
              'unreviewed_count':24 if mode=='unreviewed' else 0}
    with repo.transaction() as session:
        session.add(ResearchAsset(run_id=repo.run_id, content_kind='search_record', inline_payload=record,
                                  content_hash=digest(record), parse_status='parsed'))
    report = build_report(tools, freeze(tools, 'no_actionable_gap'))
    assert report['status'] == ('no_results' if mode=='empty' else 'partial')
    assert report['method']['retrieval_incomplete'] == (mode!='empty')
    assert not report['findings']
