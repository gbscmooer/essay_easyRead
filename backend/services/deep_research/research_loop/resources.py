"""Persisted resource reservations, shared with the unified LLM Core attempt hooks."""
from __future__ import annotations

import math
from uuid import uuid4

from sqlmodel import select

from ....models.research_core import ResearchBudgetEvent
from .repository import Repository

DEFAULT_LIMITS = {
    "lite": (60000, 15, 24, 16, 12),
    "quick": (120000, 25, 40, 28, 20),
    "standard": (240000, 50, 80, 48, 35),
    "deep": (480000, 90, 120, 80, 60),
}


class ResourceExhausted(RuntimeError):
    pass


def limits_for(depth: str, overrides: dict | None = None, *, revision: str | None = None) -> dict:
    tokens, calls, requests, actions, minutes = DEFAULT_LIMITS.get(depth, DEFAULT_LIMITS["standard"])
    # New revisions freeze their own quality-first envelope; historical runs
    # continue to use the persisted protocol, never the newest defaults.
    # Resumed historical runs continue to use their persisted protocol budget.
    if revision == "question_loop_v19":
        tokens, calls, requests, actions, minutes = {
            "lite": (120000, 25, 40, 28, 20),
            "quick": (240000, 45, 60, 44, 30),
        }.get(depth, (tokens, calls, requests, actions, minutes))
    elif revision == "question_loop_v20":
        tokens, calls, requests, actions, minutes = {
            "lite": (240000, 45, 60, 44, 30),
            "quick": (360000, 65, 90, 64, 35),
        }.get(depth, (tokens, calls, requests, actions, minutes))
    result = {"llm_tokens": tokens, "llm_calls": calls, "http_requests": requests,
              "actions": actions, "wall_clock_minutes": minutes,
              "writing_tokens": 0, "writing_calls": 0}
    for key, value in (overrides or {}).items():
        if key not in result or isinstance(value, bool) or not isinstance(value, int) or value < (0 if key.startswith("writing_") else 1):
            raise ValueError(f"无效资源上限：{key}")
        result[key] = value
    if result["writing_tokens"] >= result["llm_tokens"] or result["writing_calls"] >= result["llm_calls"]:
        raise ValueError("写作预留必须小于总资源上限")
    return result


class Resources:
    def __init__(self, repository: Repository, limits: dict):
        self.repository, self.limits = repository, limits
        self.account = "research"
        self.task_id = None
        self.current_question_id = None
        self.attempt_id = None
        self._pending = []
        self.source_exhausted = False
        self.answer_hold_tokens = 0
        self.answer_reserve_provider = None

    def reserve(self, amounts: dict[str, int]) -> str:
        reservation = uuid4().hex
        with self.repository.transaction() as session:
            rows = session.exec(select(ResearchBudgetEvent).where(ResearchBudgetEvent.run_id == self.repository.run_id)).all()
            committed = {}
            for row in rows:
                committed[row.resource] = committed.get(row.resource, 0) + row.delta_reserved - row.delta_refunded
            # Reading and research share the non-writing envelope. Each keeps a subledger.
            for resource, amount in amounts.items():
                cap = self.limits.get(resource)
                if cap is not None and committed.get(resource, 0) + amount > cap:
                    raise ResourceExhausted(f"{resource} 已达到资源上限")
                if resource in ("llm_tokens", "llm_calls") and self.account != "writing":
                    reserve = self.limits["writing_tokens" if resource == "llm_tokens" else "writing_calls"]
                    nonwriting = sum(committed.get(f"{name}:{resource}", 0) for name in ("research", "reading", "answer"))
                    if nonwriting + amount > cap - reserve:
                        raise ResourceExhausted("已到研究资源上限，保留写作额度")
                if resource in ("llm_tokens", "llm_calls") and self.account == "writing":
                    cap = self.limits["writing_tokens" if resource == "llm_tokens" else "writing_calls"]
                    if committed.get(f"writing:{resource}", 0) + amount > cap:
                        raise ResourceExhausted("写作额度已用尽")
            for resource, amount in amounts.items():
                for label in (resource, f"{self.account}:{resource}"):
                    session.add(ResearchBudgetEvent(run_id=self.repository.run_id, resource=label,
                        task_id=self.task_id, attempt_id=reservation, delta_reserved=amount,
                        unit="token" if resource == "llm_tokens" else "count"))
        return reservation

    def settle(self, reservation: str, used: dict[str, int] | None, *, known=True):
        with self.repository.transaction(accounting=True) as session:
            rows = session.exec(select(ResearchBudgetEvent).where(
                ResearchBudgetEvent.run_id == self.repository.run_id, ResearchBudgetEvent.attempt_id == reservation)).all()
            # Idempotent settlement, including recovered unknown attempts.
            if any(row.delta_used or row.delta_refunded for row in rows):
                return
            for row in rows:
                resource = row.resource.split(":")[-1]
                amount = row.delta_reserved if used is None else max(0, int(used.get(resource, row.delta_reserved)))
                session.add(ResearchBudgetEvent(run_id=row.run_id, resource=row.resource,
                    task_id=row.task_id, attempt_id=reservation, delta_used=amount,
                    delta_reserved=max(0, amount - row.delta_reserved),
                    delta_refunded=max(0, row.delta_reserved - amount), usage_known=known,
                    note="settled" if known else "unknown_upper_bound", unit=row.unit))

    def charge(self, resource: str, amount=1):
        reservation = self.reserve({resource: amount})
        self.settle(reservation, {resource: amount})

    def remaining(self, resource: str) -> int:
        with self.repository.transaction() as session:
            rows = session.exec(select(ResearchBudgetEvent).where(
                ResearchBudgetEvent.run_id == self.repository.run_id)).all()
            used = sum(row.delta_reserved - row.delta_refunded for row in rows if row.resource == resource)
        return max(0, self.limits[resource] - used)

    def before_model_attempt(self, *, estimated_output_tokens, estimated_input_tokens):
        amounts = {"llm_calls": 1, "llm_tokens": math.ceil(estimated_input_tokens + estimated_output_tokens)}
        # Preserve one synthesis + one independent support check. The report itself is deterministic.
        if self.account in ("research", "reading"):
            v3 = ((self.repository.run().options or {}).get("workflow") or {}).get("loop_revision") in ("question_loop_v3", "question_loop_v4", "question_loop_v5", "question_loop_v6", "question_loop_v7", "question_loop_v8", "question_loop_v9", "question_loop_v10", "question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20")
            if self.answer_reserve_provider is not None:
                needed = self.answer_reserve_provider()
                tokens = max(needed["llm_tokens"], self.answer_hold_tokens)
                calls = needed["llm_calls"]
            else:
                tokens, calls = (12000 if v3 else 8000), (3 if v3 else 2)
            if self.remaining("llm_calls") - amounts["llm_calls"] < calls or self.remaining("llm_tokens") - amounts["llm_tokens"] < tokens:
                raise ResourceExhausted("保留当前材料的答案综合与支持检查额度")
        self._pending.append(self.reserve(amounts))

    def after_model_attempt(self, *, usage, reserved_output_tokens, reserved_input_tokens, usage_known):
        reservation = self._pending.pop()
        used = {"llm_calls": 1, "llm_tokens": int(usage["input_tokens"] + usage["output_tokens"])} if usage_known else None
        self.settle(reservation, used, known=usage_known)

    def cancel_model_attempt(self, **kwargs):
        self.settle(self._pending.pop(), {"llm_calls": 0, "llm_tokens": 0})

    def consume_source_request(self):
        try:
            self.charge("http_requests")
        except ResourceExhausted:
            self.source_exhausted = True
            raise
