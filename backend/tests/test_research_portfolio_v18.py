"""Real recovery ordering and prospective selection, without paid research."""

import asyncio
from copy import deepcopy

import pytest

from backend.models.research_core import ResearchAsset, ResearchQuestion, ResearchTask
from backend.services.deep_research.research_loop.contracts import Action, digest
from backend.services.deep_research.research_loop.controller import Controller
from backend.services.deep_research.research_loop.dossier import freeze
from backend.services.deep_research.research_loop.writer import build_report
from backend.services.deep_research.research_loop.portfolio_v18 import (
    abstract_view,
    execute,
    prepare,
    validate_task_hint,
    navigation_cost,
)
from backend.tests.test_research_sources_v11 import env11 as source_fixture
from backend.tests.test_research_elements_v15 import model15
from backend.tests.test_research_portfolio_v17 import choice

env18 = source_fixture


def overview(repo, tools):
    prior = tools.plan()
    with repo.transaction() as session:
        run = repo.run()
        run.options = {**run.options, "search_online": True}
        session.add(run)
        for a in repo.rows(ResearchAsset):
            if a.content_kind == "research_plan":
                a.status = "superseded"
                session.add(a)
    tools.save_plan(
        {**prior, "reading_roles": [{"question_id": "main", "role": "overview"}]}
    )


def task_hint(query="queue operation"):
    return {
        "source_quote": "解释AlphaNet怎样管理队列",
        "unit_refs": ["U1"],
        "query": query,
        "reason": "从原问题寻找可读的队列操作说明",
    }


def test_ordinary_abstract_keeps_late_framework_and_long_view_preserves_exact_spans():
    text = (
        "Motivation and general background. " * 35
        + "The survey categorizes queue capacity and overflow algorithms."
    )
    complete = abstract_view(text, "queue capacity overflow", 1600)
    assert complete["abstract_complete"] and complete["abstract"] == text
    long = text * 6 + "Actual queue capacity and overflow comparison at the end."
    excerpt = abstract_view(long, "queue capacity overflow", 750)
    assert not excerpt["abstract_complete"]
    assert (
        "Actual queue capacity and overflow comparison at the end."
        in excerpt["abstract"]
    )
    assert sum(s["end"] - s["start"] for s in excerpt["abstract_spans"]) <= 750
    assert all(
        long[s["start"] : s["end"]] in excerpt["abstract"]
        for s in excerpt["abstract_spans"]
    )


@pytest.mark.parametrize("env18", ["question_loop_v18"], indirect=True)
def test_real_context_keeps_late_abstract_and_marks_candidates_as_unread(env18):
    repo, tools = env18
    with repo.transaction() as session:
        a = tools.documents()[0]
        data = deepcopy(a.inline_payload)
        data["paper"]["abstract"] = (
            "Background discussion. " * 40
            + "A taxonomy of queue mechanisms and their capacity constraints."
        )
        a.inline_payload = data
        session.add(a)
    context, *_ = prepare(tools, repo.rows(ResearchQuestion)[0], "initial")
    assert "taxonomy of queue" in context["candidates"][0]["abstract"]
    assert context["candidates"][0]["abstract_complete"]
    assert context["material_state"] == {
        "candidate_count": 1,
        "read_count": 0,
        "source_window_count": 0,
    }
    assert navigation_cost(tools, "main", "initial") > 0


@pytest.mark.parametrize("env18", ["question_loop_v18"], indirect=True)
@pytest.mark.parametrize(
    "fault,code",
    [
        ("anchor", "PORTFOLIO_TASK_ANCHOR"),
        ("requirement", "PORTFOLIO_REQUIREMENT_REF"),
        ("vocabulary", "PORTFOLIO_TASK_VOCABULARY"),
        ("name", "PORTFOLIO_TASK_NAME"),
        ("duplicate", "PORTFOLIO_DUPLICATE_QUERY"),
    ],
)
def test_task_recall_does_not_invent_names_or_source_or_repeat_queries(
    env18, fault, code
):
    repo, tools = env18
    context, _, units, _, _, _, requirements = prepare(
        tools, repo.rows(ResearchQuestion)[0], "task_recall"
    )
    hint = task_hint()
    if fault == "anchor":
        hint["source_quote"] = "未给出的队列要求"
    if fault == "requirement":
        hint["unit_refs"] = ["U999"]
    if fault == "vocabulary":
        hint["query"] = "goldnet queue"
    if fault == "name":
        hint["query"] = "GoldNet queue"
        context["task_navigation"].append("GoldNet queue")
    if fault == "duplicate":
        context["completed_queries"] = [hint["query"]]
    with pytest.raises(ValueError, match=code):
        validate_task_hint(hint, context, units, requirements)
    assert "sources" not in context


