"""受控的阶段级 ReAct 循环。

模型只负责产生结构化 action/finish 决策；工具权限、预算、验收和状态推进均由代码控制。
轨迹只保存简短 rationale_summary、工具名、脱敏参数/观察和验收结果，不保存自由思维链。
"""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping


ToolHandler = Callable[[dict, dict], dict | Awaitable[dict]]
ModelStep = Callable[[dict], dict | Awaitable[dict]]
Validator = Callable[[dict], "StageValidation" | Awaitable["StageValidation"]]
ProgressMarker = Callable[[dict], object]
EventHandler = Callable[[dict], None | Awaitable[None]]


@dataclass(frozen=True)
class StageValidation:
    passed: bool
    feedback: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class StageLoopResult:
    state: dict
    trace: list[dict]
    status: str
    stop_reason: str
    iterations: int

    def audit(self) -> dict:
        return {
            "status": self.status,
            "stop_reason": self.stop_reason,
            "iterations": self.iterations,
            "trace": self.trace,
        }


def _compact(value: Any, *, max_chars: int = 4000) -> Any:
    """把事件/模型上下文限制为小型 JSON，避免把原文或提示词写入运行轨迹。"""
    if isinstance(value, dict):
        compact = {str(k)[:80]: _compact(v, max_chars=max_chars) for k, v in list(value.items())[:40]}
    elif isinstance(value, (list, tuple)):
        compact = [_compact(v, max_chars=max_chars) for v in list(value)[:40]]
    elif isinstance(value, str):
        compact = value[:max_chars]
    elif value is None or isinstance(value, (int, float, bool)):
        compact = value
    else:
        compact = str(value)[:max_chars]
    try:
        encoded = json.dumps(compact, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(compact)[:max_chars]
    if len(encoded) <= max_chars:
        return compact
    return encoded[:max_chars] + "…"


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


async def run_stage_agent(
    *,
    stage: str,
    state: dict,
    model_step: ModelStep,
    tools: Mapping[str, ToolHandler],
    validator: Validator,
    max_iterations: int,
    no_progress_limit: int = 2,
    turn_timeout: float = 120,
    progress_marker: ProgressMarker | None = None,
    on_event: EventHandler | None = None,
) -> StageLoopResult:
    """运行一个有界阶段循环；只有 ``finish + validator.passed`` 才返回 completed。"""
    if max_iterations < 1:
        raise ValueError("max_iterations must be positive")
    if no_progress_limit < 1:
        raise ValueError("no_progress_limit must be positive")

    trace: list[dict] = []
    last_observation: dict = {"status": "stage_entered"}
    stagnant = 0

    async def emit(payload: dict) -> None:
        if on_event:
            await _resolve(on_event(_compact({"stage": stage, **payload})))

    def marker() -> object:
        if progress_marker:
            return progress_marker(state)
        return json.dumps(_compact(state, max_chars=8000), ensure_ascii=False, sort_keys=True, default=str)

    for iteration in range(1, max_iterations + 1):
        await emit({"iteration": iteration, "phase": "decide", "max_iterations": max_iterations})
        view = {
            "stage": stage,
            "iteration": iteration,
            "max_iterations": max_iterations,
            "available_tools": sorted(tools),
            "state": _compact(state, max_chars=8000),
            "last_observation": _compact(last_observation),
            "recent_trace": _compact(trace[-3:]),
        }
        try:
            decision = await asyncio.wait_for(_resolve(model_step(view)), timeout=turn_timeout)
        except Exception as exc:
            turn = {
                "iteration": iteration,
                "action": "model_error",
                "observation": {"ok": False, "code": type(exc).__name__, "detail": str(exc)[:500]},
            }
            trace.append(turn)
            await emit({"iteration": iteration, "phase": "observe", "action": "model_error",
                        "observation": turn["observation"]})
            return StageLoopResult(state, trace, "partial", f"model_error:{type(exc).__name__}", iteration)
        if not isinstance(decision, dict):
            raise ValueError("stage agent decision must be a JSON object")

        action = str(decision.get("action") or ("finish" if decision.get("finish") else "")).strip()
        arguments = decision.get("arguments") if isinstance(decision.get("arguments"), dict) else {}
        wants_finish = bool(decision.get("finish")) or action == "finish"
        turn = {
            "iteration": iteration,
            "action": action or "invalid",
            "rationale_summary": str(decision.get("rationale_summary") or "")[:500],
            "remaining_gaps": _compact(decision.get("remaining_gaps") or []),
        }

        before = marker()
        if action and action != "finish":
            handler = tools.get(action)
            if handler is None:
                last_observation = {
                    "ok": False,
                    "code": "TOOL_NOT_ALLOWED",
                    "detail": f"工具 {action!r} 不在阶段白名单中",
                    "allowed": sorted(tools),
                }
            else:
                try:
                    await emit({"iteration": iteration, "phase": "tool", "tool": action})
                    result = await _resolve(handler(dict(arguments), state))
                    last_observation = result if isinstance(result, dict) else {"result": result}
                    last_observation = {"ok": True, **last_observation}
                except Exception as exc:
                    last_observation = {
                        "ok": False,
                        "code": type(exc).__name__,
                        "detail": str(exc)[:500],
                    }
            turn["arguments"] = _compact(arguments)
            turn["observation"] = _compact(last_observation)

        validation: StageValidation | None = None
        if wants_finish:
            validation = await _resolve(validator(state))
            if not isinstance(validation, StageValidation):
                raise ValueError("stage validator must return StageValidation")
            turn["validation"] = {
                "passed": validation.passed,
                "feedback": validation.feedback[:1000],
                "details": _compact(validation.details),
            }
            if validation.passed:
                trace.append(turn)
                await emit({"iteration": iteration, "phase": "complete", "validation": turn["validation"]})
                return StageLoopResult(state, trace, "completed", "validator_passed", iteration)
            last_observation = {
                "ok": False,
                "code": "STAGE_VALIDATION_FAILED",
                "detail": validation.feedback,
                "details": validation.details,
            }

        after = marker()
        if before == after:
            stagnant += 1
        else:
            stagnant = 0
        trace.append(turn)
        await emit({
            "iteration": iteration,
            "phase": "observe",
            "action": action or "invalid",
            "observation": _compact(last_observation),
            "no_progress": stagnant,
        })
        if stagnant >= no_progress_limit:
            return StageLoopResult(state, trace, "partial", "no_progress", iteration)

    return StageLoopResult(state, trace, "partial", "iteration_budget_exhausted", max_iterations)
