"""Bound batched v20 tools by their operations and frozen remaining time."""

from math import ceil
import re

from ... import llm

from .recall_v20 import (
    relevance_batch_size, SEARCH_SOURCES, SOURCE_LIMIT, SOURCE_TIMEOUT_SECONDS,
    TARGET_SOURCE_LIMIT,
)

MODEL_ATTEMPT_SECONDS = 60  # These tools request thinking_effort="off".
MINIMAX_ATTEMPT_SECONDS = 120
STRUCTURED_ATTEMPTS = 2    # Tools.model has one bounded format recovery.
BACKFILL_TIMEOUT_SECONDS = 15  # Tools.admit's bounded metadata enrichment.
OPERATION_OVERHEAD_SECONDS = 30


def model_timeout(tools, thinking_effort="off", *, stage=None):
    resolved = llm.LLMConfig.from_mapping(getattr(tools, "config", {}) or {})
    if ((tools.repo.protocol() or {}).get("execution_revision") == "question_loop_v20"
            and resolved.api_format == llm.API_FORMAT_OPENAI
            and re.search(r"(?:^|[/])minimax(?:[-/]|$)", resolved.model, re.I)):
        return 180 if stage in ("answer_check", "answer_coverage_check") else MINIMAX_ATTEMPT_SECONDS
    return 90 if thinking_effort != "off" else MODEL_ATTEMPT_SECONDS


def tool_timeout(tools, action):
    """Keep historical limits; allow the declared new batches to finish.

    The worker's outer active-time deadline and Core dispatch deadline remain
    authoritative. No v20 tool can exceed its protocol's remaining time here.
    """
    timeout = 150 if action.tool == "read_documents" else 90
    if action.tool == "read_documents" and (tools.plan() or {}).get("strategy") == "material_first_v3":
        timeout = 120 + 180 * max(1, len(action.asset_ids))
    if (tools.repo.protocol() or {}).get("execution_revision") != "question_loop_v20":
        return timeout
    run = tools.repo.run()
    attempt_seconds = model_timeout(tools)
    if action.tool == "search_papers":
        if run.options.get("search_online", True) and not tools.resources.source_exhausted:
            targets = (tools.plan() or {}).get("targets", [])
            targeted = any(target["question_id"] == action.question_id and target["name"].casefold() == action.query.casefold() for target in targets)
            source_count = len(SEARCH_SOURCES)
            per_source = TARGET_SOURCE_LIMIT if targeted else SOURCE_LIMIT
            batches = ceil(source_count * per_source / relevance_batch_size(tools))
            timeout = (source_count * SOURCE_TIMEOUT_SECONDS
                       + batches * (STRUCTURED_ATTEMPTS * attempt_seconds + BACKFILL_TIMEOUT_SECONDS)
                       + OPERATION_OVERHEAD_SECONDS)
    elif action.tool == "plan_reading":
        from .portfolio_v20 import OUTPUT_LIMITS, navigation_calls, prepare
        stage = action.query or "initial"
        if stage in OUTPUT_LIMITS:
            attempts = navigation_calls(tools, action.question_id, stage)
            if stage == "coverage_refresh":
                from ....models.research_core import ResearchQuestion
                question = next(q for q in tools.repo.rows(ResearchQuestion) if q.question_id == action.question_id)
                if not prepare(tools, question, stage)[3]:
                    attempts = 0  # No source windows means no model navigation call.
            if attempts:
                timeout = max(timeout, attempts * attempt_seconds + OPERATION_OVERHEAD_SECONDS)
    elif attempt_seconds == MINIMAX_ATTEMPT_SECONDS:
        calls = {"read_documents": 1 + max(1, len(action.asset_ids)),
                 "read_document": 2, "assess_answer": 3, "expand_citations": 1}.get(action.tool, 0)
        if calls:
            timeout = max(timeout, calls * STRUCTURED_ATTEMPTS * attempt_seconds
                          + OPERATION_OVERHEAD_SECONDS
                          + (BACKFILL_TIMEOUT_SECONDS if action.tool == "expand_citations" else 0))
    if action.tool == "assess_answer":
        from .answer_budget_v19 import envelope
        # Envelope includes generation, bounded claim batches and coverage.
        # Optional gap planning receives one additional logical call.
        calls = envelope(tools, action.question_id)["llm_calls"] + 1
        timeout = STRUCTURED_ATTEMPTS * ((calls - 1) * attempt_seconds + model_timeout(tools, stage="answer_coverage_check")) + OPERATION_OVERHEAD_SECONDS
    remaining = tools.resources.limits["wall_clock_minutes"] * 60 - run.active_seconds
    return max(0.01, min(timeout, remaining))
