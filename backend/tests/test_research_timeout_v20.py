"""Batched tools must fit their declared attempts without changing historical limits."""
from types import SimpleNamespace

import pytest

from backend.services.deep_research.research_loop.contracts import Action
from backend.services.deep_research.research_loop.timeout_v20 import tool_timeout


def tools(revision="question_loop_v20", *, active=0, online=True, exhausted=False, targets=()):
    return SimpleNamespace(
        repo=SimpleNamespace(protocol=lambda: {"execution_revision": revision},
                             run=lambda: SimpleNamespace(options={"search_online": online}, active_seconds=active)),
        resources=SimpleNamespace(source_exhausted=exhausted, limits={"wall_clock_minutes": 35}),
        plan=lambda: {"strategy": "material_first_v3", "targets": list(targets)})


def test_deeper_search_allows_four_relevance_batches_with_metadata_and_finite_deadline():
    action = Action(tool="search_papers", question_id="main", query="queue")
    assert tool_timeout(tools(), action) == 645
    assert tool_timeout(tools(active=2095), action) == 5
    assert tool_timeout(tools(active=2101), action) == .01


def test_named_lookup_has_smaller_envelope_and_offline_does_not_reserve_search_batches():
    action = Action(tool="search_papers", question_id="main", query="AlphaNet")
    assert tool_timeout(tools(targets=[{"question_id":"main", "name":"alphanet"}]), action) == 240
    assert tool_timeout(tools(online=False), action) == 90
    assert tool_timeout(tools(exhausted=True), action) == 90


@pytest.mark.parametrize("stage,attempts,expected", [("initial", 8, 510), ("after_coverage_search", 10, 630), ("task_recall", 4, 270), ("initial", 0, 90)])
def test_selection_timeout_accounts_for_all_batch_merge_and_review_attempts(monkeypatch, stage, attempts, expected):
    from backend.services.deep_research.research_loop import portfolio_v20
    monkeypatch.setattr(portfolio_v20, "navigation_calls", lambda *args: attempts)
    action = Action(tool="plan_reading", question_id="main", query=stage)
    assert tool_timeout(tools(), action) == expected
    assert tool_timeout(tools(active=2097), action) == 3


@pytest.mark.parametrize("tool,kwargs,expected", [("search_papers", {"query":"queue"},90), ("plan_reading",{"query":"initial"},90), ("assess_answer",{},90), ("read_documents", {"asset_ids":["a","b","c"]},660)])
def test_frozen_v19_keeps_historical_timeouts_even_with_little_remaining_time(tool, kwargs, expected):
    action = Action(tool=tool, question_id="main", **kwargs)
    assert tool_timeout(tools("question_loop_v19", active=2099), action) == expected

@pytest.mark.parametrize('revision,model,api_format,expected', [
    ('question_loop_v20','MiniMax-M2.7-highspeed','openai_chat',120),
    ('question_loop_v19','MiniMax-M2.7-highspeed','openai_chat',60),
    ('question_loop_v20','other-model','openai_chat',60),
    ('question_loop_v20','MiniMax-M2.7-highspeed','anthropic_messages',60),
])
def test_minimax_timeout_is_explicitly_version_and_protocol_gated(revision, model, api_format, expected):
    from backend.services.deep_research.research_loop.timeout_v20 import model_timeout
    adapter=tools(revision)
    adapter.config={'provider':'custom','api_format':api_format,'model':model,'api_key':'test-key'}
    assert model_timeout(adapter) == expected
    assert model_timeout(adapter,'low') == (120 if expected==120 else 90)


@pytest.mark.parametrize('tool,kwargs,expected', [
    ('search_papers',{'query':'queue'},1890),
    ('read_documents',{'asset_ids':['a','b','c']},990),
    ('read_document',{'asset_id':'a'},510),
    ('assess_answer',{},1590),
])
def test_minimax_outer_tool_does_not_cancel_valid_long_model_attempts(monkeypatch, tool, kwargs, expected):
    from backend.services.deep_research.research_loop import answer_budget_v19
    monkeypatch.setattr(answer_budget_v19, 'envelope', lambda *a: {'llm_calls': 5})
    adapter=tools()
    adapter.config={'provider':'custom','api_format':'openai_chat','model':'MiniMax-M2.7-highspeed','api_key':'test-key'}
    action=Action(tool=tool,question_id='main',**kwargs)
    assert tool_timeout(adapter,action)==expected
    adapter.repo.run=lambda: SimpleNamespace(options={'search_online':True},active_seconds=2099)
    assert tool_timeout(adapter,action)==1
