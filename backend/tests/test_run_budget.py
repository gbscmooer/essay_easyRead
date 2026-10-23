"""统一运行预算与有限并发（计划 §16.6 P2）：预占/结算/退款、暂停保额度、
交付预留、deadline 钳制退避、全局模型并发上限、额度不重置。"""

import asyncio
import time
from unittest.mock import patch

from backend.services.deep_research.run_budget import (
    DEPTH_BUDGET_PRESETS,
    BudgetExhausted,
    RunBudget,
)
from backend.services.llm import client as llm_client
from backend.services.llm import set_model_concurrency
from backend.services.llm.errors import LLMError
from backend.services.llm.models import LLMResponse

_CFG = {
    "api_format": "openai_chat", "api_key": "k",
    "base_url": "https://x", "model": "m",
}


# ── RunBudget 账本 ────────────────────────────────────────────────────────────


def test_presets_match_plan_table():
    """§16.6 预设表逐档核对（时间/预留/候选/阅读/尝试/来源/token/批次/修复）。

    lite 按 v7.4.1 run6 无约束实测校准（65 调用/29.8 万 token）上调为
    enforce 可完成的规模；enforce 由 explorer 按 depth=lite 打开。
    v7.5：output_tokens 为软限制，预设放大 10 倍兜底（思考型模型 reasoning
    占输出可达 84%，30k 硬额度会在写作阶段前击穿，run4 DRAFT_WRITE_FAILED）。
    """
    assert DEPTH_BUDGET_PRESETS["lite"] == {
        "total_active_seconds": 900, "delivery_reserve_seconds": 240,
        "candidate_papers": 50, "read_papers": 8,
        "model_attempts": 40, "source_requests": 80,
        "input_tokens": 160_000, "output_tokens": 300_000,
        "supplement_batches": 0, "delivery_repairs": 1,
    }
    assert DEPTH_BUDGET_PRESETS["quick"]["total_active_seconds"] == 480
    assert DEPTH_BUDGET_PRESETS["quick"]["output_tokens"] == 250_000
    assert DEPTH_BUDGET_PRESETS["standard"]["model_attempts"] == 80
    assert DEPTH_BUDGET_PRESETS["standard"]["output_tokens"] == 600_000
    assert DEPTH_BUDGET_PRESETS["deep"]["output_tokens"] == 1_200_000


def test_baseline_used_bounds_migration_enforcement():
    """迁移基线：legacy_usage 计入 used 但不占 enforce 限额（增量约束）。"""
    preset = {"model_attempts": 10}
    state = {"resources": {"model_attempts": {"used": 6.0, "reserved": 0.0}}}
    budget = RunBudget.restore(preset, enforce=True, state=state,
                               baseline_used={"model_attempts": 6.0})
    # 旧账 6 次不占新额度：仍可预占 10 次
    assert budget.remaining("model_attempts") == 10.0
    assert budget.reserve("model_attempts", 10) is True
    assert budget.reserve("model_attempts", 1) is False
    snap = budget.snapshot()
    assert snap["baseline_used"]["model_attempts"] == 6.0
    # 已结算的增量消耗计入 counted_used；预占只反映在 remaining 里
    assert snap["resources"]["model_attempts"]["counted_used"] == 0.0
    assert snap["resources"]["model_attempts"]["reserved"] == 10.0
    # 快照恢复幂等：baseline 随快照持久，二次 restore 不丢
    again = RunBudget.restore(preset, enforce=True, state=snap)
    assert again.remaining("model_attempts") == 0.0


def test_atomic_reserve_last_unit_not_double_spent():
    """并发竞争最后余额：limit=2、三个并发任务各预占 1 → 恰好 2 成功。"""
    budget = RunBudget({"model_attempts": 2}, enforce=True)

    async def compete():
        return budget.reserve("model_attempts", 1)

    async def race():
        return await asyncio.gather(compete(), compete(), compete())

    results = asyncio.run(race())
    assert sorted(results) == [False, True, True]
    snap = budget.snapshot()["resources"]["model_attempts"]
    assert snap["reserved"] == 2 and snap["remaining"] == 0


