"""Provider-neutral completion, streaming and structured-generation API."""

from __future__ import annotations

import asyncio
import json
import hashlib
import os
import time
import weakref
from collections.abc import AsyncIterator, Mapping
from contextvars import ContextVar
from typing import Any

from .adapters import get_adapter
from .errors import LLMError, LLMResponseError
from .json_utils import parse_json, strip_markdown_fence, extract_balanced_json
from .models import LLMConfig, LLMResponse

MAX_RETRIES = 5
BASE_DELAY = 3.0
MAX_DELAY = 120.0

# ── 全局模型并发（计划 §16.6 P2）：默认 5（智谱单账号并发上限）、上限 8 ───────────────────────────
# 逐篇提取/批次核验/关系复核等流水线各自的 Semaphore 只控制结构并行度，
# 实际在途供应商请求由这里统一封顶，叠加池不再放大并发。
MODEL_CONCURRENCY_DEFAULT = 5
MODEL_CONCURRENCY_MAX = 8
_MODEL_CONCURRENCY: int = max(1, min(
    MODEL_CONCURRENCY_MAX,
    int(os.environ.get("LLM_MODEL_CONCURRENCY") or MODEL_CONCURRENCY_DEFAULT)))
class _ModelLimiter:
    def __init__(self, limit):
        self.limit, self.active = limit, 0
        self.waiters = set()

    async def acquire(self):
        while self.active >= self.limit:
            waiter = asyncio.get_running_loop().create_future()
            self.waiters.add(waiter)
            try:
                await waiter
            finally:
                self.waiters.discard(waiter)
        self.active += 1
        return True

    def _wake(self):
        for waiter in tuple(self.waiters):
            if not waiter.done():
                waiter.set_result(None)

    def release(self):
        self.active -= 1
        self._wake()

    def resize(self, limit):
        self.limit = limit
        self._wake()


_concurrency_semaphores = weakref.WeakKeyDictionary()


def set_model_concurrency(limit: int) -> int:
    """更新全局容量；已发送请求保留，新请求按新容量排队。"""
    global _MODEL_CONCURRENCY
    _MODEL_CONCURRENCY = max(1, min(MODEL_CONCURRENCY_MAX, int(limit)))
    for loop, limiter in list(_concurrency_semaphores.items()):
        if not loop.is_closed():
            loop.call_soon_threadsafe(limiter.resize, _MODEL_CONCURRENCY)
    return _MODEL_CONCURRENCY


def model_concurrency() -> int:
    return _MODEL_CONCURRENCY


def _model_semaphore():
    loop = asyncio.get_running_loop()
    limiter = _concurrency_semaphores.get(loop)
    if limiter is None:
        limiter = _ModelLimiter(_MODEL_CONCURRENCY)
        _concurrency_semaphores[loop] = limiter
    limiter.resize(_MODEL_CONCURRENCY)
    return limiter

# ── Token 台账（工作流可追溯性）──────────────────────────────────────────────
# 上层流水线 start_token_ledger() 后，每次成功调用的 usage 按当前阶段标签
# 累计；阶段标签由流水线在各阶段切换时 set_llm_stage() 设置。未启动台账时
# 零开销，不影响普通调用方。
_ledger_var: ContextVar[dict | None] = ContextVar("llm_token_ledger", default=None)
_stage_var: ContextVar[str] = ContextVar("llm_stage", default="")
_scope_var: ContextVar[dict | None] = ContextVar("llm_scope", default=None)

# 全局用量记录器：由持久化层（services/token_usage.py）安装，逐次调用回调，
# 用于跨业务的 token 消耗统计。回调必须是非阻塞的同步函数；任何异常都被吞掉，
# 统计失败绝不影响业务调用。
_usage_recorder: Any = None

_LEDGER_KEYS = ("calls", "input_tokens", "output_tokens",
                "cached_input_tokens", "cache_write_tokens")