@pytest.mark.parametrize("env18", ["question_loop_v18"], indirect=True)
def test_invalid_task_navigation_keeps_legal_row_and_original_input_audit(
    env18, monkeypatch
):
    repo, tools = env18
    calls = []

    async def model(system, data, **kw):
        calls.append(deepcopy(data))
        return (
            kw["schema"]
            .model_validate(
                {
                    "task_searches": [task_hint(), task_hint("GoldNet queue")],
                    "gaps": ["尚需阅读原文"],
                }
            )
            .model_dump()
        )

    monkeypatch.setattr(tools, "model", model)
    tools.resources.current_question_id = "main"
    result = asyncio.run(
        execute(
            tools, Action(tool="plan_reading", question_id="main", query="task_recall")
        )
    )
    portfolio = result["assets"][0]["inline_payload"]
    assert len(calls) == 1 and portfolio["selection_status"] == "navigation_ready"
    assert [h["query"] for h in portfolio["next_searches"]] == ["queue operation"]
    assert portfolio["next_searches"][0]["navigation_origin"] == "user_task"
    assert portfolio["navigation_rejections"][0]["code"] == "PORTFOLIO_TASK_VOCABULARY"
    attempt = next(
        a for a in repo.rows(ResearchAsset) if a.content_kind == "portfolio_attempt"
    ).inline_payload
    assert attempt["input"]["material_state"]["read_count"] == 0
    assert attempt["input"]["candidates"] and "sources" not in attempt["input"]


@pytest.mark.parametrize("env18", ["question_loop_v18"], indirect=True)
def test_empty_initial_recovers_from_user_task_before_reading_and_answer(
    env18, monkeypatch
):
    repo, tools = env18
    overview(repo, tools)
    events = []
    base = model15(tools)

    async def model(system, data, **kw):
        events.append(kw["stage"])
        if kw["stage"].startswith("reading_portfolio"):
            events.append(data["stage"])
            if data["stage"] == "initial":
                value = {
                    "classification": None,
                    "gaps": ["没有分类综述，仍可查找原论文"],
                }
            elif data["stage"] == "task_recall":
                assert (
                    data["material_state"]["read_count"] == 0 and "sources" not in data
                )
                value = {"task_searches": [task_hint()], "gaps": []}
            else:
                assert data["stage"] == "after_coverage_search" and not data["sources"]
                value = {
                    "representatives": [
                        {
                            **choice(data["candidates"][0]["document_ref"]),
                            "unit_refs": ["U1", "U2"],
                        }
                    ],
                    "gaps": ["评分细节等待读后核查"],
                }
            return kw["schema"].model_validate(value).model_dump()
        return await base(system, data, **kw)

    async def search(action):
        events.append("search")
        assert action.query == "queue operation" and "answer" not in events
        return {"assets": []}

    async def fetch(action):
        return {"assets": []}

    monkeypatch.setattr(tools, "model", model)
    monkeypatch.setattr(tools, "search", search)
    monkeypatch.setattr(tools, "fetch", fetch)
    stop = asyncio.run(Controller(tools).run())
    assert "coverage_refresh" not in events
    assert (
        events.index("initial")
        < events.index("task_recall")
        < events.index("search")
        < events.index("after_coverage_search")
        < events.index("extraction_batch")
        < events.index("answer")
    )
    assert any(
        t.kind == "read_documents" and t.state == "succeeded"
        for t in repo.rows(ResearchTask)
    )
    assert tools.evidence("main")
    diagnosis = freeze(tools, stop).manifest["material_selection"]["main"]
    assert diagnosis["read_count"] == diagnosis["selected_count"] == 1
    assert diagnosis["evidence_count"] > 0


