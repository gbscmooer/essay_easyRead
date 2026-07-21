"""LLM-backed goal planner for the research-agent LangGraph harness.

The planner is deliberately small: one bounded completion converts a free-form
goal into a stable search plan.  The workflow remains runnable without an API
key by falling back to a deterministic plan.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Optional

from pydantic import BaseModel, Field


class ResearchGoalPlan(BaseModel):
    search_query: str = Field(..., min_length=1, max_length=500)
    keywords: list[str] = Field(default_factory=list, max_length=8)
    deliverables: list[str] = Field(default_factory=list, max_length=8)
    target_paper_count: int = Field(default=5, ge=1, le=8)


def fallback_goal_plan(goal: str, workflow: str) -> dict[str, Any]:
    """Return a zero-cost plan that preserves the original goal."""

    deliverables = (
        ["reading_plan", "comparison_matrix", "survey_draft"]
        if workflow == "survey_path"
        else ["selected_papers", "structured_summary", "reading_suggestions"]
    )
    # literature_brief historically selected up to 8 papers; keep that when the
    # optional LLM planner is disabled so default offline runs do not regress.
    target_paper_count = 5 if workflow == "survey_path" else 8
    return {
        "search_query": goal.strip(),
        "keywords": [],
        "deliverables": deliverables,
        "target_paper_count": target_paper_count,
        "planner": "deterministic",
        "model": None,
    }


async def _request_completion(
    *,
    api_key: str,
    base_url: str,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    timeout_seconds: float,
) -> str:
    """Issue one bounded completion and always close the SDK client."""

    from .ai_analyzer import (
        _complete_once,
        _create_client,
        _create_openai_client,
        _is_openai_format,
    )

    use_openai = _is_openai_format(base_url)
    client = (
        _create_openai_client(api_key, base_url)
        if use_openai
        else _create_client(api_key, base_url)
    )
    try:
        return await asyncio.wait_for(
            _complete_once(
                client,
                use_openai,
                model,
                system,
                user,
                max_tokens=max_tokens,
                temperature=0.0,
            ),
            timeout=timeout_seconds,
        )
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result


def _parse_plan(raw: str) -> ResearchGoalPlan:
    from .ai_analyzer import _extract_json_value, _repair_json

    # OpenAI-compatible models may still wrap JSON in a markdown fence despite
    # the system instruction.  Start from the first object rather than treating
    # the fence/preamble as a scalar value.
    object_start = raw.find("{")
    candidate = raw[object_start:] if object_start >= 0 else raw.strip()
    payload = _extract_json_value(candidate) or candidate
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        repaired = _repair_json(payload)
        if repaired is None:
            raise ValueError("goal planner 未返回有效 JSON") from None
        data = json.loads(repaired)
    if not isinstance(data, dict):
        raise ValueError("goal planner 返回值不是 JSON 对象")
    return ResearchGoalPlan.model_validate(data)


async def plan_research_goal(
    goal: str,
    workflow: str,
    *,
    enabled: Optional[bool] = None,
    config: Optional[dict[str, Any]] = None,
    max_tokens: Optional[int] = None,
    timeout_seconds: Optional[float] = None,
) -> dict[str, Any]:
    """Plan a research goal with one LLM call or deterministic fallback.

    ``config`` is injectable so live smoke tests can use an ephemeral key
    without persisting it to ``.env`` or ``ai_config.json``.
    """

    from ..config import settings
    from .ai_config import get_analysis_config

    goal = str(goal or "").strip()
    if not goal:
        raise ValueError("goal 不能为空")

    planner_enabled = (
        settings.research_agent_planner_enabled if enabled is None else bool(enabled)
    )
    if not planner_enabled:
        return fallback_goal_plan(goal, workflow)

    cfg = dict(config or get_analysis_config())
    api_key = str(cfg.get("api_key") or "").strip()
    base_url = str(cfg.get("base_url") or "").strip()
    model = str(cfg.get("model") or "").strip()
    if not api_key or not base_url or not model:
        return fallback_goal_plan(goal, workflow)

    token_limit = max(64, min(int(max_tokens or settings.research_agent_planner_max_tokens), 800))
    timeout = max(
        5.0,
        min(float(timeout_seconds or settings.research_agent_planner_timeout_seconds), 120.0),
    )
    system = (
        "You plan academic-literature research workflows. Return only one JSON object "
        "with keys search_query, keywords, deliverables, target_paper_count. "
        "search_query must be concise and preserve important technical terms. "
        "keywords and deliverables must be short string arrays. target_paper_count must be 1-8."
    )
    user = f"workflow={workflow}\nresearch goal:\n{goal}"
    raw = await _request_completion(
        api_key=api_key,
        base_url=base_url,
        model=model,
        system=system,
        user=user,
        max_tokens=token_limit,
        timeout_seconds=timeout,
    )
    plan = _parse_plan(raw).model_dump(mode="json")
    plan["planner"] = "llm"
    plan["model"] = model
    return plan