def start_token_ledger() -> dict:
    """创建并激活一个台账（{stage: {calls, input_tokens, ...}}）。"""
    ledger: dict = {}
    _ledger_var.set(ledger)
    return ledger


def set_llm_stage(stage: str) -> None:
    """标记后续调用归属的工作流阶段（空串表示未标注）。"""
    _stage_var.set(str(stage or ""))


def set_llm_scope(category: str, run_id: str | None = None, *,
                  run_instance_id: str | None = None,
                  operation: str | None = None) -> None:
    """标记后续调用归属的业务类目、运行 ID 与实例/操作身份（供用量统计归因）。

    run_instance_id：运行实例的不可复用 UUID（计划 §16.5）——数字 run_id 在 SQLite
    行删除后可复用，实例 ID 保证用量/性能归因不串账；operation：research |
    report_regenerate | chat 等操作类型，避免把追问/重生成混进同一性能样本。
    """
    _scope_var.set({
        "category": str(category or "other"),
        "run_id": str(run_id) if run_id else None,
        "run_instance_id": str(run_instance_id or "")[:64],
        "operation": str(operation or "")[:32],
    })


def set_usage_recorder(recorder: Any) -> None:
    """安装/替换全局用量记录器；传 None 卸载。"""
    global _usage_recorder
    _usage_recorder = recorder


# ── 运行级预算与 deadline（计划 §16.6 P2）────────────────────────────────────
# deadline：单调时钟绝对时刻；重试退避与排队等待都被钳制在该时刻前，超时抛
# 不可重试的 BUDGET_DEADLINE。budget：带 before_model_attempt/after_model_attempt
# 钩子的账本（services/deep_research/run_budget.py），缺方法时零开销。
_deadline_var: ContextVar[float | None] = ContextVar("llm_deadline", default=None)
_budget_var: ContextVar[Any] = ContextVar("llm_budget", default=None)


def set_llm_deadline(deadline_at: float | None) -> None:
    """为后续调用设置单调时钟总 deadline（None 清除）。"""
    _deadline_var.set(deadline_at)


def llm_deadline() -> float | None:
    return _deadline_var.get()


def set_llm_budget(budget: Any) -> None:
    """注入运行预算账本（None 卸载）。只认 before/after_model_attempt 钩子。"""
    _budget_var.set(budget)


def llm_budget() -> Any:
    """当前运行预算账本（未注入返回 None）——供模型调用之外的研究资源
    （来源 HTTP 尝试、补充批次）做同一账本的记账/强制。"""
    return _budget_var.get()


def _check_deadline(stage: str = "request") -> None:
    deadline = _deadline_var.get()
    if deadline is not None and time.monotonic() >= deadline:
        raise LLMError(
            "BUDGET_DEADLINE",
            f"运行预算时间已耗尽（{stage} 前检查）",
            retryable=False,
        )


def _clamped_delay(delay: float) -> float:
    """重试退避钳制在总 deadline 内：剩余时间不足即提前抛 BUDGET_DEADLINE。"""
    deadline = _deadline_var.get()
    if deadline is None:
        return delay
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LLMError("BUDGET_DEADLINE", "运行预算时间已耗尽（退避等待前）",
                       retryable=False)
    return min(delay, remaining)


async def _await_before_deadline(awaitable: Any, stage: str) -> Any:
    """让排队、普通请求和单次流读取都受同一个运行 deadline 约束。"""
    deadline = _deadline_var.get()
    if deadline is None:
        return await awaitable
    task = asyncio.ensure_future(awaitable)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise LLMError("BUDGET_DEADLINE", f"运行预算时间已耗尽（{stage}）",
                       retryable=False)
    try:
        done, _ = await asyncio.wait({task}, timeout=remaining)
    except BaseException:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
    if task not in done:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise LLMError("BUDGET_DEADLINE", f"运行预算时间已耗尽（{stage}）",
                       retryable=False)
    return await task


