"""受控的阶段级 ReAct 循环。

模型只负责产生结构化 action/finish 决策；工具权限、预算、验收和状态推进均由代码控制。
轨迹只保存简短 rationale_summary、工具名、脱敏参数/观察和验收结果，不保存自由思维链。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping


ToolHandler = Callable[[dict, dict], dict | Awaitable[dict]]
ModelStep = Callable[[dict], dict | Awaitable[dict]]
Validator = Callable[[dict], "StageValidation" | Awaitable["StageValidation"]]
ProgressMarker = Callable[[dict], object]
EventHandler = Callable[[dict], None | Awaitable[None]]
PauseCheck = Callable[[], bool]


class StagePausedError(RuntimeError):
    """暂停请求在安全点（迭代/轮次边界）被观察到，阶段循环立即让出。

    调用方（explorer 编排层）捕获后把运行置为 paused 并持久化断点；
    该异常不得被阶段内部的通用 ``except Exception`` 吞掉。
    """


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
    duration_ms: int = 0

    def audit(self) -> dict:
        tool_calls = sum(1 for row in self.trace if row.get("action") not in {None, "", "finish", "model_error", "invalid"})
        failures = sum(1 for row in self.trace if (row.get("observation") or {}).get("ok") is False)
        return {
            "status": self.status,
            "stop_reason": self.stop_reason,
            "iterations": self.iterations,
            "duration_ms": self.duration_ms,
            "model_calls": self.iterations,
            "tool_calls": tool_calls,
            "failure_count": failures,
            "trace": self.trace,
        }


def _truncate_strings(value: Any, limit: int) -> Any:
    if isinstance(value, dict):
        return {str(k)[:80]: _truncate_strings(v, limit) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        return [_truncate_strings(v, limit) for v in list(value)[:40]]
    if isinstance(value, str):
        return value[:limit] + ("…" if len(value) > limit else "")
    return value


def _trim_lists(value: Any, keep: int) -> Any:
    """递归保留每个列表的前 keep 个元素，其余记为 {"__dropped__": N}。

    索引/观察里最要紧的是每条记录的标识字段（如 evidence_id）：宁可丢列表尾部、
    字符串截短，也不能把整个对象坍缩成字符串——模型看不到有效编号就会凭空
    生成不存在的 ID（v7.4.1 研究 5 实测 11 条索引压缩后只剩 1 条完整编号）。
    """
    if isinstance(value, dict):
        return {str(k)[:80]: _trim_lists(v, keep) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        items = list(value)
        head = [_trim_lists(v, keep) for v in items[:keep]]
        if len(items) > keep:
            head.append({"__dropped__": len(items) - keep})
        return head
    return value


def _encoded_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return 1 << 60  # unencodable → treat as oversized, force next ladder step


def _compact(value: Any, *, max_chars: int = 4000) -> Any:
    """把事件/模型上下文限制为小型 JSON，避免把原文或提示词写入运行轨迹。

    超限时渐进收缩，**始终保持 dict/list 结构**：先截短字符串（600→200→80），
    仍超限再裁列表长度（24→12→6→3→1，尾部记 __dropped__），只对完全无法
    收纳的对象退化为 ``{"__truncated__": ...}``。事件回调和模型视图按 dict
    解包/序列化，类型漂移会导致 ** 解包崩溃或多重转义不可读。
    """
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
    for limit in (600, 200, 80):
        shrunk = _truncate_strings(compact, limit)
        if _encoded_size(shrunk) <= max_chars:
            return shrunk
    for keep in (24, 12, 6, 3, 1):
        trimmed = _trim_lists(compact, keep)
        for limit in (200, 80):
            candidate = _truncate_strings(trimmed, limit)
            if _encoded_size(candidate) <= max_chars:
                return candidate
    return {"__truncated__": str(compact)[:max_chars]}


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
    should_pause: PauseCheck | None = None,
) -> StageLoopResult:
    """运行一个有界阶段循环；只有 ``finish + validator.passed`` 才返回 completed。"""
    if max_iterations < 1:
        raise ValueError("max_iterations must be positive")
    if no_progress_limit < 1:
        raise ValueError("no_progress_limit must be positive")

    trace: list[dict] = []
    started_at = time.monotonic()
    last_observation: dict = {"status": "stage_entered"}
    stagnant = 0

    def finish(status: str, stop_reason: str, iterations: int) -> StageLoopResult:
        return StageLoopResult(
            state, trace, status, stop_reason, iterations,
            duration_ms=max(0, round((time.monotonic() - started_at) * 1000)),
        )

    async def emit(payload: dict) -> None:
        if on_event:
            compacted = _compact({"stage": stage, **payload})
            if not isinstance(compacted, dict):
                # _compact 对超限 payload 会退化为截断字符串；事件回调按 dict 解包
                # （**payload），保持结构避免 TypeError
                compacted = {"stage": stage, "truncated_payload": compacted}
            await _resolve(on_event(compacted))

    def marker() -> object:
        if progress_marker:
            return progress_marker(state)
        return json.dumps(_compact(state, max_chars=8000), ensure_ascii=False, sort_keys=True, default=str)

    for iteration in range(1, max_iterations + 1):
        # 暂停是协作式安全点：只在迭代边界让出，不打断进行中的模型调用
        if should_pause is not None and await _resolve(should_pause()):
            raise StagePausedError(f"stage {stage} paused at iteration {iteration}")
        await emit({"iteration": iteration, "phase": "decide", "max_iterations": max_iterations})
        # 字段顺序即请求前缀稳定性顺序：网关前缀缓存按最长相同前缀命中，
        # 逐轮不变的内容（stage/工具表/仅追加的 state）必须排在前面，
        # 每轮变化的内容（最近观察/轨迹/轮次号）必须排在最后——否则轮次号
        # 出现在载荷开头会让后续每一轮的缓存前缀在第一个字节处就断掉。
        view = {
            "stage": stage,
            "available_tools": sorted(tools),
            "state": _compact(state, max_chars=8000),
            # 证据索引一页 12 条 × 80 字符摘要 + coverage/材料清单 ≈ 5k+：
            # 预算给足 6000，配合保结构压缩让 evidence_id 全部可见
            "last_observation": _compact(last_observation, max_chars=6000),
            "recent_trace": _compact(trace[-3:], max_chars=5000),
            "iteration": iteration,
            "max_iterations": max_iterations,
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
            return finish("partial", f"model_error:{type(exc).__name__}", iteration)
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
                return finish("completed", "validator_passed", iteration)
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
            return finish("partial", "no_progress", iteration)

    # 预算耗尽时补一次程序验收：模型只是持续改进而未显式 finish 时，验收权威
    # 在 validator——通过即按完成处理，不把已达标的运行判死
    budget_validation = await _resolve(validator(state))
    if isinstance(budget_validation, StageValidation) and budget_validation.passed:
        return finish("completed", "validator_passed_on_budget", max_iterations)
    return finish("partial", "iteration_budget_exhausted", max_iterations)
