"""Real stage ordering, row-local identity gates, bounded repair and API projection."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
import pytest
from sqlmodel import Session
from backend.models.research_core import ResearchAsset, ResearchQuestion
from backend.services.deep_research.research_loop.portfolio_v17 import (
    Initial,
    Final,
    bind_choices,
    execute,
    navigation_cost,
)
from backend.services.deep_research.research_loop.contracts import Action, digest
from backend.services.deep_research.research_loop.controller import Controller
from backend.services.deep_research.research_loop.dossier import freeze
from backend.services.deep_research.research_loop.writer import build_report
from backend.services.deep_research.research_loop.queries import questions
from backend.tests.test_research_sources_v11 import env11 as source_fixture
from backend.tests.test_research_elements_v15 import model15
from backend.tests.test_research_planning_v16 import ScopePlanner

env17 = source_fixture


def document(aid, role="method", title=None, family=None):
    return SimpleNamespace(
        asset_id=aid,
        family_key=family or aid,
        inline_payload={
            "text": "Actual original passage.",
            "paper": {"title": title or aid, "research_role": role},
        },
    )


def choice(ref="D1"):
    return {
        "document_ref": ref,
        "unit_refs": ["U1"],
        "focus_queries": ["queue operation"],
        "reason": "original mechanism",
    }


@pytest.mark.parametrize(
    "fault,code",
    [
        ("document", "PORTFOLIO_DOCUMENT_REF"),
        ("requirement", "PORTFOLIO_REQUIREMENT_REF"),
        ("survey", "PORTFOLIO_SURVEY_AS_REPRESENTATIVE"),
        ("empty", "PORTFOLIO_EMPTY_MATERIAL"),
        ("duplicate", "PORTFOLIO_DUPLICATE_WORK"),
        ("query", "PORTFOLIO_QUERY_BOUNDS"),
    ],
)
def test_invalid_row_never_erases_valid_method(fault, code):
    docs = {"D1": document("one"), "D2": document("two")}
    bad = choice("D2")
    if fault == "document":
        bad["document_ref"] = "D404"
    if fault == "requirement":
        bad["unit_refs"] = ["U404"]
    if fault == "survey":
        docs["D2"] = document("two", "survey")
    if fault == "empty":
        docs["D2"].inline_payload["text"] = ""
    if fault == "duplicate":
        docs["D2"] = document("two", family="one")
    if fault == "query":
        bad["focus_queries"] = ["word " * 13]
    kept, rejected = bind_choices(
        {"representatives": [choice(), bad]},
        docs,
        {"U1": "real-u"},
        "after_coverage_search",
        [],
    )
    assert [c["asset_id"] for c in kept] == ["one"] and rejected[0]["code"] == code


def test_total_allocation_includes_examined_classification():
    docs = {f"D{i}": document(str(i)) for i in range(1, 5)}
    kept, rejected = bind_choices(
        {"representatives": [choice(d) for d in docs]},
        docs,
        {"U1": "u"},
        "after_coverage_search",
        [document("survey", "survey")],
    )
    assert len(kept) == 3 and rejected[0]["code"] == "PORTFOLIO_READ_ALLOCATION"


def test_initial_contract_needs_no_representatives_or_free_role():
    raw = Initial.model_validate(
        {
            "classification": {k: v for k, v in choice().items() if k != "unit_refs"},
            "gaps": [],
        }
    ).model_dump()
    kept, rejected = bind_choices(
        raw, {"D1": document("taxonomy", "survey")}, {"U1": "u"}, "initial", []
    )
    assert kept[0]["role"] == "classification" and len(kept) == 1 and not rejected
    assert (
        not {"representatives", "choices", "next_searches"}
        & Initial.model_json_schema()["properties"].keys()
    )
    assert (
        "role" not in Final.model_json_schema()["$defs"]["Representative"]["properties"]
    )


@pytest.mark.parametrize("env17", ["question_loop_v17"], indirect=True)
@pytest.mark.parametrize("persistent", [False, True])
def test_unknown_short_reference_repaired_once_and_all_candidates_recorded(
    env17, monkeypatch, persistent
):
    repo, tools = env17
    calls = []

    async def model(system, data, **kw):
        calls.append(deepcopy(data))
        ref = (
            "D404"
            if len(calls) == 1 or persistent
            else data["candidates"][0]["document_ref"]
        )
        return (
            kw["schema"]
            .model_validate(
                {
                    "classification": {
                        "document_ref": ref,
                        "focus_queries": ["queue classification"],
                        "reason": "actual navigation",
                    },
                    "gaps": [],
                }
            )
            .model_dump()
        )

    monkeypatch.setattr(tools, "model", model)
    result = asyncio.run(
        execute(tools, Action(tool="plan_reading", question_id="main"))
    )
    payload = result["assets"][0]["inline_payload"]
    assert (
        len(calls) == 2
        and calls[1]["repair"]["rejections"][0]["code"] == "PORTFOLIO_DOCUMENT_REF"
    )
    assert bool(payload["choices"]) is not persistent
    audits = [
        a for a in repo.rows(ResearchAsset) if a.content_kind == "portfolio_attempt"
    ]
    assert (
        len(audits) == 2
        and audits[0].inline_payload["candidate"]["classification"]["document_ref"]
        == "D404"
    )
    assert all(a.inline_payload["aliases"]["documents"] for a in audits)


@pytest.mark.parametrize("env17", ["question_loop_v17"], indirect=True)
@pytest.mark.parametrize("nav_fault", [False, True])
def test_classification_original_then_navigation_search_and_final_method_before_answer(
    env17, monkeypatch, nav_fault
):
    repo, tools = env17
    with repo.transaction() as s:
        run = repo.run()
        run.options = {**run.options, "search_online": True}
        s.add(run)
        s.add(
            ResearchAsset(
                run_id=repo.run_id,
                asset_id="taxonomy",
                family_key="taxonomy",
                content_kind="document",
                content_hash="taxonomy-text",
                parse_status="parsed",
                inline_payload={
                    "paper": {
                        "key": "taxonomy",
                        "title": "Queue mechanism taxonomy",
                        "research_role": "survey",
                        "relevant_questions": ["main"],
                    },
                    "source_kind": "full_text",
                    "text": "AlphaNet stores a bounded queue and drops the oldest entry when the queue is full. BetaLog offers indexed retrieval.",
                },
            )
        )
        for a in repo.rows(ResearchAsset):
            if a.content_kind == "research_plan":
                a.status = "superseded"
                s.add(a)
    plan = tools.plan() or {
        "strategy": "material_first_v3",
        "queries": [],
        "targets": [],
        "requirement_ledger": {"main": []},
    }
    # Keep the original immutable requirements for the actual controller path.
    prior = next(
        a.inline_payload
        for a in repo.rows(ResearchAsset)
        if a.content_kind == "research_plan"
    )
    plan = {**prior, "reading_roles": [{"question_id": "main", "role": "overview"}]}
    tools.save_plan(plan)
    events = []
    base = model15(tools)

    async def model(system, data, **kw):
        events.append(kw["stage"])
        if kw["stage"].startswith("reading_portfolio"):
            if data["stage"] == "initial":
                c = next(
                    c
                    for c in data["candidates"]
                    if c["title"] == "Queue mechanism taxonomy"
                )
                value = {
                    "classification": {
                        "document_ref": c["document_ref"],
                        "focus_queries": ["queue mechanism taxonomy"],
                        "reason": "actual classification",
                    },
                    "gaps": [],
                }
            elif data["stage"] == "coverage_refresh":
                source = data["sources"][0]
                value = {
                    "next_searches": [
                        {
                            "source_id": source["source_id"],
                            "source_quote": "BetaLog offers indexed retrieval.",
                            "query": "GoldNet indexed retrieval"
                            if nav_fault
                            else "BetaLog indexed retrieval",
                            "unit_refs": ["U1"],
                            "reason": "actual alternative",
                        }
                    ],
                    "gaps": [],
                }
            else:
                c = next(
                    c
                    for c in data["candidates"]
                    if c["title"] != "Queue mechanism taxonomy"
                )
                value = {
                    "representatives": [
                        {**choice(c["document_ref"]), "unit_refs": ["U1", "U2"]}
                    ],
                    "gaps": [],
                }
            return kw["schema"].model_validate(value).model_dump()
        return await base(system, data, **kw)

    async def search(action):
        events.append("search")
        assert "answer" not in events and action.query == "BetaLog indexed retrieval"
        return {"assets": []}

    async def fetch(action):
        return {"assets": []}

    monkeypatch.setattr(tools, "model", model)
    monkeypatch.setattr(tools, "search", search)
    monkeypatch.setattr(tools, "fetch", fetch)
    asyncio.run(Controller(tools).run())
    assert "answer" in events and events.index("extraction_batch") < events.index(
        "answer"
    )
    assert ("search" in events) is not nav_fault
    if not nav_fault:
        assert events.index("search") < events.index("answer")
    portfolios = [
        a.inline_payload
        for a in repo.rows(ResearchAsset)
        if a.content_kind == "reading_portfolio"
    ]
    assert [p["stage"] for p in portfolios] == [
        "initial",
        "coverage_refresh",
        "after_coverage_search",
    ]
    assert len(portfolios[-1]["choices"]) == 2
    assert len(
        [a for a in repo.rows(ResearchAsset) if a.content_kind == "portfolio_attempt"]
    ) == (4 if nav_fault else 3)


@pytest.mark.parametrize("env17", ["question_loop_v17"], indirect=True)
def test_tool_failure_is_material_selection_failure_not_no_actionable_gap(
    env17, monkeypatch
):
    repo, tools = env17
    prior = tools.plan()
    with repo.transaction() as s:
        for a in repo.rows(ResearchAsset):
            if a.content_kind == "research_plan":
                a.status = "superseded"
                s.add(a)
    tools.save_plan(
        {**prior, "reading_roles": [{"question_id": "main", "role": "overview"}]}
    )

    async def model(*args, **kw):
        raise ValueError("offline malformed output")

    monkeypatch.setattr(tools, "model", model)
    stop = asyncio.run(Controller(tools).run())
    assert stop == "material_selection_failed"
    report = build_report(tools, freeze(tools, stop))
    assert report["status"] == "partial" and "选材工具" in report["summary"]
    assert (
        "额度" not in report["summary"] and "选材工具" in report["chapters"][0]["text"]
    )
    assert report["method"]["stop_details"][0]["tool"] == "plan_reading"
    assert any(a.content_kind == "portfolio_attempt" for a in repo.rows(ResearchAsset))


@pytest.mark.parametrize("env17", ["question_loop_v17"], indirect=True)
@pytest.mark.parametrize(
    "fault", [None, "plan_hash", "protocol_hash", "plan_binding", "unknown_method"]
)
def test_new_real_plan_projection_checks_protocol_hash_identity_and_preserves_source(
    env17, monkeypatch, client, fault
):
    from backend.routers import exploration
    from backend.config import settings

    repo, tools = env17
    planner = ScopePlanner()
    monkeypatch.setattr(tools, "model", planner.model)
    q = repo.rows(ResearchQuestion)[0]
    plan = asyncio.run(
        tools.make_plan(
            [
                q.model_dump(
                    exclude={
                        "id",
                        "run_id",
                        "created_at",
                        "updated_at",
                        "status",
                        "gaps",
                        "answer_revision_id",
                    }
                )
            ]
        )
    )
    with repo.transaction() as s:
        for a in repo.rows(ResearchAsset):
            if a.content_kind == "research_plan":
                a.status = "superseded"
                s.add(a)
    tools.save_plan(plan)
    with repo.transaction() as s:
        active = next(
            a
            for a in repo.rows(ResearchAsset)
            if a.content_kind == "research_plan" and a.status == "active"
        )
        if fault == "plan_hash":
            active.content_hash = "tampered"
        if fault == "protocol_hash":
            proto = repo.asset(repo.run().protocol_asset_id)
            proto.content_hash = "tampered"
            s.add(proto)
        if fault in ("plan_binding", "unknown_method"):
            data = deepcopy(active.inline_payload)
            if fault == "plan_binding":
                data["protocol_hash"] = "other protocol"
            else:
                data["intent_coverage"]["method"] = "future_unknown"
            active.inline_payload = data
            active.content_hash = digest(data)
        s.add(active)
    monkeypatch.setattr(exploration, "get_engine", lambda: repo.engine)
    path = f"/api/exploration/runs/{repo.run_id}/questions"
    origin = {"Origin": settings.frontend_origin}
    assert (
        client.options(
            path, headers={**origin, "Access-Control-Request-Method": "GET"}
        ).status_code
        == 200
    )
    response = client.get(path, headers=origin)
    assert (
        response.status_code == 200
        and response.headers["access-control-allow-origin"] == settings.frontend_origin
    )
    units = response.json()["items"][0]["requirements"]
    if fault:
        assert units == []
    else:
        assert [u["requirement"] for u in units] == [
            u["requirement"] for u in plan["requirement_ledger"]["main"]
        ]
        assert units[0]["source_quote"] in q.text


@pytest.mark.parametrize("env17", ["question_loop_v17"], indirect=True)
def test_navigation_estimate_uses_actual_candidate_text_and_stage_schema(env17):
    repo, tools = env17
    initial = navigation_cost(tools, "main", "initial")
    with repo.transaction() as s:
        a = tools.documents()[0]
        data = deepcopy(a.inline_payload)
        data["paper"]["abstract"] = "Queue original material. " * 100
        a.inline_payload = data
        s.add(a)
    assert navigation_cost(tools, "main", "initial") > initial