@pytest.mark.parametrize("env18", ["question_loop_v18"], indirect=True)
@pytest.mark.parametrize("have_candidates", [True, False])
def test_empty_reading_is_not_ready_and_actual_gap_reaches_frozen_report_and_api(
    env18, monkeypatch, client, have_candidates
):
    from backend.routers import exploration
    from backend.config import settings

    repo, tools = env18
    overview(repo, tools)
    if not have_candidates:
        with repo.transaction() as session:
            for a in tools.documents():
                a.status = "superseded"
                session.add(a)
    stages = []

    async def model(system, data, **kw):
        stage = data["stage"]
        stages.append(stage)
        value = {
            "initial": {"classification": None},
            "task_recall": {"task_searches": []},
            "after_coverage_search": {"representatives": []},
        }[stage]
        return (
            kw["schema"]
            .model_validate(
                {**value, "gaps": ["尚缺任务内原始方法资料，未确认操作和测试判据"]}
            )
            .model_dump()
        )

    monkeypatch.setattr(tools, "model", model)
    stop = asyncio.run(Controller(tools).run())
    assert stop == "material_selection_empty"
    assert stages == (
        ["initial", "task_recall", "after_coverage_search"]
        if have_candidates
        else ["task_recall"]
    )
    dossier = freeze(tools, stop)
    report = build_report(tools, dossier)
    assert report["delivery"]["answer_status"] == "none"
    expected = "可读候选" if have_candidates else "没有可读准入候选"
    assert expected in report["chapters"][0]["text"]
    if have_candidates:
        assert "未确认操作和测试判据" in report["chapters"][0]["text"]
        assert report["status"] == "partial" and "已召回可读候选" in report["summary"]
    else:
        assert report["status"] == "no_results" and "未取得可读" in report["summary"]
    diagnosis = report["method"]["material_selection"]["main"]
    assert diagnosis["selection_status"] == (
        "insufficient" if have_candidates else "no_candidates"
    )
    assert diagnosis["read_count"] == 0
    monkeypatch.setattr(exploration, "get_engine", lambda: repo.engine)
    origin = {"Origin": settings.frontend_origin}
    path = f"/api/exploration/runs/{repo.run_id}/questions"
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
    assert response.json()["items"][0]["gaps"] == dossier.manifest["answers"][0]["gaps"]


@pytest.mark.parametrize("env18", ["question_loop_v18"], indirect=True)
def test_valid_initial_candidate_can_be_read_before_final_support_is_known(
    env18, monkeypatch
):
    repo, tools = env18
    overview(repo, tools)
    seen = []
    base = model15(tools)

    async def model(system, data, **kw):
        seen.append(kw["stage"])
        if kw["stage"].startswith("reading_portfolio"):
            seen.append(data["stage"])
            if data["stage"] == "initial":
                assert not data["sources"]
                value = {
                    "classification": {
                        k: v
                        for k, v in choice(
                            data["candidates"][0]["document_ref"]
                        ).items()
                        if k != "unit_refs"
                    },
                    "gaps": ["分类细节与限制需要阅读正文核查"],
                }
            elif data["stage"] == "coverage_refresh":
                assert data["sources"] and data["material_state"]["read_count"] == 1
                value = {"next_searches": [], "gaps": ["已读材料没有明确下一方法名字"]}
            elif data["stage"] == "task_recall":
                value = {"task_searches": [], "gaps": []}
            else:
                value = {"representatives": [], "gaps": []}
            return kw["schema"].model_validate(value).model_dump()
        return await base(system, data, **kw)

    async def fetch(action):
        return {"assets": []}

    monkeypatch.setattr(tools, "model", model)
    monkeypatch.setattr(tools, "fetch", fetch)
    asyncio.run(Controller(tools).run())
    assert (
        seen.index("extraction_batch")
        < seen.index("coverage_refresh")
        < seen.index("task_recall")
        < seen.index("answer")
    )
    assert tools.evidence("main")


