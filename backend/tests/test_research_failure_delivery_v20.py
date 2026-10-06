"""A model outage preserves diagnostic work without inventing delivered research."""

import asyncio

import pytest

from backend.models.research_core import (
    ResearchAsset,
    ResearchAnswerRevision,
    ResearchTask,
)
from backend.services.llm import LLMError
from backend.services.deep_research.research_loop.contracts import Action, digest
from backend.services.deep_research.research_loop.answering_v19 import check_output
from backend.services.deep_research.research_loop.answer_budget_v19 import envelope
from backend.services.deep_research.research_loop.controller import Controller
from backend.services.deep_research.research_loop.dossier import freeze
from backend.services.deep_research.research_loop.writer import build_report
from backend.tests.test_research_sources_v11 import env11 as source_fixture
from backend.tests.test_research_narrative_v19 import model19

failure_env = source_fixture


@pytest.mark.parametrize("failure_env", ["question_loop_v20"], indirect=True)
def test_checker_outage_keeps_unverified_draft_and_truthful_stop_and_report(
    failure_env, monkeypatch
):
    repo, tools = failure_env
    tools.config = {"api_format": "openai_chat", "model": "MiniMax-M2.7-highspeed"}
    base = model19(tools)

    async def model(system, data, **kw):
        if kw["stage"] == "answer_claim_check":
            assert kw["max_tokens"] == 6000
            raise LLMError("LLM_EMPTY_CONTENT", "diagnostic outage")
        return await base(system, data, **kw)

    monkeypatch.setattr(tools, "model", model)

    async def exercise():
        read = Action(
            tool="read_documents",
            question_id="main",
            asset_ids=[tools.documents()[0].asset_id],
        )
        tid, aid = repo.claim_task(read)
        repo.complete_task(tid, aid, await tools.read_batch(read))
        calls = iter([Action(tool="assess_answer", question_id="main"), None])

        async def choose():
            return next(calls)

        controller = Controller(tools)
        monkeypatch.setattr(controller, "choose", choose)
        return await controller.run()

    stop = asyncio.run(exercise())
    assert stop == "model_error"
    assert repo.run().checkpoint["stop_details"][0]["code"] == "LLM_EMPTY_CONTENT"
    drafts = [a for a in repo.rows(ResearchAsset) if a.content_kind == "answer_attempt"]
    assert len(drafts) == 1 and drafts[0].inline_payload["status"] == "unverified"
    assert drafts[0].inline_payload["candidate"]["units"]
    assert not repo.rows(ResearchAnswerRevision)
    assert any(
        t.state == "failed" and t.kind == "assess_answer"
        for t in repo.rows(ResearchTask)
    )
    # An unchecked navigation diagnosis must not become a formal conclusion or
    # introduce a requirement absent from the user's request.
    payload = {
        "question_id": "main",
        "protocol_hash": repo.protocol()["hash"],
        "stage": "after_coverage_search",
        "selection_status": "partial",
        "choices": [],
        "gaps": ["已覆盖所有路线", "还需要RGB+3D迁移阈值"],
        "candidate_count": 1,
    }
    with repo.transaction() as session:
        session.add(
            ResearchAsset(
                run_id=repo.run_id,
                content_kind="reading_portfolio",
                inline_payload=payload,
                content_hash=digest(payload),
            )
        )
    dossier = freeze(tools, stop)
    gaps = dossier.manifest["answers"][0]["gaps"]
    assert any("模型调用/输出错误" in g for g in gaps)
    assert any("解释队列管理" in g for g in gaps)
    assert not any("RGB+3D" in g or "已覆盖" in g for g in gaps)
    assert dossier.manifest["material_selection"]["main"]["gaps"] == payload["gaps"]
    report = build_report(tools, dossier)
    assert not dossier.manifest["answers"][0]["conclusion"]
    assert not report["references"]


@pytest.mark.parametrize(
    "failure_env", ["question_loop_v20", "question_loop_v19"], indirect=True
)
def test_minimax_checker_capacity_and_reservation_are_revisioned(
    failure_env, monkeypatch
):
    repo, tools = failure_env
    tools.config = {"api_format": "openai_chat", "model": "MiniMax-M2.7-highspeed"}
    revision = repo.protocol()["execution_revision"]
    assert check_output(1, tools=tools) == (
        9000 if revision == "question_loop_v20" else 2860
    )
    import backend.services.deep_research.research_loop.answer_budget_v19 as budget

    outputs = []

    def cost(system, data, schema, output, **kwargs):
        outputs.append(output)
        return 20000 + output

    monkeypatch.setattr(budget, "model_cost", cost)
    envelope(tools, "main")
    # Two units => conservative 18 claims, same output calculation as dispatch.
    if revision == 'question_loop_v20':
        assert outputs == [4000, 6000, 6000, 6000, 9000]
    else:
        assert outputs[-1] == check_output(18, tools=tools)
    assert check_output(1) == 2860
