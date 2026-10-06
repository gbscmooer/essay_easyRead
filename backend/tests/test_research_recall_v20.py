"""All recall tests use offline providers and local verdicts, never paid models."""

import asyncio
from types import SimpleNamespace

import pytest

from backend.models.research_core import ResearchAsset

from backend.services.deep_research.research_loop.contracts import Action
from backend.services.deep_research.research_loop.recall_v20 import identity_aliases, merge_work_records
from backend.services.deep_research.research_loop.resources import ResourceExhausted
from backend.tests.test_research_sources_v11 import env11 as source_fixture

env20 = source_fixture


def paper(index, **extra):
    return {"key": f"doi:10.1234/queue.{index}", "doi": f"10.1234/queue.{index}",
            "title": f"Queue mechanism number {index}", "authors": ["Ada Researcher"],
            "year": 2025, "abstract": f"This original study describes bounded queue operation {index}.", **extra}


def candidate(row):
    return SimpleNamespace(**{**row, "canonical_key": row["key"]})


def online(repo, tools):
    prior = tools.plan() or {}
    with repo.transaction() as session:
        run = repo.run()
        run.options = {**run.options, "search_online": True}
        session.add(run)
        for asset in repo.rows(ResearchAsset):
            if asset.content_kind == "research_plan":
                asset.status = "superseded"
                session.add(asset)
    tools.save_plan({**prior, "queries": [
        {"question_id": "main", "query": "queue operation"},
        {"question_id": "main", "query": "very narrow task"},
    ]})


def providers(monkeypatch, starts=(0, 20, 40)):
    from backend.services.deep_research import recall_engine
    from backend.services.deep_research.research_loop import retrieval
    calls = []

    async def default(source, query, limit, order, **kw):
        calls.append((source, query, limit))
        start = starts[0] if source == "openalex" else starts[2]
        return [candidate(paper(i)) for i in range(start, start + limit)]

    async def arxiv(query, *, cutoff=None, limit=6):
        calls.append(("arxiv", query, limit))
        return [paper(i) for i in range(starts[1], starts[1] + limit)]

    monkeypatch.setattr(recall_engine, "_default_search_one", default)
    monkeypatch.setattr(retrieval, "search_arxiv", arxiv)
    return calls


def relevance_model(monkeypatch, tools):
    screened = []

    async def model(system, data, **kw):
        assert kw["stage"] == "relevance"
        from backend.services.deep_research.research_loop.recall_v20 import relevance_output_tokens
        assert kw["max_tokens"] == (relevance_output_tokens(tools) if tools.repo.protocol()["execution_revision"] == "question_loop_v20" else 3000)
        screened.append([p["key"] for p in data["papers"]])
        return kw["schema"].model_validate({"decisions": [
            {"key": p["key"], "status": "relevant", "role": "method", "scope": "specific", "reason": "Actual task operation"}
            for p in data["papers"]]}).model_dump()

    monkeypatch.setattr(tools, "model", model)
    return screened


def audit(result):
    return next(a["inline_payload"] for a in result["assets"] if a["content_kind"] == "search_record")


def test_identity_bridge_groups_arxiv_versions_and_doi_without_title_only_merge():
    rows = [paper(1, key="arxiv:2510.00001", doi=None, arxiv_id="2510.00001v1"),
            paper(1, key="doi:10.1234/queue.1", arxiv_id="2510.00001v3"),
            paper(1, authors=["Different Author"], key="doi:10.1234/unrelated", doi="10.1234/unrelated")]
    merged = merge_work_records(rows)
    assert len(merged) == 2
    assert {"arxiv:2510.00001", "doi:10.1234/queue.1"}.issubset(merged[0]["identity_aliases"])
    assert identity_aliases(paper(1, key="t:weak", doi=None, authors=[]))