@pytest.mark.parametrize("env18", ["question_loop_v18"], indirect=True)
def test_real_source_navigation_still_runs_after_reading_and_before_answer(
    env18, monkeypatch
):
    repo, tools = env18
    overview(repo, tools)
    events = []
    base = model15(tools)

    async def model(system, data, **kw):
        events.append(kw["stage"])
        if kw["stage"].startswith("reading_portfolio"):
            events.append(data["stage"])
            if data["stage"] == "initial":
                value = {
                    "classification": {
                        k: v
                        for k, v in choice(
                            data["candidates"][0]["document_ref"]
                        ).items()
                        if k != "unit_refs"
                    },
                    "gaps": [],
                }
            elif data["stage"] == "coverage_refresh":
                assert data["sources"]
                value = {
                    "next_searches": [
                        {
                            "source_id": data["sources"][0]["source_id"],
                            "source_quote": "AlphaNet stores a bounded queue",
                            "query": "AlphaNet queue",
                            "unit_refs": ["U1"],
                            "reason": "从真实原文指向下一次阅读",
                        }
                    ],
                    "gaps": [],
                }
            else:
                assert data["stage"] == "after_coverage_search"
                value = {"representatives": [], "gaps": []}
            return kw["schema"].model_validate(value).model_dump()
        return await base(system, data, **kw)

    async def search(action):
        events.append("source_search")
        assert action.query == "AlphaNet queue"
        return {"assets": []}

    async def fetch(action):
        return {"assets": []}

    monkeypatch.setattr(tools, "model", model)
    monkeypatch.setattr(tools, "fetch", fetch)
    monkeypatch.setattr(tools, "search", search)
    asyncio.run(Controller(tools).run())
    assert (
        events.index("extraction_batch")
        < events.index("coverage_refresh")
        < events.index("source_search")
        < events.index("answer")
    )
    assert "task_recall" not in events
    audit = next(
        a.inline_payload
        for a in repo.rows(ResearchAsset)
        if a.content_kind == "portfolio_attempt"
        and a.inline_payload["stage"] == "coverage_refresh"
    )
    assert (
        audit["input"]["material_state"]["read_count"] == 1
        and audit["input"]["sources"]
    )


@pytest.mark.parametrize("env18", ["question_loop_v18"], indirect=True)
@pytest.mark.parametrize("tampered", [False, True])
def test_v18_real_planner_api_projects_only_same_protocol_frozen_requirements(
    env18, monkeypatch, client, tampered
):
    from backend.routers import exploration
    from backend.tests.test_research_planning_v16 import ScopePlanner
    from backend.config import settings

    repo, tools = env18
    q = repo.rows(ResearchQuestion)[0]
    monkeypatch.setattr(tools, "model", ScopePlanner().model)
    specs = [
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
    plan = asyncio.run(tools.make_plan(specs))
    with repo.transaction() as session:
        for a in repo.rows(ResearchAsset):
            if a.content_kind == "research_plan":
                a.status = "superseded"
                session.add(a)
    tools.save_plan(plan)
    assert tools.plan()["execution_revision"] == "question_loop_v18"
    if tampered:
        with repo.transaction() as session:
            a = next(
                a
                for a in repo.rows(ResearchAsset)
                if a.content_kind == "research_plan" and a.status == "active"
            )
            a.inline_payload = {
                **a.inline_payload,
                "protocol_hash": "another frozen protocol",
            }
            a.content_hash = digest(a.inline_payload)
            session.add(a)
    monkeypatch.setattr(exploration, "get_engine", lambda: repo.engine)
    response = client.get(
        f"/api/exploration/runs/{repo.run_id}/questions",
        headers={"Origin": settings.frontend_origin},
    )
    assert (
        response.status_code == 200
        and response.headers["access-control-allow-origin"] == settings.frontend_origin
    )
    projected = response.json()["items"][0]["requirements"]
    if tampered:
        assert projected == []
    else:
        original = plan["requirement_ledger"]["main"]
        assert [
            (u["unit_id"], u["requirement"], u["source_quote"]) for u in projected
        ] == [(u["unit_id"], u["requirement"], u["source_quote"]) for u in original]
