"""No-paid-request regressions for actual portfolio/answer/budget transitions."""

import asyncio
import json
from copy import deepcopy

import pytest
from sqlmodel import Session

from backend.models.exploration import ExplorationRun
from backend.models.research_core import ResearchAsset, ResearchQuestion
from backend.services.deep_research.research_loop.contracts import (
    Action,
    AtomicFact,
    digest,
    SUPPORTED_LOOP_VERSIONS,
)
from backend.services.deep_research.research_loop.repository import Repository
from backend.services.deep_research.research_loop.resources import (
    Resources,
    limits_for,
    ResourceExhausted,
)
from backend.services.deep_research.research_loop.tools import Tools
from backend.services.deep_research.research_loop.controller import Controller
from backend.services.deep_research.research_loop.evidence import validated_evidence
from backend.services.deep_research.research_loop.portfolio_v10 import (
    validate,
    make,
    latest,
)
from backend.services.deep_research.research_loop.answer_budget_v10 import (
    envelope,
    model_cost,
    can_read,
)
from backend.services.deep_research.research_loop.answering_v10 import (
    Answer,
    ANSWER_SYSTEM,
)
from backend.services.deep_research.research_loop.material_v10 import outside_scope
from backend.services.deep_research.research_loop.dossier import freeze
from backend.services.deep_research.research_loop.writer import build_report
from backend.tests.test_research_intent_v9 import (
    Planner,
    specs,
    make_anchored_plan,
    positive,
)


@pytest.fixture
def v10_env(engine):
    with Session(engine) as session:
        run = ExplorationRun(
            question=specs()[0]["text"],
            depth="quick",
            workflow_version="academic_survey_v2",
            state_version=1,
            options={
                "search_online": False,
                "workflow": {"loop_revision": "question_loop_v10"},
            },
        )
        session.add(run)
        session.commit()
        run_id = run.id
    repo = Repository(engine, run_id, "v10-worker")
    assert repo.acquire()
    limits = limits_for("quick")
    repo.initialize(specs(), limits)
    tools = Tools(repo, Resources(repo, limits), {})
    plan = asyncio.run(make_anchored_plan(Planner(), specs()))
    plan["reading_roles"][0]["role"] = "overview"
    plan["targets"] = []
    tools.save_plan(plan)
    with repo.transaction() as session:
        for name, role, text in [
            (
                "Overview",
                "survey",
                "Queue policies include eviction and admission rejection.",
            ),
            (
                "AlphaNet",
                "method",
                "AlphaNet stores a bounded queue and drops the oldest entry when the queue is full.",
            ),
            (
                "BetaNet",
                "method",
                "BetaNet stores a bounded queue and rejects the newest entry when the queue is full.",
            ),
        ]:
            spec = tools.document_spec(
                {
                    "key": name,
                    "title": name + ": Bounded queues",
                    "year": 2023,
                    "research_role": role,
                    "abstract": text,
                    "primary_targets": [name] if role == "method" else [],
                },
                text=text,
                source_kind="full_text",
            )
            session.add(ResearchAsset(run_id=run_id, **spec))
    return repo, tools


def choices(tools):
    units = tools.plan()["requirement_ledger"]["main"]
    return {
        "choices": [
            {
                "asset_id": a.asset_id,
                "role": "classification"
                if a.inline_payload["paper"]["research_role"] == "survey"
                else "representative",
                "requirement_ids": [u["unit_id"] for u in units],
                "focus_queries": ["queue capacity overflow mechanism"],
                "reason": "Complementary actual source",
            }
            for a in tools.documents()
        ],
        "gaps": [],
    }


@pytest.mark.parametrize(
    "change",
    [
        "unknown_asset",
        "unknown_requirement",
        "duplicate_title",
        "survey_representative",
    ],
)
def test_portfolio_rejects_cross_identity_or_new_requirements(v10_env, change):
    _, tools = v10_env
    raw = choices(tools)
    if change == "unknown_asset":
        raw["choices"][0]["asset_id"] = "another-run"
    elif change == "unknown_requirement":
        raw["choices"][0]["requirement_ids"] = ["invented"]
    elif change == "duplicate_title":
        raw["choices"].append(deepcopy(raw["choices"][0]))
    else:
        raw["choices"][0]["role"] = "representative"
    with pytest.raises(ValueError):
        validate(raw, tools.documents(), tools.plan()["requirement_ledger"]["main"])


