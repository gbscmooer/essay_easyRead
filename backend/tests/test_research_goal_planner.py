import pytest

from backend.services import research_goal_planner as planner


@pytest.mark.asyncio
async def test_goal_planner_is_deterministic_when_disabled(monkeypatch):
    async def unexpected_request(**_kwargs):
        raise AssertionError("disabled planner must not call the network")

    monkeypatch.setattr(planner, "_request_completion", unexpected_request)

    result = await planner.plan_research_goal(
        "retrieval augmented generation for literature QA",
        "literature_brief",
        enabled=False,
        config={"api_key": "unused", "base_url": "https://example.invalid", "model": "unused"},
    )

    assert result["planner"] == "deterministic"
    assert result["search_query"] == "retrieval augmented generation for literature QA"
    assert result["target_paper_count"] == 8


@pytest.mark.asyncio
async def test_goal_planner_parses_bounded_llm_json(monkeypatch):
    captured = {}

    async def fake_request(**kwargs):
        captured.update(kwargs)
        return """```json
        {
          "search_query": "RAG academic literature question answering",
          "keywords": ["RAG", "literature QA"],
          "deliverables": ["paper brief", "reading plan"],
          "target_paper_count": 5
        }
        ```"""

    monkeypatch.setattr(planner, "_request_completion", fake_request)

    result = await planner.plan_research_goal(
        "Find papers about RAG for academic literature QA",
        "literature_brief",
        enabled=True,
        config={
            "api_key": "test-key",
            "base_url": "https://api.example/v1",
            "model": "planner-model",
        },
        max_tokens=120,
        timeout_seconds=9,
    )

    assert result == {
        "search_query": "RAG academic literature question answering",
        "keywords": ["RAG", "literature QA"],
        "deliverables": ["paper brief", "reading plan"],
        "target_paper_count": 5,
        "planner": "llm",
        "model": "planner-model",
    }
    assert captured["max_tokens"] == 120
    assert captured["timeout_seconds"] == 9
    assert "test-key" not in captured["system"]
    assert "test-key" not in captured["user"]


@pytest.mark.asyncio
async def test_goal_planner_rejects_non_json(monkeypatch):
    async def fake_request(**_kwargs):
        return "not json"

    monkeypatch.setattr(planner, "_request_completion", fake_request)

    with pytest.raises(ValueError, match="有效 JSON"):
        await planner.plan_research_goal(
            "test goal",
            "literature_brief",
            enabled=True,
            config={
                "api_key": "test-key",
                "base_url": "https://api.example/v1",
                "model": "planner-model",
            },
        )