def test_settle_refunds_difference_and_conservative_overage():
    budget = RunBudget({"output_tokens": 100}, enforce=True)
    assert budget.reserve("output_tokens", 40) is True
    budget.settle("output_tokens", 40, 25)  # 实际 25：退 15
    assert budget.remaining("output_tokens") == 75
    # 实际超出估算不回拨（保守扣账）
    budget.settle("output_tokens", 10, 30)
    assert budget.remaining("output_tokens") == 45
    # 实际总量超过 limit：保守落账（remaining 0，不为负），并记一次耗尽事件
    budget.settle("output_tokens", 10, 80)
    assert budget.remaining("output_tokens") == 0
    assert any(row["resource"] == "output_tokens" for row in budget.exhaustion_log)


def test_pause_stops_active_clock_and_resume_keeps_quota():
    """暂停停走活动时钟；恢复沿用剩余额度，任何计数不重置（§16.6 退出标准）。"""
    clock = {"now": 1000.0}
    budget = RunBudget({"total_active_seconds": 100, "delivery_reserve_seconds": 20,
                        "model_attempts": 5}, enforce=True, clock=lambda: clock["now"])
    assert budget.reserve("model_attempts", 2) is True
    budget.settle("model_attempts", 2, 2)

    clock["now"] += 10.0            # 活动 10s
    assert budget.active_seconds() == 10.0
    budget.pause()
    clock["now"] += 500.0           # 暂停区间不计入活动时间
    assert budget.paused is True
    assert budget.active_seconds() == 10.0
    assert budget.research_seconds_left() == 70.0  # 100 − 20 预留 − 10 活动
    assert budget.delivery_seconds_left() == 90.0

    budget.resume()
    clock["now"] += 5.0             # 恢复后继续累计
    assert budget.active_seconds() == 15.0
    # 额度未重置：已用 2/5 保持
    assert budget.snapshot()["resources"]["model_attempts"]["used"] == 2
    assert budget.remaining("model_attempts") == 3


def test_restore_from_snapshot_continues_remaining_quota():
    """检查点恢复：已用量与活动时钟延续，limit 来自当前预设（不重置）。"""
    clock = {"now": 0.0}
    budget = RunBudget({"model_attempts": 10, "total_active_seconds": 60},
                       enforce=True, clock=lambda: clock["now"])
    budget.settle("model_attempts", 1, 4)  # 已消耗 4 次尝试
    clock["now"] += 12.0
    state = budget.snapshot()

    clock2 = {"now": 999.0}  # 新进程：时钟基准不同
    restored = RunBudget.restore({"model_attempts": 10, "total_active_seconds": 60},
                                 enforce=True, state=state, clock=lambda: clock2["now"])
    snap = restored.snapshot()["resources"]["model_attempts"]
    assert snap["used"] == 4 and snap["remaining"] == 6  # 不重置
    assert restored.active_seconds() == 12.0  # 活动时间延续
    clock2["now"] += 3.0
    assert restored.active_seconds() == 15.0


def test_accounting_mode_never_blocks_but_logs_exhaustion():
    """legacy 记账模式：reserve 永远成功；超限仍记耗尽事件供观测。"""
    budget = RunBudget({"model_attempts": 1}, enforce=False)
    assert budget.reserve("model_attempts", 1) is True
    assert budget.reserve("model_attempts", 5) is True  # 不阻断
    assert budget.exhaustion_log  # 超限可见
    with_end = RunBudget({"model_attempts": 1}, enforce=True)
    with_end.reserve("model_attempts", 1)
    assert with_end.reserve("model_attempts", 1) is False


def test_model_attempt_hooks_reserve_and_settle():
    budget = RunBudget({"model_attempts": 3, "output_tokens": 1000,
                        "input_tokens": 2000}, enforce=True)
    budget.before_model_attempt(estimated_output_tokens=800)
    budget.after_model_attempt(
        usage={"input_tokens": 700, "output_tokens": 300},
        reserved_output_tokens=800)
    snap = budget.snapshot()["resources"]
    assert snap["model_attempts"]["used"] == 1
    assert snap["output_tokens"]["used"] == 300  # 实际 300，退 500
    assert snap["input_tokens"]["used"] == 700
    # 额度耗尽时预占抛 BudgetExhausted（LLM Core 转为不可重试错误）
    small = RunBudget({"model_attempts": 1, "output_tokens": 100}, enforce=True)
    small.before_model_attempt(50)
    small.after_model_attempt(usage={"output_tokens": 10}, reserved_output_tokens=50)
    try:
        small.before_model_attempt(50)
        raise AssertionError("should exhaust")
    except BudgetExhausted as exc:
        assert exc.resource == "model_attempts"