def test_portfolio_is_frozen_restorable_navigation_not_a_new_fact(v10_env, monkeypatch):
    repo, tools = v10_env
    calls = []

    async def model(system, data, **kwargs):
        calls.append(data)
        return choices(tools)

    monkeypatch.setattr(tools, "model", model)
    question = repo.rows(ResearchQuestion)[0]
    before = deepcopy(tools.plan())
    one = asyncio.run(make(tools, question))
    two = asyncio.run(make(tools, question))
    assert one == two and len(calls) == 1 and latest(tools, "main") == one
    assert not tools.evidence("main") and tools.plan() == before
    assert [c["asset_id"] for c in one["choices"]] == [
        a.asset_id for a in tools.documents()
    ]


def test_read_complementary_sources_before_first_answer_then_preserve_report(
    v10_env, monkeypatch
):
    repo, tools = v10_env

    async def portfolio(system, data, **kwargs):
        return choices(tools)

    monkeypatch.setattr(tools, "model", portfolio)
    control = Controller(tools)
    planning = asyncio.run(control.choose())
    assert planning.tool == "plan_reading"
    repo.complete_task(*repo.claim_task(planning), asyncio.run(tools.execute(planning)))
    action = asyncio.run(control.choose())
    assert action.tool == "read_documents" and len(action.asset_ids) == 3
    facts = [
        validated_evidence(
            repo.run_id,
            "main",
            a,
            AtomicFact(fact=a.inline_payload["text"], quote=a.inline_payload["text"]),
            {"supported": True, "relevant": True},
            reviewer_version="question_loop_v10",
        )
        for a in tools.documents()
    ]
    repo.complete_task(*repo.claim_task(action), {"evidence": facts})
    assert asyncio.run(control.choose()).tool == "assess_answer"
    known = {e["evidence_id"]: e for e in tools.evidence("main")}

    async def answer_model(system, data, **kwargs):
        if kwargs["stage"] == "answer":
            rows = []
            for u in data["requirements"]:
                eid = next(eid for eid, e in known.items() if u["subject"] in e["fact"])
                rows.append(
                    {
                        "unit_id": u["unit_id"],
                        "state": "answered",
                        "claims": [
                            {
                                "text": known[eid]["fact"],
                                "kind": "fact",
                                "evidence_ids": [eid],
                            }
                        ],
                    }
                )
            return {
                "units": rows,
                "explanations": [],
                "conditions": [],
                "relations": [],
            }
        return {
            "claims": [positive(c, known) for c in data["claims"]],
            "units": [
                {
                    "unit_id": u["unit_id"],
                    "complete": True,
                    "reason": "Exact original mechanism",
                }
                for u in data["units"]
            ],
            "relations": [],
            "conditions_preserved": True,
            "conflicts": [],
        }

    monkeypatch.setattr(tools, "model", answer_model)
    action = asyncio.run(control.choose())
    result = asyncio.run(tools.assess(action))
    repo.complete_task(*repo.claim_task(action), result)
    report = build_report(tools, freeze(tools, "required_questions_supported"))
    assert (
        result["answer"]["status"] == "supported"
        and "AlphaNet" in report["markdown"]
        and "BetaNet" in report["markdown"]
    )
    assert len(report["references"]) == 2


def test_budget_matches_core_serialization_and_grows_with_actual_material(v10_env):
    from backend.services.llm.client import _estimate_input_tokens

    _, tools = v10_env
    data = {"question": "有界队列", "unicode": "正常材料"}
    system = (
        ANSWER_SYSTEM
        + "\n严格遵守 JSON schema："
        + json.dumps(Answer.model_json_schema(), ensure_ascii=False)
    )
    assert (
        model_cost(ANSWER_SYSTEM, data, Answer, 2000)
        == _estimate_input_tokens(
            system, json.dumps(data, ensure_ascii=False, default=str)
        )
        + 2000
    )
    before = envelope(tools, "main")
    after = envelope(tools, "main", future_sources=2)
    assert (
        after["llm_tokens"] > before["llm_tokens"]
        and after["llm_calls"] == before["llm_calls"] == 2
    )
    assert can_read(tools, "main", 1)
    tools.resources.charge(
        "llm_tokens",
        tools.resources.remaining("llm_tokens") - before["llm_tokens"] - 1000,
    )
    assert not can_read(tools, "main", 1)
    tools.resources.account = "reading"
    with pytest.raises(ResourceExhausted):
        tools.resources.before_model_attempt(
            estimated_output_tokens=2000, estimated_input_tokens=1000
        )
    assert tools.resources.remaining("llm_tokens") == before["llm_tokens"] + 1000


