import asyncio
import json

import pytest
from sqlmodel import Session

from backend.models.exploration import ExplorationMessage, ExplorationRun
from backend.services import exploration_chat as chat
from backend.services.database import _migrate_schema, reconcile_interrupted_runs


@pytest.fixture
def chat_env(engine, monkeypatch):
    from backend.services.deep_research import literature_retrieval as retrieval
    async def unavailable(*args, **kwargs):
        raise RuntimeError("offline test")
    monkeypatch.setattr(retrieval, "document_vectors", unavailable)
    monkeypatch.setattr(retrieval, "get_reranker_config", lambda: {"enabled": False})
    async def audit(*args, **kwargs):
        return {"status": "completed", "support_rate": 1.0, "claim_count": 1}
    monkeypatch.setattr(chat, "audit_answer", audit)
    monkeypatch.setattr(chat, "get_engine", lambda: engine)
    monkeypatch.setattr(chat, "get_analysis_config", lambda: {"api_key": "test", "model": "test"})
    from backend.routers import exploration
    monkeypatch.setattr(exploration, "get_engine", lambda: engine)
    with Session(engine) as session:
        run = ExplorationRun(question="Original research question", status="done", result={
            "papers": [{"key": "p1", "title": "First paper", "abstract": "A robust method", "doi": "10.1/a"}],
            "decompose": {"en_queries": ["original query"]},
            "synthesis": {"markdown_report": "Initial findings"}, "metrics": {"total": 1},
        })
        session.add(run)
        session.commit()
        session.refresh(run)
        return run.id


def test_multiturn_persists_context_and_filters_invalid_evidence(chat_env, monkeypatch, engine):
    payloads = []

    async def llm(cfg, system, user, **kwargs):
        payloads.append(json.loads(user))
        return {"answer": "Supported answer [P1]", "evidence": ["P1", "P999"]}

    monkeypatch.setattr(chat, "_llm_json", llm)
    one = chat.create_message(chat_env, "What is the method?", False, ["p1"])
    asyncio.run(chat.answer_message(one.id))
    two = chat.create_message(chat_env, "What are its limitations?", False, [])
    asyncio.run(chat.answer_message(two.id))
    rows = chat.conversation(chat_env)
    assert len(rows) == 2 and all(r.status == "done" for r in rows)
    assert rows[1].evidence_keys == ["p1"]
    assert payloads[1]["原始问题"] == "Original research question"
    assert payloads[1]["历史对话"][0]["answer"] == "Supported answer [P1]"
    assert payloads[1]["历史对话"][0]["citations"] == {"P1": "First paper"}
    assert rows[1].context["history_ids"] == [one.id]
    assert rows[1].context["evidence_audit"]["status"] == "completed"
    with Session(engine) as session:
        assert session.get(ExplorationRun, chat_env).result["synthesis"]["markdown_report"] == "Initial findings"


def test_new_materials_survive_failure_and_are_used_next_turn(chat_env, monkeypatch):
    async def decompose(*args, **kwargs):
        return {"en_queries": ["new query"]}

    async def recall(*args, **kwargs):
        return [{"key": "p2", "title": "New paper", "abstract": "New evidence"}], "stub"

    async def broken(*args, **kwargs):
        raise RuntimeError("credential must not be exposed")

    monkeypatch.setattr(chat, "decompose_question", decompose)
    monkeypatch.setattr(chat, "recall_papers", recall)
    monkeypatch.setattr(chat, "_llm_json", broken)
    row = chat.create_message(chat_env, "Find recent evidence", True, [])
    asyncio.run(chat.answer_message(row.id))
    failed = chat.conversation(chat_env)[0]
    assert failed.status == "failed" and failed.papers[0]["key"] == "p2"
    assert "credential" not in failed.error_message

    async def llm(cfg, system, user, **kwargs):
        payload = json.loads(user)
        assert any(p["title"] == "New paper" for p in payload["本轮资料"])
        return {"answer": "New answer", "evidence": []}

    monkeypatch.setattr(chat, "_llm_json", llm)
    next_row = chat.create_message(chat_env, "New paper details", False, ["p2"])
    asyncio.run(chat.answer_message(next_row.id))
    assert chat.conversation(chat_env)[1].status == "done"


def test_busy_no_key_restart_and_missing(chat_env, monkeypatch, engine):
    row = chat.create_message(chat_env, "Question", False, [])
    with pytest.raises(chat.ExplorationChatError) as exc:
        chat.create_message(chat_env, "Duplicate", False, [])
    assert exc.value.code == "CHAT_RUNNING"
    reconcile_interrupted_runs(engine)
    assert chat.conversation(chat_env)[0].status == "failed"
    monkeypatch.setattr(chat, "get_analysis_config", lambda: {})
    with pytest.raises(chat.ExplorationChatError) as exc:
        chat.create_message(chat_env, "Question", False, [])
    assert exc.value.code == "LLM_NOT_CONFIGURED"
    with pytest.raises(chat.ExplorationChatError) as exc:
        chat.conversation(99999)
    assert exc.value.status == 404


def test_api_persistence_validation_delete_and_cors(chat_env, monkeypatch, client, engine):
    monkeypatch.setattr(chat, "start_message", lambda _: None)
    url = f"/api/exploration/runs/{chat_env}/messages"
    assert client.post(url, json={"question": "   "}).status_code == 422
    response = client.post(url, json={"question": "Question", "search_more": True})
    assert response.status_code == 200
    assert client.get(url).json()[0]["question"] == "Question"
    assert client.post(url, json={"question": "Again"}).status_code == 409
    assert client.delete(f"/api/exploration/runs/{chat_env}").status_code == 409
    with Session(engine) as session:
        row = session.get(ExplorationMessage, response.json()["id"])
        row.status = "done"
        session.add(row)
        session.commit()
    assert client.delete(f"/api/exploration/runs/{chat_env}").status_code == 200
    assert client.get(url).status_code == 404
    from backend.config import settings
    cors = client.options(url, headers={"Origin": settings.frontend_origin,
        "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
    assert cors.status_code == 200
    assert cors.headers["access-control-allow-origin"] == settings.frontend_origin
    untrusted = client.options(url, headers={"Origin": "https://untrusted.invalid", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in untrusted.headers


def test_explicit_selection_overrides_citations():
    papers = [{"key": "a", "title": "Popular paper", "citation_count": 9000},
              {"key": "b", "title": "Specific paper", "citation_count": 0}]
    assert chat.select_materials(papers, "Popular paper", ["b"], limit=1)[0]["key"] == "b"


def test_active_message_index_migrates_existing_table(chat_env, engine):
    from sqlalchemy.exc import IntegrityError
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP INDEX uq_exploration_active_message")
    _migrate_schema(engine)
    _migrate_schema(engine)
    chat.create_message(chat_env, "First", False, [])
    with Session(engine) as session:
        session.add(ExplorationMessage(run_id=chat_env, question="Concurrent"))
        with pytest.raises(IntegrityError):
            session.commit()