def test_output_tokens_soft_limit_never_blocks_attempt():
    """v7.5 软限制：仅 output_tokens 超限时不抛 BudgetExhausted，调用照常
    预占/结算；超限只记一次耗尽事件，实际用量可越过 limit（保守落账）。"""
    budget = RunBudget({"model_attempts": 10, "input_tokens": 100_000,
                        "output_tokens": 100}, enforce=True)
    budget.before_model_attempt(estimated_output_tokens=500)
    budget.after_model_attempt(usage={"input_tokens": 10, "output_tokens": 80},
                               reserved_output_tokens=500)
    # 已用 80/100，下一次预占 500 仍放行（硬限制会在此抛）
    budget.before_model_attempt(estimated_output_tokens=500)
    budget.after_model_attempt(usage={"input_tokens": 10, "output_tokens": 200},
                               reserved_output_tokens=500)
    snap = budget.snapshot()["resources"]["output_tokens"]
    assert snap["used"] == 280 and snap["remaining"] == 0  # 不为负
    assert sum(1 for row in budget.exhaustion_log
               if row["resource"] == "output_tokens") == 1


def test_llm_output_hint_thresholds_and_accounting_mode():
    """软限制提示词分档：≥60% 提醒、≥85% 警告、未达阈值 None；
    记账模式（enforce=False）行为不变恒为 None。"""
    def _used(value: float) -> RunBudget:
        b = RunBudget({"output_tokens": 100}, enforce=True)
        b.settle("output_tokens", 0, value)
        return b

    assert _used(59).llm_output_hint() is None
    gentle = _used(60).llm_output_hint()
    assert gentle is not None and "提醒" in gentle
    strong = _used(85).llm_output_hint()
    assert strong is not None and "警告" in strong
    # 超限（remaining 0）仍取最高档
    assert (_used(120).llm_output_hint() or "").endswith("宁短勿长。")
    accounting = RunBudget({"output_tokens": 100}, enforce=False)
    accounting.settle("output_tokens", 0, 100)
    assert accounting.llm_output_hint() is None


# ── LLM Core：deadline / 并发 / 预算钩子 ─────────────────────────────────────


class _OkStub:
    async def complete_once(self, *a, **k):
        return LLMResponse(text="a", model="m", api_format="openai_chat",
                           usage={"prompt_tokens": 10, "completion_tokens": 5})


def _cleanup():
    llm_client._deadline_var.set(None)
    llm_client._budget_var.set(None)
    llm_client._scope_var.set(None)
    set_model_concurrency(2)


def test_deadline_cancels_inflight_request():
    """总 deadline 同时约束在途请求，不能只阻止下一次调用。"""
    class _SlowStub:
        def __init__(self):
            self.calls = 0

        async def complete_once(self, *a, **k):
            self.calls += 1
            await asyncio.sleep(0.04)
            return LLMResponse(text="a", model="m", api_format="openai_chat", usage={})

    slow = _SlowStub()
    try:
        with patch.object(llm_client, "get_adapter", lambda fmt: slow):
            llm_client.set_llm_deadline(time.monotonic() + 0.03)
            try:
                asyncio.run(llm_client.complete(_CFG, system="s", user="u", max_tokens=8))
                raise AssertionError("should hit deadline")
            except LLMError as exc:
                assert exc.code == "BUDGET_DEADLINE" and exc.retryable is False
            assert slow.calls == 1
    finally:
        _cleanup()