def test_low_reading_budget_prefers_existing_answer_over_unread_source(
    v10_env, monkeypatch
):
    repo, tools = v10_env

    async def model(system, data, **kwargs):
        return choices(tools)

    monkeypatch.setattr(tools, "model", model)
    asyncio.run(make(tools, repo.rows(ResearchQuestion)[0]))
    asset = tools.documents()[1]
    evidence = validated_evidence(
        repo.run_id,
        "main",
        asset,
        AtomicFact(
            fact=asset.inline_payload["text"], quote=asset.inline_payload["text"]
        ),
        {"supported": True, "relevant": True},
        reviewer_version="question_loop_v10",
    )
    repo.complete_task(
        *repo.claim_task(
            Action(
                tool="read_documents", question_id="main", asset_ids=[asset.asset_id]
            )
        ),
        {"evidence": [evidence]},
    )
    needed = envelope(tools, "main")["llm_tokens"]
    tools.resources.charge(
        "llm_tokens", tools.resources.remaining("llm_tokens") - needed - 1000
    )
    action = asyncio.run(Controller(tools).choose())
    assert action.tool == "assess_answer"


@pytest.mark.parametrize(
    "fact,reject",
    [
        ("Outlier Exposure使用NLP英文维基百科并在已知异常上训练。", True),
        ("模型在已标注异常上训练二分类器。", True),
        ("正常图像建立记忆库，测试计算距离。", False),
        ("只用正常图像训练，并用人工生成异常辅助学习。", False),
        ("无需已标注异常训练样本。", False),
        ("视频异常用时间序列建立评分。", True),
    ],
)
def test_image_unsupervised_boundary_does_not_reject_synthetic_anomaly(fact, reject):
    assert outside_scope("无监督工业缺陷检测，只讨论图像。", fact) == reject


def test_optional_followup_budget_failure_keeps_checked_partial_answer(
    v10_env, monkeypatch
):
    repo, tools = v10_env
    a = tools.documents()[1]
    ev = validated_evidence(
        repo.run_id,
        "main",
        a,
        AtomicFact(fact=a.inline_payload["text"], quote=a.inline_payload["text"]),
        {"supported": True, "relevant": True},
        reviewer_version="question_loop_v10",
    )
    repo.complete_task(
        *repo.claim_task(
            Action(tool="read_documents", question_id="main", asset_ids=[a.asset_id])
        ),
        {"evidence": [ev]},
    )
    known = {e["evidence_id"]: e for e in tools.evidence("main")}
    eid = next(iter(known))

    async def model(system, data, **kwargs):
        if kwargs["stage"] == "answer":
            return {
                "units": [
                    {
                        "unit_id": u["unit_id"],
                        "state": "unknown",
                        "claims": [
                            {"text": ev["fact"], "kind": "fact", "evidence_ids": [eid]}
                        ],
                    }
                    for u in data["requirements"]
                ],
                "explanations": [],
                "conditions": [],
                "relations": [],
            }
        if kwargs["stage"] == "answer_check":
            return {
                "claims": [positive(c, known) for c in data["claims"]],
                "units": [
                    {
                        "unit_id": u["unit_id"],
                        "complete": False,
                        "reason": "Missing some subjects",
                    }
                    for u in data["units"]
                ],
                "relations": [],
                "conditions_preserved": True,
                "conflicts": [],
            }
        raise ResourceExhausted(
            "Optional next-step planning exceeds remaining envelope"
        )

    monkeypatch.setattr(tools, "model", model)
    result = asyncio.run(tools.assess(Controller(tools).assessment_action("main")))
    assert (
        result["answer"]["status"] == "insufficient"
        and "AlphaNet" in result["answer"]["conclusion"]
    )
    assert (
        result["answer"]["assessment"]["followup_stop_reason"] == "ResourceExhausted"
        and result["assets"] == []
    )


