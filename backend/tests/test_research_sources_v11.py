"""Source fidelity, abstract scope and real controller transitions without paid calls."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from backend.models.exploration import ExplorationRun
from backend.models.research_core import (
    ResearchAsset,
    ResearchQuestion,
    ResearchAnswerRevision,
)
from backend.services.deep_research.research_loop.contracts import (
    Action,
    AtomicFact,
    LOOP_VERSION,
    SUPPORTED_LOOP_VERSIONS,
)
from backend.services.deep_research.research_loop.repository import Repository
from backend.services.deep_research.research_loop.resources import Resources, limits_for
from backend.services.deep_research.research_loop.tools import Tools
from backend.services.deep_research.research_loop.controller import Controller
from backend.services.deep_research.research_loop.source_refs_v11 import (
    wire_context,
    resolve_answer,
    checker_payload,
    resolve_checks,
)
from backend.services.deep_research.research_loop.answering_v11 import (
    check_claims,
    bind_claims,
)
from backend.services.deep_research.research_loop.reading_v11 import validate
from backend.services.deep_research.research_loop.answer_budget_v11 import envelope
from backend.services.deep_research.research_loop.material_v11 import passages
from backend.services.deep_research.research_loop.dossier import freeze
from backend.services.deep_research.research_loop.writer import build_report
from backend.services.deep_research.research_loop.intent_v11 import make_anchored_plan
from backend.tests.test_research_intent_v9 import Planner, specs


@pytest.fixture
def env11(engine, request):
    question = "解释AlphaNet怎样管理队列，并提出部署核查建议。"
    spec = {
        "question_id": "main",
        "text": question,
        "answer_criteria": ["解释队列管理", "提出核查建议"],
        "required": True,
    }
    with Session(engine) as session:
        run = ExplorationRun(
            question=question,
            depth="quick",
            workflow_version="academic_survey_v2",
            state_version=1,
            options={
                "search_online": False,
                "workflow": {"loop_revision": getattr(request, "param", "question_loop_v11")},
            },
        )
        session.add(run)
        session.commit()
        rid = run.id
    repo = Repository(engine, rid, "source-worker")
    assert repo.acquire()
    limits = limits_for("quick")
    repo.initialize([spec], limits)
    tools = Tools(repo, Resources(repo, limits), {})
    tools.save_plan(
        {
            "strategy": "material_first_v3",
            "queries": [],
            "targets": [],
            "reading_queries": ["queue capacity overflow"],
            "reading_roles": [{"question_id": "main", "role": "mechanism"}],
            "constraints": {"main": []},
            "intent_coverage": {"complete": True, "gaps_by_question": {}},
            "requirement_ledger": {
                "main": [
                    {
                        "unit_id": "req_queue",
                        "question_id": "main",
                        "requirement": "解释队列管理",
                        "subject": "AlphaNet",
                        "criterion_index": 0,
                        "response_kind": "fact",
                    },
                    {
                        "unit_id": "req_test",
                        "question_id": "main",
                        "requirement": "提出核查建议",
                        "subject": "",
                        "criterion_index": 1,
                        "response_kind": "inference",
                    },
                ]
            },
        }
    )
    with repo.transaction() as session:
        session.add(
            ResearchAsset(
                run_id=rid,
                **tools.document_spec(
                    {
                        "key": "AlphaNet",
                        "title": "AlphaNet bounded queue",
                        "primary_targets": ["AlphaNet"],
                        "year": 2024,
                    },
                    text="AlphaNet stores a bounded queue and drops the oldest entry when the queue is full.",
                    source_kind="abstract",
                ),
            )
        )
    return repo, tools


def source_model(tools, unsupported=False):
    async def model(system, data, **kw):
        if kw["stage"] == "extraction_batch":
            return {
                "facts": [
                    {
                        "source_id": data["sources"][0]["source_id"],
                        "fact": "AlphaNet保存有界队列，队列满时删除最旧条目。",
                        "evidence_kind": "mechanism",
                        "conditions": {"when": "队列满时"},
                        "missing_fields": [],
                    }
                ]
            }
        if kw["stage"] == "evidence_check_batch":
            return {
                "checks": [
                    {
                        "fact_id": f["fact_id"],
                        "supported": True,
                        "relevant": True,
                        "unsupported_parts": ["缺少操作依据"] if unsupported else [],
                        "reason": "Actual source operation",
                    }
                    for f in data["facts"]
                ]
            }
        if kw["stage"] == "answer":
            eid = data["facts"][0]["evidence_id"]
            return {
                "units": [
                    {
                        "unit_id": u["unit_id"],
                        "state": "answered",
                        "claims": [
                            {
                                "text": (
                                    "AlphaNet保存有界队列，队列满时删除最旧条目。"
                                    if i == 0
                                    else "依据删除最旧条目的规则，可核查队列满时旧条目的处理是否符合需求。"
                                ),
                                "kind": u["response_kind"],
                                "evidence_ids": [eid],
                            }
                        ],
                    }
                    for i, u in enumerate(data["requirements"])
                ],
                "explanations": [],
                "conditions": [],
                "relations": [],
            }
        if kw["stage"] == "answer_check":
            assert "facts" not in data and "proofs" not in data
            return {
                "claims": [
                    {
                        "claim_id": c["claim_id"],
                        "supported": True,
                        "relevant": True,
                        "conditions_preserved": True,
                        "support_mode": "direct"
                        if c["kind"] == "fact"
                        else "inference",
                        "supported_by": [data["sources"][0]["source_id"]],
                        "unsupported_parts": [],
                        "reason": "source premise",
                        "added_information": False,
                    }
                    for c in data["claims"]
                ],
                "units": [
                    {
                        "unit_id": u["unit_id"],
                        "complete": True,
                        "reason": "all required",
                    }
                    for u in data["requirements"]
                ],
                "relations": [],
                "conditions_preserved": True,
                "conflicts": [],
            }
        raise AssertionError(kw["stage"])

    return model


def test_actual_controller_read_assess_freeze_and_report_abstract_conditions(
    env11, monkeypatch
):
    repo, tools = env11
    monkeypatch.setattr(tools, "model", source_model(tools))
    stop = asyncio.run(Controller(tools).run())
    assert stop == "required_questions_supported"
    evidence = tools.evidence("main")
    assert len(evidence) == 1 and evidence[0]["source_kind"] == "abstract"
    assert evidence[0]["conditions"] == {"when": "队列满时"}
    dossier = freeze(tools, stop)
    report = build_report(tools, dossier)
    assert report["quality_status"] == "internally_checked"
    assert (
        "2 / 2" in report["summary"]
        and "source_type" not in report["chapters"][0]["text"]
    )
    answer = repo.rows(ResearchAnswerRevision)[0]
    assert answer.assessment["human_reviewed"] is False
    assert (
        answer.assessment["candidate_answer"]
        and not answer.assessment["rejected_claim_details"]
    )
    assert all(
        s["evidence_id"] == evidence[0]["evidence_id"]
        for c in answer.assessment["claim_checks"]
        for s in c["supported_by"]
    )


def test_reader_rejection_diagnostic_preserves_missing_action(env11, monkeypatch):
    _, tools = env11
    monkeypatch.setattr(tools, "model", source_model(tools, unsupported=True))
    action = Action(
        tool="read_documents",
        question_id="main",
        asset_ids=[tools.documents()[0].asset_id],
    )
    output = asyncio.run(tools.read_batch(action))
    assert not output["evidence"]
    reject = output["usage"]["extraction_diagnostics"]["rejections"][0]
    assert (
        reject["code"] == "unsupported_fact_parts"
        and reject["check"]["unsupported_parts"]
    )
    assert tools.resources.answer_hold_tokens == 0


def sample_context():
    known = {
        "ev_real_1": {
            "evidence_id": "ev_real_1",
            "asset_id": "asset_1",
            "fact": "summary1",
            "spans": [{"quote": "A source stores normal vectors."}],
            "source_kind": "full_text",
            "conditions": {},
            "missing_fields": [],
        },
        "ev_real_2": {
            "evidence_id": "ev_real_2",
            "asset_id": "asset_2",
            "fact": "summary2",
            "spans": [{"quote": "Another source reconstructs test images."}],
            "source_kind": "full_text",
            "conditions": {},
            "missing_fields": [],
        },
    }
    papers = {eid: {"title": "source" + eid} for eid in known}
    return known, wire_context(
        [{"unit_id": "req_long", "requirement": "mechanism"}], known, papers
    )


def candidate():
    return {
        "units": [
            {
                "unit_id": "U1",
                "state": "answered",
                "claims": [
                    {"text": "保存正常向量。", "kind": "fact", "evidence_ids": ["E1"]}
                ],
            }
        ],
        "explanations": [],
        "conditions": [],
        "relations": [],
    }


@pytest.mark.parametrize(
    "source,code",
    [("S1", None), ("S2", "source_not_cited_by_claim"), ("S99", "unknown_source_ref")],
)
def test_selected_source_is_bound_to_this_claim(source, code):
    known, context = sample_context()
    raw = resolve_answer(candidate(), context)
    claims, _, _ = bind_claims(raw)
    data, mapping = checker_payload(claims, raw, context)
    assert not any("summary1" in str(v) for v in data.values())
    verdict = {
        "claims": [
            {
                "claim_id": "C1",
                "supported": True,
                "relevant": True,
                "conditions_preserved": True,
                "support_mode": "direct",
                "supported_by": [source],
                "unsupported_parts": [],
                "reason": "checked",
            }
        ],
        "units": [{"unit_id": "U1", "complete": True}],
        "conflicts": [],
        "relations": [],
    }
    check = resolve_checks(verdict, context, mapping)
    accepted, rejected = check_claims(claims, check["claims"], known)
    if code:
        assert not accepted and rejected[0]["code"] == code
    else:
        assert (
            accepted
            and not rejected
            and check["claims"][0]["supported_by"][0]["quote"]
            == known["ev_real_1"]["spans"][0]["quote"]
        )


def test_unknown_alias_is_not_repaired_from_memory():
    known, context = sample_context()
    raw = candidate()
    raw["units"][0]["claims"][0]["evidence_ids"] = ["ev_real_1"]
    decoded = resolve_answer(raw, context)
    claims, _, _ = bind_claims(decoded)
    accepted, rejected = check_claims(
        claims, [{"claim_id": claims[0]["claim_id"], "supported": True}], known
    )
    assert not accepted and rejected[0]["code"] == "unknown_evidence_ref"


def test_incomplete_positive_semantic_check_is_rejected():
    known, _ = sample_context()
    claim = {
        "claim_id": "c",
        "text": "保存并评分。",
        "kind": "fact",
        "evidence_ids": ["ev_real_1"],
    }
    check = {
        "claim_id": "c",
        "supported": True,
        "relevant": True,
        "conditions_preserved": True,
        "support_mode": "direct",
        "unsupported_parts": ["source never defines scoring"],
        "supported_by": [
            {"evidence_id": "ev_real_1", "quote": "A source stores normal vectors."}
        ],
    }
    accepted, rejected = check_claims([claim], [check], known)
    assert not accepted and rejected[0]["code"] == "unsupported_claim_parts"


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("mechanism", True),
        ("condition", True),
        ("comparison", False),
        ("limitation", False),
    ],
)
def test_explicit_abstract_conditions_with_bounded_scope(env11, kind, expected):
    _, tools = env11
    asset = tools.documents()[0]
    question = tools.repo.rows(ResearchQuestion)[0]
    fact = AtomicFact(
        fact="AlphaNet队列满时删除最旧条目。",
        quote=asset.inline_payload["text"],
        evidence_kind=kind,
        conditions={"when": "队列满时"},
    )
    row, code = validate(
        tools,
        question,
        asset,
        fact,
        {"supported": True, "relevant": True, "unsupported_parts": []},
    )
    assert bool(row) == expected
    if row:
        assert (
            row["verification"]["status"] == "abstract_supported"
            and row["conditions"] == fact.conditions
        )
    else:
        assert code == "abstract_detail_forbidden"


def test_chinese_adjacent_unknown_method_name_rejected(env11):
    _, tools = env11
    asset = tools.documents()[0]
    question = tools.repo.rows(ResearchQuestion)[0]
    fact = AtomicFact(
        fact="本方法采用OtherNet并保存队列。", quote=asset.inline_payload["text"]
    )
    row, code = validate(
        tools,
        question,
        asset,
        fact,
        {"supported": True, "relevant": True, "unsupported_parts": []},
    )
    assert row is None and code == "unanchored_technical_name"


def test_material_keeps_trailing_scoring_sentence(env11, monkeypatch):
    _, tools = env11
    asset = SimpleNamespace(
        asset_id=tools.documents()[0].asset_id,
        inline_payload=deepcopy(dict(tools.documents()[0].inline_payload)),
    )
    asset.inline_payload = {**asset.inline_payload, "source_kind": "full_text"}
    text = (
        "Header. "
        + ("x " * 880)
        + "The test score is the distance to normal vectors. "
        + "Other. " * 100
    )
    asset.inline_payload["text"] = text
    from backend.services.deep_research.research_loop import material

    monkeypatch.setattr(
        material,
        "body_material",
        lambda *a, **kw: [{"passage_id": "p0", "text": text[:1800]}],
    )
    chunks = passages(asset, "explain mechanism", tools.plan())
    assert "The test score is the distance to normal vectors." in chunks[0]["text"]
    assert (
        all(c["text"] in text for c in chunks)
        and sum(len(c["text"]) for c in chunks) <= 8000
    )


def test_planner_independently_corrects_fact_inference_confusion():
    old = Planner()

    class CheckedPlanner:
        async def model(self, system, data, **kw):
            value = await old.model(system, data, **kw)
            if kw["stage"] == "planning":
                value["requests"][0]["response_kind"] = "inference"
            if kw["stage"].startswith("planning_check"):
                value["response_kinds"] = [
                    {
                        "unit_id": u["unit_id"],
                        "response_kind": "fact",
                        "reason": "existing method explanation",
                    }
                    for u in data["requirements"]
                ]
            return value

    plan = asyncio.run(make_anchored_plan(CheckedPlanner(), specs()))
    assert plan["intent_coverage"]["complete"]
    assert all(u["response_kind"] == "fact" for u in plan["requirement_ledger"]["main"])


def test_budget_grows_with_real_source_and_keeps_old_version(env11):
    _, tools = env11
    before = envelope(tools, "main")
    after = envelope(tools, "main", future_sources=2)
    assert after["llm_tokens"] > before["llm_tokens"] and after["llm_calls"] == 2
    assert (
        LOOP_VERSION == "question_loop_v20"
        and "question_loop_v10" in SUPPORTED_LOOP_VERSIONS
    )


def test_partial_table_unknown_rows_are_not_uncited_factual_prose(env11, monkeypatch):
    repo, tools = env11
    base = source_model(tools)

    async def model(system, data, **kw):
        value = await base(system, data, **kw)
        if kw["stage"] == "answer_check":
            value["claims"][1].update(
                supported=False, supported_by=[], unsupported_parts=["premise missing"]
            )
            value["units"][1]["complete"] = False
        return value

    monkeypatch.setattr(tools, "model", model)
    stop = asyncio.run(Controller(tools).run())
    answer = repo.rows(ResearchAnswerRevision)[0]
    assert answer.status == "insufficient" and answer.conclusion
    assert "尚未形成完整回答" in answer.conclusion
    assert "答案包含未绑定证据的事实段落" not in answer.gaps
    assert answer.assessment["rejected_claim_details"][0]["code"] == "semantic_rejected"
    assert len(answer.assessment["proposed_claims"]) == 2
    report = build_report(tools, freeze(tools, stop))
    assert "1 / 2" in report["summary"] and report["quality_status"] == "needs_review"


def test_v11_get_answer_audit_and_cors(env11, client, monkeypatch):
    from backend.config import settings
    from backend.routers import exploration

    repo, tools = env11
    monkeypatch.setattr(tools, "model", source_model(tools))
    asyncio.run(Controller(tools).run())
    monkeypatch.setattr(exploration, "get_engine", lambda: repo.engine)
    origin = {"Origin": settings.frontend_origin}
    for suffix in ["questions", "answers", "tasks"]:
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
        if suffix == "answers":
            assessment = response.json()["items"][0]["assessment"]
            assert (
                assessment["method"] == "source_selection_v4"
                and assessment["candidate_answer"]
            )
            assert not assessment["human_reviewed"]


def test_aliases_restore_two_sided_relation_identity():
    known, context = sample_context()
    raw = candidate()
    raw["relations"] = [
        {
            "subject_asset_id": "D1",
            "object_asset_id": "D2",
            "subject_evidence_ids": ["E1"],
            "object_evidence_ids": ["E2"],
            "predicate": "method_difference",
            "scope": "bounded materials",
            "description": "保存正常向量与重建图像的机制不同。",
        }
    ]
    result = resolve_answer(raw, context)
    assert result["relations"][0]["subject_asset_id"] == "asset_1"
    assert result["relations"][0]["object_evidence_ids"] == ["ev_real_2"]
    claims, _, _ = bind_claims(result)
    payload, _ = checker_payload(claims, result, context)
    assert payload["relations"][0]["object_asset_id"] == "D2"
    assert payload["relations"][0]["object_evidence_ids"] == ["E2"]


def test_unaffordable_navigation_is_resource_stop_with_diagnostics(env11, monkeypatch):
    repo, tools = env11

    async def choose():
        tools.resources.navigation_blockers = [
            {
                "code": "final_answer_reserve",
                "tool": "read_documents",
                "question_id": "main",
            }
        ]
        return None

    controller = Controller(tools)
    monkeypatch.setattr(controller, "choose", choose)
    assert asyncio.run(controller.run()) == "resource_limit"
    assert repo.run().checkpoint["stop_details"][0]["code"] == "final_answer_reserve"


def test_bad_planning_anchor_preserves_candidate_and_safe_guard_code(
    env11, monkeypatch
):
    repo, tools = env11
    planner = Planner()

    async def model(system, data, **kw):
        value = await planner.model(system, data, **kw)
        if kw["stage"] == "planning":
            value["requests"][0]["clause_id"] = "unknown-source"
        return value

    monkeypatch.setattr(tools, "model", model)
    with pytest.raises(ValueError):
        asyncio.run(tools.make_plan(specs()))
    rows = [a for a in repo.rows(ResearchAsset) if a.content_kind == "planning_attempt"]
    assert (
        len(rows) == 1
        and rows[0].inline_payload["candidate"]["requests"][0]["clause_id"]
        == "unknown-source"
    )
    from backend.models.research_core import ResearchEvent

    event = next(e for e in repo.rows(ResearchEvent) if e.type == "planning_rejected")
    assert event.payload == {"stage": "planning", "code": "PLAN_SOURCE_ANCHOR"}
    assert rows[0] not in tools.documents()


def test_failed_planning_is_not_reported_as_executed_zero_hit_search(env11):
    repo, tools = env11
    with repo.transaction() as session:
        for asset in repo.rows(ResearchAsset):
            stored = session.get(ResearchAsset, asset.id)
            if asset.content_kind in ("document", "research_plan"):
                stored.status = "quarantined"
    report = build_report(tools, freeze(tools, "planning_failed"))
    assert report["status"] == "partial"
    assert (
        report["summary"]
        == "研究规划未完成，尚未执行文献检索与分析；当前没有研究结论。"
    )
    assert not report["search_summary"]["attempts"]