def test_retry_backoff_clamped_by_deadline():
    """429 退避受总 deadline 钳制：3s 基础退避被截断，不空烧等待（§16.6）。"""
    class _RateLimited:
        async def complete_once(self, *a, **k):
            raise RuntimeError("429 rate limit exceeded")

    started = time.monotonic()
    try:
        with patch.object(llm_client, "get_adapter", lambda fmt: _RateLimited()):
            llm_client.set_llm_deadline(time.monotonic() + 0.05)
            try:
                asyncio.run(llm_client.complete(
                    _CFG, system="s", user="u", max_tokens=8, max_retries=3))
                raise AssertionError("should not succeed")
            except LLMError as exc:
                assert exc.code == "BUDGET_DEADLINE"
        # 0.05s deadline 内终止，而不是 3s+ 的完整退避链
        assert time.monotonic() - started < 1.5
    finally:
        _cleanup()


def test_global_model_concurrency_caps_inflight_requests():
    """全局并发默认 2：5 个并发请求的在途峰值恰为 2（叠加池不再放大）。"""
    inflight = {"now": 0, "max": 0}

    class _TrackingStub:
        async def complete_once(self, *a, **k):
            inflight["now"] += 1
            inflight["max"] = max(inflight["max"], inflight["now"])
            await asyncio.sleep(0.02)
            inflight["now"] -= 1
            return LLMResponse(text="a", model="m", api_format="openai_chat", usage={})

    async def run_five():
        return await asyncio.gather(*(
            llm_client.complete(_CFG, system="s", user=f"u{i}", max_tokens=8)
            for i in range(5)
        ))

    try:
        set_model_concurrency(2)
        with patch.object(llm_client, "get_adapter", lambda fmt: _TrackingStub()):
            responses = asyncio.run(run_five())
        assert len(responses) == 5
        assert inflight["max"] == 2  # 峰值被全局信号量封顶
        assert set_model_concurrency(8) == 8  # 上限 8
        assert set_model_concurrency(9) == 8  # 收口不超上限
    finally:
        _cleanup()