def _estimate_input_tokens(system: str, user: str) -> int:
    """请求前的保守输入估算；真实 usage 返回后按实际值结算退差额。"""
    payload_bytes = len(system.encode("utf-8")) + len(user.encode("utf-8"))
    return max(1, 16 + (payload_bytes + 2) // 3)


def _usage_known(usage: Mapping[str, Any] | None) -> bool:
    if not usage:
        return False
    return any(key in usage for key in (
        "input_tokens", "output_tokens", "prompt_tokens", "completion_tokens",
    ))


def normalize_usage(usage: Mapping[str, Any] | None) -> dict[str, int]:
    """把 OpenAI / Anthropic 两种 usage 载荷归一为统一口径。

    - input_tokens：完整输入量。OpenAI prompt_tokens 已含缓存命中；Anthropic
      input_tokens 只计非缓存部分，需加上 cache_read/cache_creation。
    - cached_input_tokens：缓存命中（OpenAI prompt_tokens_details.cached_tokens
      / Anthropic cache_read_input_tokens）
    - cache_write_tokens：Anthropic prompt caching 的写入量
    - reasoning_tokens：OpenAI o/gpt-5 系思考 token
    """

    def _int(source: Mapping[str, Any], *keys: str) -> int:
        for key in keys:
            value = source.get(key)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return 0

    raw = dict(usage or {})
    cached = 0
    cache_write = 0
    reasoning = 0
    prompt_details = raw.get("prompt_tokens_details")
    if isinstance(prompt_details, Mapping):
        cached = _int(prompt_details, "cached_tokens")
    completion_details = raw.get("completion_tokens_details")
    if isinstance(completion_details, Mapping):
        reasoning = _int(completion_details, "reasoning_tokens")
    # Anthropic：顶层 cache_read_input_tokens / cache_creation_input_tokens，
    # 以及 cache_creation: {ephemeral_5m_input_tokens, ...} 嵌套形态
    cached = max(cached, _int(raw, "cache_read_input_tokens"))
    cache_write = _int(raw, "cache_creation_input_tokens")
    cache_creation = raw.get("cache_creation")
    if isinstance(cache_creation, Mapping):
        cache_write = max(
            cache_write,
            _int(cache_creation, "ephemeral_5m_input_tokens")
            + _int(cache_creation, "ephemeral_1h_input_tokens"),
        )
    input_tokens = _int(raw, "prompt_tokens", "input_tokens")
    if "prompt_tokens" not in raw and (
        "cache_read_input_tokens" in raw or "cache_creation_input_tokens" in raw
        or isinstance(cache_creation, Mapping)
    ):
        input_tokens += cached + cache_write
    return {
        "input_tokens": input_tokens,
        "output_tokens": _int(raw, "completion_tokens", "output_tokens"),
        "cached_input_tokens": cached,
        "cache_write_tokens": cache_write,
        "reasoning_tokens": reasoning,
    }


def _dispatch_usage(
    config: Any, usage: Mapping[str, Any] | None, *, elapsed_ms: int = 0,
) -> None:
    """一次成功调用后的用量分发：工作流台账 + 全局记录器。"""
    normalized = normalize_usage(usage)
    if not (normalized["input_tokens"] or normalized["output_tokens"]):
        return  # 上游未上报 usage（部分流式网关）——不虚构用量
    stage = _stage_var.get() or "unlabeled"
    ledger = _ledger_var.get()
    if ledger is not None:
        row = ledger.setdefault(stage, {key: 0 for key in _LEDGER_KEYS})
        row["calls"] += 1
        for key in ("input_tokens", "output_tokens",
                    "cached_input_tokens", "cache_write_tokens"):
            row[key] += normalized[key]
    recorder = _usage_recorder
    if recorder is not None:
        scope = _scope_var.get() or {}
        try:
            recorder({
                **normalized,
                "elapsed_ms": max(0, int(elapsed_ms)),
                "model": config.model,
                "api_format": config.api_format,
                "category": scope.get("category") or "other",
                "run_id": scope.get("run_id"),
                "stage": _stage_var.get(),
                "run_instance_id": scope.get("run_instance_id") or "",
                "operation": scope.get("operation") or "",
                "status": "ok",
            })
        except Exception:
            pass


def _dispatch_failure(config: Any, exc: BaseException) -> None:
    """一次失败请求尝试的记账（计划 §16.5：成功、失败均计尝试，失败计数可见）。

    重试循环中的每次失败各记一行；只存异常类别——异常文本可能携带 URL/密钥，
    不落库。token 全 0、不计耗时、不参与 TPS：失败行只用于尝试次数与失败率。
    """
    recorder = _usage_recorder
    if recorder is None:
        return
    scope = _scope_var.get() or {}
    try:
        recorder({
            "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0,
            "cache_write_tokens": 0, "reasoning_tokens": 0, "elapsed_ms": 0,
            "model": getattr(config, "model", "") or "",
            "api_format": getattr(config, "api_format", "") or "",
            "category": scope.get("category") or "other",
            "run_id": scope.get("run_id"),
            "stage": _stage_var.get(),
            "run_instance_id": scope.get("run_instance_id") or "",
            "operation": scope.get("operation") or "",
            "status": "error",
            "error_code": type(exc).__name__[:64],
        })
    except Exception:
        pass


def _accumulate_usage(response: LLMResponse) -> None:
    _dispatch_usage(response, response.usage)


def token_ledger_report(ledger: dict) -> dict:
    """台账快照 + 合计，供持久化审计使用。"""
    stages = {stage: dict(row) for stage, row in (ledger or {}).items()}
    stages["total"] = {
        key: sum(row.get(key, 0) for row in (ledger or {}).values())
        for key in _LEDGER_KEYS
    }
    return stages


def token_ledger_snapshot() -> dict:
    """Read the active workflow ledger without replacing or resetting it."""
    return token_ledger_report(_ledger_var.get() or {})


def retry_delays(max_retries: int = MAX_RETRIES) -> list[float]:
    return [min(BASE_DELAY * (2 ** index), MAX_DELAY) for index in range(max(0, max_retries))]


def is_retryable(exc: Exception) -> bool:
    if isinstance(exc, LLMError):
        return exc.retryable
    if isinstance(exc, (json.JSONDecodeError, LLMResponseError)):
        return True
    message = str(exc).lower()
    return any(token in message for token in (
        "429", "500", "502", "503", "504", "timeout", "rate limit", "overloaded", "capacity",
    ))


def _resolved(
    config: Mapping[str, Any] | LLMConfig,
    *,
    max_tokens: int | None,
    temperature: float | None,
    thinking_effort: str | None,
    timeout_seconds: float | None,
) -> tuple[LLMConfig, int, float, str]:
    resolved = LLMConfig.from_mapping(config)
    overrides: dict[str, Any] = {}
    if timeout_seconds is not None:
        overrides["timeout_seconds"] = timeout_seconds
    if overrides:
        resolved = resolved.with_overrides(**overrides)
    resolved.validate_for_request()
    return (
        resolved,
        max(1, int(max_tokens if max_tokens is not None else resolved.max_tokens)),
        float(temperature if temperature is not None else resolved.temperature),
        str(thinking_effort if thinking_effort is not None else resolved.thinking_effort),
    )


def _budget_before_attempt(budget: Any, estimated_output_tokens: float,
                           estimated_input_tokens: float) -> None:
    """发起尝试前预占（§16.6）；BudgetExhausted 统一转为不可重试 LLMError。"""
    if budget is None:
        return
    hook = getattr(budget, "before_model_attempt", None)
    if hook is None:
        return
    try:
        hook(estimated_output_tokens=estimated_output_tokens,
             estimated_input_tokens=estimated_input_tokens)
    except LLMError:
        raise
    except Exception as exc:
        raise LLMError("BUDGET_EXHAUSTED", str(exc)[:200], retryable=False) from exc


def _budget_after_attempt(budget: Any, usage: Mapping[str, Any] | None,
                          reserved_output_tokens: float,
                          reserved_input_tokens: float,
                          *, usage_known: bool) -> None:
    if budget is None:
        return
    hook = getattr(budget, "after_model_attempt", None)
    if hook is None:
        return
    # 供应商原始 usage（prompt/completion_tokens）先归一化为统一口径再结算
    hook(usage=normalize_usage(usage) if usage_known else None,
         reserved_output_tokens=reserved_output_tokens,
         reserved_input_tokens=reserved_input_tokens,
         usage_known=usage_known)


def _budget_cancel_attempt(budget: Any, reserved_output_tokens: float,
                           reserved_input_tokens: float) -> None:
    if budget is None:
        return
    hook = getattr(budget, "cancel_model_attempt", None)
    if hook is not None:
        hook(reserved_output_tokens=reserved_output_tokens,
             reserved_input_tokens=reserved_input_tokens)


def _budget_output_soft_hint(budget: Any) -> str:
    """output_tokens 软限制（v7.5）：读取账本按用量生成的催促文案。

    duck-typing（缺方法/异常时零开销）；文案拼在 user 末尾——system 与
    正文前缀不变，供应商前缀缓存不失效。
    """
    if budget is None:
        return ""
    hook = getattr(budget, "llm_output_hint", None)
    if hook is None:
        return ""
    try:
        return str(hook() or "")
    except Exception:
        return ""


def _apply_budget_output_hint(budget: Any, user: str) -> str:
    hint = _budget_output_soft_hint(budget)
    return f"{user}\n\n{hint}" if hint else user


async def complete(
    config: Mapping[str, Any] | LLMConfig,
    *,
    system: str,
    user: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
    thinking_effort: str | None = None,
    timeout_seconds: float | None = None,
    max_retries: int = 0,
    extra_body: dict[str, Any] | None = None,
) -> LLMResponse:
    resolved, token_limit, temp, effort = _resolved(
        config,
        max_tokens=max_tokens,
        temperature=temperature,
        thinking_effort=thinking_effort,
        timeout_seconds=timeout_seconds,
    )
    adapter = get_adapter(resolved.api_format)
    delays = retry_delays(max_retries)
    budget = _budget_var.get()
    user = _apply_budget_output_hint(budget, user)
    semaphore = _model_semaphore()
    estimated_input_tokens = _estimate_input_tokens(system, user)
    from .structured import tool_input_bytes
    structured_bytes = tool_input_bytes(extra_body)
    if structured_bytes:
        estimated_input_tokens += (structured_bytes + 2) // 3
    for attempt in range(max_retries + 1):
        _check_deadline()
        _budget_before_attempt(budget, token_limit, estimated_input_tokens)
        acquired = False
        dispatched = False
        settled = False
        try:
            await _await_before_deadline(semaphore.acquire(), "模型并发排队")
            acquired = True
            # 排队等待也可能耗尽预算时间：发送前复查
            _check_deadline("dispatch")
            started = time.monotonic()
            dispatched = True
            try:
                response = await _await_before_deadline(
                    adapter.complete_once(
                        resolved,
                        system=system,
                        user=user,
                        max_tokens=token_limit,
                        temperature=temp,
                        thinking_effort=effort,
                        extra_body=extra_body,
                    ),
                    "模型请求",
                )
            except Exception as exc:
                # 失败尝试也计账（§16.5）：重试中的每次失败各一行，status=error
                _dispatch_failure(resolved, exc)
                _budget_after_attempt(
                    budget, None, token_limit, estimated_input_tokens,
                    usage_known=False,
                )
                settled = True
                if attempt >= max_retries or not is_retryable(exc):
                    raise
                delay = _clamped_delay(delays[attempt])
            else:
                # elapsed 只计最终成功的那一次请求：重试等待/失败尝试不计入
                # 生成速度（TPS = output_tokens / elapsed）
                elapsed_ms = round((time.monotonic() - started) * 1000)
                _dispatch_usage(resolved, response.usage, elapsed_ms=elapsed_ms)
                known = _usage_known(response.usage)
                _budget_after_attempt(
                    budget, response.usage, token_limit, estimated_input_tokens,
                    usage_known=known,
                )
                settled = True
                return response
        finally:
            if acquired:
                semaphore.release()
            if not settled:
                if dispatched:
                    # 已发送后取消/超时无法确认供应商计费，按预估上界保守结算。
                    _budget_after_attempt(
                        budget, None, token_limit, estimated_input_tokens,
                        usage_known=False,
                    )
                else:
                    # 排队超时/取消时尚未发送，完整退还预占。
                    _budget_cancel_attempt(budget, token_limit, estimated_input_tokens)
        # 退避在信号量外等待：重试退避不占用并发额度（§16.6）
        await asyncio.sleep(delay)
    raise RuntimeError("unreachable")


async def complete_text(
    config: Mapping[str, Any] | LLMConfig,
    **kwargs: Any,
) -> str:
    return (await complete(config, **kwargs)).text


async def complete_json(
    config: Mapping[str, Any] | LLMConfig,
    *,
    system: str,
    user: str,
    expected: type = dict,
    max_retries: int = MAX_RETRIES,
    metadata_out: dict[str, Any] | None = None,
    root_array_key: str | None = None,
    **kwargs: Any,
) -> Any:
    delays = retry_delays(max_retries)
    resolved = LLMConfig.from_mapping(config)
    resolved.validate_for_request()
    for attempt in range(max_retries + 1):
        _check_deadline()
        try:
            response = await complete(
                resolved,
                system=system,
                user=user,
                max_retries=0,
                **kwargs,
            )
            if metadata_out is not None:
                metadata_out.clear()
                metadata_out.update({
                    "finish_reason": response.finish_reason,
                    "request_id": response.request_id,
                    "usage": dict(response.usage or {}),
                })
            try:
                from .structured import requests_result_tool, unwrap_result
                if requests_result_tool(kwargs.get("extra_body")):
                    result = unwrap_result(parse_json(response.text, expected=dict), expected)
                    if metadata_out is not None:
                        metadata_out["structured_recovery"] = {"kind": "native_result_envelope", "field": "result"}
                    return result
                return parse_json(response.text, expected=expected)
            except (ValueError, TypeError) as exc:
                framed = strip_markdown_fence(response.text)
                candidate = extract_balanced_json(framed) or framed
                root_type = None
                try:
                    parsed = json.loads(candidate)
                    root_type = type(parsed).__name__
                except (ValueError, TypeError):
                    parsed = None
                if expected is dict and root_array_key and isinstance(parsed, list) and framed == candidate:
                    # Only an explicit caller contract may wrap a top-level array.
                    # All items remain unchanged and still undergo schema checks.
                    if metadata_out is not None:
                        metadata_out["structured_recovery"] = {
                            "kind": "root_array_wrapper", "field": root_array_key,
                            "response_chars": len(response.text),
                            "response_sha256": hashlib.sha256(response.text.encode()).hexdigest(),
                        }
                    return {root_array_key: parsed}
                if metadata_out is not None:
                    # Diagnose syntax without retaining generated text, material
                    # or credentials in metadata consumed by public event logs.
                    metadata_out["structured_error"] = {
                        "type": type(exc).__name__,
                        "root_type": root_type,
                        "response_chars": len(response.text),
                        "response_sha256": hashlib.sha256(response.text.encode()).hexdigest(),
                        **({"message": exc.msg, "line": exc.lineno,
                            "column": exc.colno, "position": exc.pos}
                           if isinstance(exc, json.JSONDecodeError) else {}),
                    }
                raise
        except LLMError as exc:
            # 预算类错误（BUDGET_*）不可重试；其余 LLMError 按其 retryable 重试
            if not exc.retryable or attempt >= max_retries:
                raise
            await asyncio.sleep(_clamped_delay(delays[attempt]))
        except Exception as exc:
            retryable = is_retryable(exc) or isinstance(exc, (json.JSONDecodeError, ValueError))
            if attempt >= max_retries or not retryable:
                raise
            # 解析失败发生在成功请求之后：退避同样受总 deadline 钳制
            await asyncio.sleep(_clamped_delay(delays[attempt]))
    raise RuntimeError("unreachable")


async def stream_text(
    config: Mapping[str, Any] | LLMConfig,
    *,
    system: str,
    user: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
    thinking_effort: str | None = None,
    timeout_seconds: float | None = None,
) -> AsyncIterator[str]:
    resolved, token_limit, temp, effort = _resolved(
        config,
        max_tokens=max_tokens,
        temperature=temperature,
        thinking_effort=thinking_effort,
        timeout_seconds=timeout_seconds,
    )
    adapter = get_adapter(resolved.api_format)
    budget = _budget_var.get()
    user = _apply_budget_output_hint(budget, user)
    usage_holder: dict[str, Any] = {}
    stream_started = time.monotonic()
    estimated_input_tokens = _estimate_input_tokens(system, user)
    _check_deadline()
    _budget_before_attempt(budget, token_limit, estimated_input_tokens)
    semaphore = _model_semaphore()
    acquired = False
    dispatched = False
    settled = False
    try:
        await _await_before_deadline(semaphore.acquire(), "模型并发排队")
        acquired = True
        # 排队后复查：等待耗尽预算时间即放弃（预占退回）
        _check_deadline("dispatch")
        dispatched = True
        try:
            stream = adapter.stream_once(
                resolved,
                system=system,
                user=user,
                max_tokens=token_limit,
                temperature=temp,
                thinking_effort=effort,
                usage_out=usage_holder,
            )
            while True:
                try:
                    text = await _await_before_deadline(
                        anext(stream), "模型流式请求")
                except StopAsyncIteration:
                    break
                yield text
        except Exception as exc:
            _dispatch_failure(resolved, exc)
            _budget_after_attempt(
                budget, None, token_limit, estimated_input_tokens,
                usage_known=False,
            )
            settled = True
            raise
        # 正常走完整个流后才有 usage（消费方提前 break 或异常中断不计）
        _dispatch_usage(
            resolved, usage_holder,
            elapsed_ms=round((time.monotonic() - stream_started) * 1000),
        )
        known = _usage_known(usage_holder)
        _budget_after_attempt(
            budget, usage_holder, token_limit, estimated_input_tokens,
            usage_known=known,
        )
        settled = True
    finally:
        if acquired:
            semaphore.release()
        if not settled:
            if dispatched:
                # 消费方提前 break/取消：请求已发出，按输入输出估算保守落账。
                _budget_after_attempt(
                    budget, None, token_limit, estimated_input_tokens,
                    usage_known=False,
                )
            else:
                _budget_cancel_attempt(budget, token_limit, estimated_input_tokens)


async def test_connection(
    config: Mapping[str, Any] | LLMConfig,
) -> dict[str, Any]:
    started = time.monotonic()
    set_llm_scope("connection_test")
    try:
        resolved = LLMConfig.from_mapping(config)
        response = await complete(
            resolved,
            system="Reply with OK.",
            user="Hi",
            max_tokens=5,
            temperature=0,
            thinking_effort="off",
            timeout_seconds=min(resolved.timeout_seconds, 30.0),
            max_retries=0,
        )
        return {
            "ok": True,
            "message": f"连接成功 ({resolved.model})",
            "latency_ms": int((time.monotonic() - started) * 1000),
            "request_id": response.request_id,
            "api_format": resolved.api_format,
        }
    except Exception as exc:
        api_format = getattr(locals().get("resolved"), "api_format", None)
        return {
            "ok": False,
            "message": str(exc)[:500],
            "latency_ms": int((time.monotonic() - started) * 1000),
            "request_id": getattr(exc, "request_id", None),
            "api_format": api_format,
        }