@pytest.mark.parametrize("env20", ["question_loop_v20"], indirect=True)
def test_real_search_deepens_three_sources_and_screens_all_65_unique_candidates(env20, monkeypatch):
    repo, tools = env20
    online(repo, tools)
    calls = providers(monkeypatch)
    screened = relevance_model(monkeypatch, tools)
    result = asyncio.run(tools.search(Action(tool="search_papers", question_id="main", query="queue operation")))
    record = audit(result)
    assert calls == [(s, "queue operation", 25) for s in ("openalex", "arxiv", "scopus")]
    assert [len(batch) for batch in screened] == [24, 24, 17]
    assert len({key for batch in screened for key in batch}) == 65
    assert record["raw_hits"] == 75 and record["identity_deduplicated_count"] == 65
    assert record["admitted"] == record["screened_count"] == 65
    assert record["unreviewed_count"] == 0 and not record["unreviewed_keys"]
    assert record["initial_recall"]["target_reached"]
    assert len(record["candidate_identities"][20]["observations"]) == 2
    assert record["source_counts"]["arxiv"] == {"raw_hits": 25, "identity_deduplicated_count": 25, "eligible_count": 25, "admitted_count": 25}
    assert all(r["requested_limit"] == 25 and r["retrieval_mode"] == "single_operation_deeper_results" for r in record["attempts"])


@pytest.mark.parametrize("env20", ["question_loop_v20"], indirect=True)
@pytest.mark.parametrize("resource_failure", [True, False])
def test_failed_relevance_batch_preserves_successful_candidates_and_exact_unreviewed(env20, monkeypatch, resource_failure):
    repo, tools = env20
    online(repo, tools)
    providers(monkeypatch)
    real_admit = tools.admit
    relevance_model(monkeypatch, tools)
    attempts = []

    async def admit(rows, question_id):
        attempts.append(len(rows))
        if len(attempts) == 2:
            if resource_failure:
                raise ResourceExhausted("offline exhausted budget")
            raise ValueError("offline invalid JSON")
        return await real_admit(rows, question_id)

    monkeypatch.setattr(tools, "admit", admit)
    record = audit(asyncio.run(tools.search(Action(tool="search_papers", question_id="main", query="queue operation"))))
    assert record["admitted"] == record["screened_count"] == (24 if resource_failure else 41)
    assert record["unreviewed_count"] == (41 if resource_failure else 24)
    assert len(record["unreviewed_keys"]) == record["unreviewed_count"]
    assert record["screening_batches"][1]["status"] == ("resource_exhausted" if resource_failure else "structured_output_failed")
    assert record["initial_recall"]["unreviewed_candidate_count"] == record["unreviewed_count"]
    assert not record["initial_recall"]["target_reached"]


@pytest.mark.parametrize("env20", ["question_loop_v20"], indirect=True)
def test_empty_sources_are_real_insufficiency_not_padding_or_memory(env20, monkeypatch):
    repo, tools = env20
    online(repo, tools)
    from backend.services.deep_research import recall_engine
    from backend.services.deep_research.research_loop import retrieval

    async def empty(*args, **kw):
        return []

    async def forbidden(*args, **kw):
        raise AssertionError("No model is needed for an empty candidate pool")

    monkeypatch.setattr(recall_engine, "_default_search_one", empty)
    monkeypatch.setattr(retrieval, "search_arxiv", empty)
    monkeypatch.setattr(tools, "model", forbidden)
    record = audit(asyncio.run(tools.search(Action(tool="search_papers", question_id="main", query="very narrow task"))))
    assert record["raw_hits"] == record["admitted"] == record["screened_count"] == 0
    assert not record["initial_recall"]["target_reached"]
    assert all(row["status"] == "ok_empty" for row in record["attempts"])
    assert "不能用无关" in record["initial_recall"]["limitation"]