def test_history_version_family_is_explicit():
    assert SUPPORTED_LOOP_VERSIONS == tuple(f"question_loop_v{i}" for i in range(1, 21))


def test_reading_plan_action_is_not_allowed_for_historical_executor(v10_env):
    repo, tools = v10_env
    with repo.transaction() as session:
        asset = session.get(ResearchAsset, repo.asset(repo.run().protocol_asset_id).id)
        payload = {**asset.inline_payload, "execution_revision": "question_loop_v9"}
        asset.inline_payload = payload
        session.add(asset)
    assert not Controller(tools).available(
        Action(tool="plan_reading", question_id="main")
    )


def test_body_resolves_actual_title_and_budget_hold_resets_after_failure(
    v10_env, monkeypatch
):
    from backend.services.deep_research.research_loop.portfolio_v10 import resolve
    from backend.services.deep_research.research_loop import reading

    repo, tools = v10_env
    original = tools.documents()[1]
    with repo.transaction() as session:
        raw = tools.document_spec(
            original.inline_payload["paper"],
            text=original.inline_payload["text"],
            source_kind="abstract",
        )
        asset = ResearchAsset(run_id=repo.run_id, **raw)
        session.add(asset)
        session.flush()
        aid = asset.asset_id
    chosen = {"asset_id": aid, "reason": "Call this different method GammaNet"}
    assert (
        resolve(tools, chosen).inline_payload["paper"]["title"].startswith("AlphaNet")
    )

    async def failure(tools, action):
        assert tools.resources.answer_hold_tokens > 0
        raise ResourceExhausted("Stop before consuming final answer reserve")

    monkeypatch.setattr(reading, "read_anchored_batch", failure)
    with pytest.raises(ResourceExhausted):
        asyncio.run(
            tools.read_batch(
                Action(
                    tool="read_documents",
                    question_id="main",
                    asset_ids=[original.asset_id],
                )
            )
        )
    assert tools.resources.answer_hold_tokens == 0


def test_attributed_mechanism_survives_agenda_filter_but_agenda_is_not_evidence():
    from backend.services.deep_research.research_loop.material_v10 import (
        description_only,
    )

    assert not description_only(
        "该论文保存正常图像特征，测试时计算与正常记忆的距离作为评分。"
    )
    assert description_only("该综述介绍了十种技术并讨论未来方向。")


def test_v10_plan_task_and_question_contract_cors_without_paid_request(
    v10_env, client, monkeypatch
):
    from backend.config import settings
    from backend.routers import exploration

    repo, tools = v10_env

    async def model(system, data, **kwargs):
        return choices(tools)

    monkeypatch.setattr(tools, "model", model)
    action = asyncio.run(Controller(tools).choose())
    repo.complete_task(*repo.claim_task(action), asyncio.run(tools.execute(action)))
    monkeypatch.setattr(exploration, "get_engine", lambda: repo.engine)
    origin = {"Origin": settings.frontend_origin}
    for suffix in ("questions", "tasks"):
        path = f"/api/exploration/runs/{repo.run_id}/{suffix}"
        assert (
            client.options(
                path, headers={**origin, "Access-Control-Request-Method": "GET"}
            ).status_code
            == 200
        )
        response = client.get(path, headers=origin)
        assert (
            response.status_code == 200
            and response.headers["access-control-allow-origin"]
            == settings.frontend_origin
        )
        item = response.json()["items"][0]
        if suffix == "tasks":
            assert item["kind"] == "plan_reading" and item["state"] == "succeeded"
        else:
            assert len(item["requirements"]) == 2 and item["reading_role"] == "overview"
            assert item["scope_constraints"] == ["只研究有界队列，不做性能排名"]


def test_answer_reserve_tracks_current_question_in_multi_question_run(v10_env):
    repo, tools = v10_env
    with repo.transaction() as session:
        session.add(
            ResearchQuestion(
                run_id=repo.run_id,
                question_id="q1",
                protocol_version=1,
                text="Explain another queue policy",
                required=True,
                priority=2,
                intent="understand",
                answer_criteria=["Explain the actual mechanism"],
                expected_evidence=["fact"],
            )
        )
    tools.resources.current_question_id = "q1"
    assert tools.resources.answer_reserve_provider() == envelope(tools, "q1")
    assert tools.resources.answer_reserve_provider() != envelope(tools, "main")
