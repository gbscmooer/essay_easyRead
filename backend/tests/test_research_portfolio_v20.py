"""Complete candidate exposure, cross-batch selection and honest failed-batch audit."""

import asyncio
from copy import deepcopy

import pytest

from backend.models.research_core import ResearchAsset, ResearchQuestion
from backend.services.deep_research.research_loop.contracts import Action
from backend.services.deep_research.research_loop.portfolio_v20 import execute, prepare, navigation_calls, navigation_cost, OUTPUT_LIMITS
from backend.services.deep_research.research_loop.resources import ResourceExhausted
from backend.tests.test_research_sources_v11 import env11 as source_fixture

env20 = source_fixture


def add_documents(repo, tools, count=70):
    with repo.transaction() as session:
        for index in range(count):
            session.add(ResearchAsset(run_id=repo.run_id, **tools.document_spec(
                {"key": f"queue:{index}", "title": f"Queue original research {index}", "year": 2025,
                 "relevant_questions": ["main"], "research_role": "method", "authors": ["Ada Researcher"],
                 "abstract": f"This original research independently implements a bounded queue operation {index}."})))
    tools.resources.current_question_id = "main"


def choice(ref, initial=False):
    row = {"document_ref": ref, "focus_queries": ["queue operation"], "reason": "Actual operation worth reading"}
    if not initial:
        row["unit_refs"] = ["U1"]
    return row


def offline_model(tools, calls, fail_batch=None, fail_resource=False, scope_eligible=True):
    async def model(system, data, **kw):
        calls.append({"stage": kw["stage"], "data": deepcopy(data), "system": system, "max_tokens": kw["max_tokens"]})
        if kw["stage"] == "reading_selection_check":
            return kw["schema"].model_validate({"checks": [
                {"document_ref": row["document_ref"], "eligible": scope_eligible,
                 "scope_source_ids": [row["scope_passages"][0]["source_id"]] if scope_eligible else [], "reason": "Current task scope"}
                for row in data["candidates"]]}).model_dump()
        assert kw["stage"] in ("reading_portfolio_batch", "reading_portfolio_merge")
        if fail_batch is not None and data.get("candidate_batch", {}).get("index") == fail_batch:
            if fail_resource:
                raise ResourceExhausted("Offline budget bound")
            raise ValueError("Offline malformed batch")
        refs = [row["document_ref"] for row in data["candidates"]]
        # Later batches must be selectable; no first-32 or first-batch fallback.
        selected = refs[-1:] if data["stage"] == "initial" else refs[-3:]
        value = {"classification": choice(selected[-1], initial=True)} if data["stage"] == "initial" else {"representatives": [choice(ref) for ref in selected]}
        return kw["schema"].model_validate({**value, "gaps": []}).model_dump()
    return model


def payload(result):
    return result["assets"][0]["inline_payload"]


@pytest.mark.parametrize("env20", ["question_loop_v20"], indirect=True)
@pytest.mark.parametrize("stage", ["initial", "after_coverage_search"])
def test_every_readable_candidate_is_offered_and_late_batch_can_win(env20, monkeypatch, stage):
    repo, tools = env20
    add_documents(repo, tools)
    calls = []
    monkeypatch.setattr(tools, "model", offline_model(tools, calls))
    context, docs, *_ = prepare(tools, repo.rows(ResearchQuestion)[0], stage)
    assert len(docs) == 71 and context["material_state"]["candidate_batch_count"] == 3
    assert navigation_calls(tools, "main", stage) == (8 if stage == "initial" else 10)
    assert navigation_cost(tools, "main", stage) > 0
    result = payload(asyncio.run(execute(tools, Action(tool="plan_reading", question_id="main", query=stage))))
    batch_calls = [call for call in calls if call["stage"] == "reading_portfolio_batch"]
    assert [len(call["data"]["candidates"]) for call in batch_calls] == [24, 24, 23]
    assert {row["document_ref"] for call in batch_calls for row in call["data"]["candidates"]} == set(docs)
    assert all("原文支持" in call["system"] for call in batch_calls)
    merge = next(call for call in calls if call["stage"] == "reading_portfolio_merge")
    assert merge["data"]["candidates"][-1]["document_ref"] == "D71"
    assert docs["D71"].asset_id in {choice["asset_id"] for choice in result["choices"]}
    assert result["candidate_reviewed_count"] == result["candidate_count"] == 71
    assert result["candidate_unreviewed_count"] == 0 and result["selection_status"] == "selected"
    assert all(row["status"] == "screened" for row in result["selection_batches"])
    assert not tools.evidence("main")  # Prospective selection never produces fact support.
    audits = [a.inline_payload for a in repo.rows(ResearchAsset) if a.content_kind == "portfolio_attempt"]
    assert len(audits) == 4 and all(a["method"] == "complete_candidate_batches_v20" for a in audits)
    if stage == "after_coverage_search":
        assert calls[-1]["stage"] == "reading_selection_check"
        assert all(choice["selection_review"]["eligible"] for choice in result["choices"])