@pytest.mark.parametrize("env20", ["question_loop_v19"], indirect=True)
def test_historical_v19_keeps_12_hit_source_depth_and_original_single_batch(env20, monkeypatch):
    repo, tools = env20
    online(repo, tools)
    calls = providers(monkeypatch, starts=(0, 20, 40))
    screened = relevance_model(monkeypatch, tools)
    record = audit(asyncio.run(tools.search(Action(tool="search_papers", question_id="main", query="queue operation"))))
    assert [c[2] for c in calls] == [12, 12, 12]
    assert len(screened) == 1 and len(screened[0]) == 36
    assert "initial_recall" not in record and "screening_batches" not in record


@pytest.mark.parametrize("env20", ["question_loop_v20"], indirect=True)
def test_later_followup_and_duplicate_initial_query_do_not_inflate_frozen_initial_target(env20, monkeypatch):
    repo, tools = env20
    online(repo, tools)
    providers(monkeypatch)
    relevance_model(monkeypatch, tools)
    action = Action(tool="search_papers", question_id="main", query="queue operation")
    first = asyncio.run(tools.search(action))
    with repo.transaction() as session:
        for spec in first["assets"]:
            session.add(ResearchAsset(run_id=repo.run_id, **spec))
    duplicate = audit(asyncio.run(tools.search(action)))
    assert duplicate["already_admitted"] == 65 and duplicate["admitted"] == duplicate["screened_count"] == 0
    assert duplicate["initial_recall"]["breadth_candidate_count"] == audit(first)["initial_recall"]["breadth_candidate_count"]
    providers(monkeypatch, starts=(100, 120, 140))
    followup = audit(asyncio.run(tools.search(Action(tool="search_papers", question_id="main", query="source derived followup"))))
    assert followup["recall_phase"] == "followup" and followup["admitted"] == 65
    assert followup["initial_recall"] == audit(first)["initial_recall"]

@pytest.mark.parametrize('env20', ['question_loop_v20'], indirect=True)
def test_provider_model_failure_preserves_sources_prior_admit_and_replayable_unreviewed(env20, monkeypatch):
    from backend.services.llm import LLMError
    repo, tools = env20
    online(repo, tools)
    providers(monkeypatch)
    real_admit = tools.admit
    relevance_model(monkeypatch, tools)
    calls = []

    async def admit(rows, question_id):
        calls.append(rows)
        if len(calls) == 2:
            raise LLMError('LLM_RATE_LIMITED', 'secret-provider-body', status_code=429)
        return await real_admit(rows, question_id)

    monkeypatch.setattr(tools, 'admit', admit)
    record = audit(asyncio.run(tools.search(Action(tool='search_papers', question_id='main', query='queue operation'))))
    assert len(calls) == 2 and record['raw_hits'] == 75 and len(record['attempts']) == 3
    assert record['screened_count'] == record['admitted'] == 24
    assert record['unreviewed_count'] == 41
    assert {p['key'] for p in record['unreviewed_candidates']} == set(record['unreviewed_keys'])
    assert all(p['abstract'] for p in record['unreviewed_candidates'])
    assert record['screening_batches'][1]['error_code'] == 'LLM_RATE_LIMITED'
    assert record['screening_batches'][1]['http_status'] == 429
    assert 'secret-provider-body' not in str(record)

@pytest.mark.parametrize('env20', ['question_loop_v20'], indirect=True)
def test_minimax_smaller_batches_preserve_every_candidate_and_same_admission_rules(env20, monkeypatch):
    repo, tools = env20
    online(repo, tools)
    tools.config = {'provider':'custom','api_format':'openai_chat','model':'MiniMax-M2.7-highspeed','api_key':'test-key'}
    providers(monkeypatch)
    screened=relevance_model(monkeypatch,tools)
    record=audit(asyncio.run(tools.search(Action(tool='search_papers',question_id='main',query='queue operation'))))
    assert [len(batch) for batch in screened] == [12,12,12,12,12,5]
    assert len({key for batch in screened for key in batch}) == record['screened_count'] == record['admitted'] == 65
    assert record['relevance_batch_size']==12 and record['unreviewed_count']==0
    assert record['initial_recall']['target_reached']