def test_budget_enforced_through_complete_attempts():
    """强制模式穿全链路：尝试额度耗尽 → 不可重试 BUDGET_EXHAUSTED；
    失败重试各占一次尝试（含失败/重试的口径，§16.6）。"""
    budget = RunBudget({"model_attempts": 3, "output_tokens": 2000,
                        "input_tokens": 10_000}, enforce=True)
    calls = {"n": 0}

    class _FlakyThenOk:
        async def complete_once(self, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("503 overloaded")  # 可重试失败
            return LLMResponse(text="a", model="m", api_format="openai_chat",
                               usage={"prompt_tokens": 100, "completion_tokens": 20})

    try:
        llm_client.set_llm_budget(budget)
        with patch.object(llm_client, "get_adapter", lambda fmt: _FlakyThenOk()):
            asyncio.run(llm_client.complete(
                _CFG, system="s", user="u", max_tokens=500, max_retries=1))
            asyncio.run(llm_client.complete(
                _CFG, system="s", user="u", max_tokens=500))
            try:
                asyncio.run(llm_client.complete(
                    _CFG, system="s", user="u", max_tokens=500))
                raise AssertionError("budget should be exhausted")
            except LLMError as exc:
                assert exc.code == "BUDGET_EXHAUSTED" and exc.retryable is False
    finally:
        _cleanup()

    snap = budget.snapshot()["resources"]
    assert snap["model_attempts"]["used"] == 3  # 失败 1 + 成功 2
    # 首次 503 已发送但 usage 未知，按 max_tokens=500 保守扣账；两次成功各 20。
    assert snap["output_tokens"]["used"] == 540
    assert calls["n"] == 3  # 第 4 次在预占阶段被拒，未发请求


def test_complete_appends_output_soft_hint_at_user_end():
    """全链路：用量达阈值后提示词追加在 user 末尾（前缀不变，保住缓存）；
    未达阈值 user 原样透传。"""
    captured: list[str] = []

    class _Capture:
        async def complete_once(self, *a, **k):
            captured.append(k["user"])
            return LLMResponse(text="a", model="m", api_format="openai_chat",
                               usage={"prompt_tokens": 10, "completion_tokens": 5})

    budget = RunBudget({"model_attempts": 10, "input_tokens": 100_000,
                        "output_tokens": 100}, enforce=True)
    try:
        llm_client.set_llm_budget(budget)
        with patch.object(llm_client, "get_adapter", lambda fmt: _Capture()):
            asyncio.run(llm_client.complete(
                _CFG, system="s", user="正文", max_tokens=10))
            assert captured[-1] == "正文"  # 5/100：未达阈值不改写
            budget.settle("output_tokens", 0, 90)  # 95/100 ≥ 85%
            asyncio.run(llm_client.complete(
                _CFG, system="s", user="正文", max_tokens=10))
            hint = budget.llm_output_hint() or ""
            assert captured[-1] == f"正文\n\n{hint}"
            assert captured[-1].endswith(hint)  # 追加在末尾
    finally:
        _cleanup()


def test_nested_json_retries_counted_per_provider_attempt():
    """嵌套重试（complete_json 外层 + 内层 provider 尝试）逐次计账不漏不重。"""
    budget = RunBudget({"model_attempts": 5, "output_tokens": 10_000,
                        "input_tokens": 100_000}, enforce=True)
    calls = {"n": 0}

    class _BadJson:
        async def complete_once(self, *a, **k):
            calls["n"] += 1
            return LLMResponse(text="not-json", model="m", api_format="openai_chat",
                               usage={"prompt_tokens": 10, "completion_tokens": 2})

    try:
        llm_client.set_llm_budget(budget)
        with patch.object(llm_client, "get_adapter", lambda fmt: _BadJson()):
            try:
                asyncio.run(llm_client.complete_json(
                    _CFG, system="s", user="u", max_retries=2, max_tokens=500))
                raise AssertionError("should fail to parse")
            except Exception:
                pass
    finally:
        _cleanup()
    assert calls["n"] == 3  # 首发 + 2 次解析重试
    assert budget.snapshot()["resources"]["model_attempts"]["used"] == 3


def test_pause_resume_through_budget_clock_keeps_llm_deadline_semantics():
    """暂停→恢复→deadline 语义：恢复后 deadline 按剩余活动时间重算。"""
    clock = {"now": 0.0}
    budget = RunBudget({"total_active_seconds": 100, "delivery_reserve_seconds": 20},
                       enforce=True, clock=lambda: clock["now"])
    clock["now"] += 60.0
    budget.pause()
    clock["now"] += 1000.0  # 暂停一千年也不消耗
    budget.resume()
    deadline = budget.deadline_after(budget.delivery_seconds_left())
    clock["now"] += 1.0
    # 交付侧剩 40s（100−60 活动），deadline 在恢复后 40s（不含暂停区间）
    assert abs((deadline - clock["now"]) - 39.0) < 0.01


def test_input_budget_is_reserved_before_dispatch():
    """输入额度在请求前检查；上次实际输入超额后不能继续发送。"""
    budget = RunBudget({"model_attempts": 3, "input_tokens": 10,
                        "output_tokens": 100}, enforce=True)
    budget.before_model_attempt(estimated_output_tokens=10, estimated_input_tokens=5)
    budget.after_model_attempt(
        usage={"input_tokens": 12, "output_tokens": 1},
        reserved_output_tokens=10, reserved_input_tokens=5,
    )
    try:
        budget.before_model_attempt(estimated_output_tokens=10, estimated_input_tokens=1)
        raise AssertionError("input budget should be exhausted")
    except BudgetExhausted as exc:
        assert exc.resource == "input_tokens"


def test_unknown_usage_keeps_conservative_input_and_output_charge():
    """请求已发送但 usage 未知时保留预估上界，不能全额退款。"""
    budget = RunBudget({"model_attempts": 2, "input_tokens": 100,
                        "output_tokens": 100}, enforce=True)
    budget.before_model_attempt(estimated_output_tokens=40, estimated_input_tokens=20)
    budget.after_model_attempt(
        usage=None, reserved_output_tokens=40, reserved_input_tokens=20,
        usage_known=False,
    )
    snap = budget.snapshot()
    assert snap["resources"]["model_attempts"]["used"] == 1
    assert snap["resources"]["input_tokens"]["used"] == 20
    assert snap["resources"]["output_tokens"]["used"] == 40
    assert snap["usage_unknown_attempts"] == 1


def test_restore_converts_inflight_reservations_to_unknown_used():
    """崩溃快照里的未决预占按未知结果转已用，恢复不能重新花费。"""
    preset = {"model_attempts": 3, "input_tokens": 100, "output_tokens": 100}
    budget = RunBudget(preset, enforce=True)
    budget.before_model_attempt(estimated_output_tokens=40, estimated_input_tokens=20)
    restored = RunBudget.restore(preset, enforce=True, state=budget.snapshot())
    resources = restored.snapshot()["resources"]
    assert resources["model_attempts"]["used"] == 1
    assert resources["input_tokens"]["used"] == 20
    assert resources["output_tokens"]["used"] == 40
    assert all(row["reserved"] == 0 for row in resources.values())
    assert restored.snapshot()["usage_unknown_attempts"] == 1


def test_queue_deadline_refunds_unsent_reservations():
    """等待全局并发位超时属于未发送：及时结束且不泄漏预占。"""
    budget = RunBudget({"model_attempts": 2, "input_tokens": 100,
                        "output_tokens": 100}, enforce=True)

    async def blocked_call():
        set_model_concurrency(1)
        semaphore = llm_client._model_semaphore()
        await semaphore.acquire()
        try:
            llm_client.set_llm_budget(budget)
            llm_client.set_llm_deadline(time.monotonic() + 0.02)
            try:
                await llm_client.complete(
                    _CFG, system="s", user="u", max_tokens=10)
                raise AssertionError("queue should hit deadline")
            except LLMError as exc:
                assert exc.code == "BUDGET_DEADLINE"
        finally:
            semaphore.release()

    try:
        asyncio.run(blocked_call())
    finally:
        _cleanup()
    resources = budget.snapshot()["resources"]
    assert resources["model_attempts"]["used"] == 0
    assert resources["model_attempts"]["reserved"] == 0
    assert resources["input_tokens"]["reserved"] == 0
    assert resources["output_tokens"]["reserved"] == 0


def test_failed_provider_attempt_is_conservatively_charged_by_core():
    """Core 必须把已发送但失败、无 usage 的请求传给未知用量结算路径。"""
    budget = RunBudget({"model_attempts": 2, "input_tokens": 100,
                        "output_tokens": 100}, enforce=True)

    class _TimeoutStub:
        async def complete_once(self, *a, **k):
            raise TimeoutError("provider timeout")

    try:
        llm_client.set_llm_budget(budget)
        with patch.object(llm_client, "get_adapter", lambda fmt: _TimeoutStub()):
            try:
                asyncio.run(llm_client.complete(
                    _CFG, system="system", user="user", max_tokens=10))
                raise AssertionError("provider should fail")
            except TimeoutError:
                pass
    finally:
        _cleanup()
    snap = budget.snapshot()
    assert snap["resources"]["model_attempts"]["used"] == 1
    assert snap["resources"]["output_tokens"]["used"] == 10
    assert snap["resources"]["input_tokens"]["used"] > 0
    assert snap["usage_unknown_attempts"] == 1


def test_stream_consumption_is_bounded_by_run_deadline():
    """流式等待也受总 deadline 约束，超时后按未知用量保守结算。"""
    budget = RunBudget({"model_attempts": 2, "input_tokens": 100,
                        "output_tokens": 100}, enforce=True)

    class _SlowStreamStub:
        async def stream_once(self, *a, **k):
            await asyncio.sleep(0.05)
            yield "late"

    async def consume():
        return [part async for part in llm_client.stream_text(
            _CFG, system="s", user="u", max_tokens=10)]

    try:
        llm_client.set_llm_budget(budget)
        llm_client.set_llm_deadline(time.monotonic() + 0.02)
        with patch.object(llm_client, "get_adapter", lambda fmt: _SlowStreamStub()):
            try:
                asyncio.run(consume())
                raise AssertionError("stream should hit deadline")
            except LLMError as exc:
                assert exc.code == "BUDGET_DEADLINE"
    finally:
        _cleanup()
    snap = budget.snapshot()
    assert snap["resources"]["output_tokens"]["used"] == 10
    assert snap["usage_unknown_attempts"] == 1