@pytest.mark.parametrize("env20", ["question_loop_v20"], indirect=True)
@pytest.mark.parametrize("resource_failure", [False, True])
def test_failed_batch_records_unreviewed_tail_and_preserves_other_nominations(env20, monkeypatch, resource_failure):
    repo, tools = env20
    add_documents(repo, tools)
    calls = []
    monkeypatch.setattr(tools, "model", offline_model(tools, calls, fail_batch=2, fail_resource=resource_failure))
    result = payload(asyncio.run(execute(tools, Action(tool="plan_reading", question_id="main", query="initial"))))
    assert result["candidate_reviewed_count"] == (24 if resource_failure else 47)
    assert result["candidate_unreviewed_count"] == (47 if resource_failure else 24)
    assert len(result["candidate_unreviewed_refs"]) == result["candidate_unreviewed_count"]
    assert result["choices"] and result["selection_status"] == "partial"
    assert result["selection_batches"][1]["status"] == ("resource_exhausted" if resource_failure else "failed")
    assert any("未完成本阶段" in gap for gap in result["gaps"])
    assert all(not a.inline_payload.get("fact") for a in tools.documents())


@pytest.mark.parametrize("env20", ["question_loop_v20"], indirect=True)
def test_selected_unread_candidates_still_require_scope_gate_and_cannot_become_evidence(env20, monkeypatch):
    repo, tools = env20
    add_documents(repo, tools, count=1)
    calls = []
    monkeypatch.setattr(tools, "model", offline_model(tools, calls, scope_eligible=False))
    result = payload(asyncio.run(execute(tools, Action(tool="plan_reading", question_id="main", query="after_coverage_search"))))
    assert not result["choices"] and result["selection_status"] == "insufficient"
    assert all(row["code"] == "PORTFOLIO_SCOPE_NOT_VERIFIED" for row in result["navigation_rejections"])
    assert not tools.evidence("main")


@pytest.mark.parametrize("env20", ["question_loop_v20"], indirect=True)
def test_navigation_estimate_grows_with_full_candidate_set_not_only_first_32(env20):
    repo, tools = env20
    add_documents(repo, tools, count=31)
    before = navigation_cost(tools, "main", "initial")
    before_calls = navigation_calls(tools, "main", "initial")
    add_documents(repo, tools, count=70)
    assert navigation_cost(tools, "main", "initial") > before
    assert navigation_calls(tools, "main", "initial") > before_calls


@pytest.mark.parametrize("env20", ["question_loop_v19"], indirect=True)
def test_previous_prepare_still_preserves_historical_first_32_candidate_behavior(env20):
    from backend.services.deep_research.research_loop.portfolio_v19 import prepare as old_prepare
    repo, tools = env20
    add_documents(repo, tools)
    context, docs, *_ = old_prepare(tools, repo.rows(ResearchQuestion)[0], "initial")
    assert len(docs) == len(context["candidates"]) == 32


@pytest.mark.parametrize("env20", ["question_loop_v20"], indirect=True)
def test_task_navigation_keeps_all_candidate_statistics_without_requiring_paper_facts(env20, monkeypatch):
    repo, tools = env20
    add_documents(repo, tools)
    plan = deepcopy(tools.plan())
    plan['requirement_ledger']['main'][0]['source_quote'] = '解释AlphaNet怎样管理队列'
    monkeypatch.setattr(tools, 'plan', lambda: plan)
    calls = []

    async def model(system, data, **kw):
        calls.append(deepcopy(data))
        assert "candidates" not in data and "sources" not in data
        return kw["schema"].model_validate({"task_searches": [
            {"unit_refs": ["U1"], "query_tokens": ["queue", "operation"], "reason": "User-anchored reading navigation"}
        ], "gaps": []}).model_dump()

    monkeypatch.setattr(tools, "model", model)
    result = payload(asyncio.run(execute(tools, Action(tool="plan_reading", question_id="main", query="task_recall"))))
    assert len(calls) == 1 and result["candidate_count"] == 71
    assert result["next_searches"][0]["navigation_origin"] == "user_task"
    assert result["selection_status"] == "navigation_ready" and not result["choices"]


@pytest.mark.parametrize("env20", ["question_loop_v20"], indirect=True)
@pytest.mark.parametrize("stage", ["initial", "after_coverage_search"])
def test_batch_and_merge_output_requests_match_the_same_prospective_budget_constants(env20, monkeypatch, stage):
    from backend.services.deep_research.research_loop import answer_budget_v19, portfolio_v20
    repo, tools = env20
    add_documents(repo, tools)
    estimates, calls = [], []

    def estimate(system, data, schema, output):
        estimates.append((schema, output))
        return output

    monkeypatch.setattr(answer_budget_v19, "model_cost", estimate)
    cost = navigation_cost(tools, "main", stage)
    stage_estimates = [output for schema, output in estimates if schema is portfolio_v20.SCHEMAS[stage]]
    assert stage_estimates == [OUTPUT_LIMITS[stage]] * 4  # Three batches plus their merge.
    assert cost == 2 * sum(output for _, output in estimates) + 2500
    if stage == "after_coverage_search":
        assert estimates[-1] == (portfolio_v20.ChoiceChecks, 2400)  # Inherited scope check remains bounded separately.
    monkeypatch.setattr(tools, "model", offline_model(tools, calls))
    asyncio.run(execute(tools, Action(tool="plan_reading", question_id="main", query=stage)))
    actual = [call["max_tokens"] for call in calls if call["stage"] in ("reading_portfolio_batch", "reading_portfolio_merge")]
    assert actual == stage_estimates


@pytest.mark.parametrize("env20", ["question_loop_v20"], indirect=True)
@pytest.mark.parametrize("stage", ["coverage_refresh", "task_recall"])
def test_navigation_output_request_matches_four_attempt_budget_without_affecting_old_portfolio(env20, monkeypatch, stage):
    from backend.services.deep_research.research_loop import answer_budget_v19, portfolio_v20
    repo, tools = env20
    add_documents(repo, tools, count=1)
    original_prepare = portfolio_v20.prepare

    def with_source(*args):
        result = list(original_prepare(*args))
        if stage == "coverage_refresh":
            result[3] = {"W1": {"quote": "Actual queue operation source window"}}
        return tuple(result)

    estimates, requests = [], []

    def estimate(system, data, schema, output):
        estimates.append(output)
        return output

    async def model(system, data, **kwargs):
        requests.append(kwargs["max_tokens"])
        field = "next_searches" if stage == "coverage_refresh" else "task_searches"
        return kwargs["schema"].model_validate({field: [], "gaps": []}).model_dump()

    monkeypatch.setattr(portfolio_v20, "prepare", with_source)
    monkeypatch.setattr(answer_budget_v19, "model_cost", estimate)
    monkeypatch.setattr(tools, "model", model)
    assert navigation_calls(tools, "main", stage) == 4
    assert navigation_cost(tools, "main", stage) == 4 * OUTPUT_LIMITS[stage] + 2500
    asyncio.run(execute(tools, Action(tool="plan_reading", question_id="main", query=stage)))
    assert estimates == requests == [4000]

@pytest.mark.parametrize('env20', ['question_loop_v20'], indirect=True)
@pytest.mark.parametrize('failure_stage', ['reading_portfolio_batch', 'reading_portfolio_merge', 'reading_selection_check'])
def test_provider_failure_keeps_portfolio_audit_and_does_not_create_supported_choices(env20, monkeypatch, failure_stage):
    from backend.services.llm import LLMError
    repo, tools = env20
    add_documents(repo, tools)
    calls = []
    original = offline_model(tools, calls)

    async def model(system, data, **kw):
        if kw['stage'] == failure_stage:
            raise LLMError('LLM_FORBIDDEN', 'secret-provider-message', status_code=403)
        return await original(system, data, **kw)

    monkeypatch.setattr(tools, 'model', model)
    result = payload(asyncio.run(execute(tools, Action(tool='plan_reading', question_id='main', query='after_coverage_search'))))
    assert result['candidate_count'] == 71 and not result['choices']
    assert result['selection_status'] == 'insufficient'
    assert result['candidate_unreviewed_count'] == (71 if failure_stage == 'reading_portfolio_batch' else 0)
    assert any(r.get('error_code') == 'LLM_FORBIDDEN' for r in result['navigation_rejections'])
    assert 'secret-provider-message' not in str(result)
    assert not tools.evidence('main')
