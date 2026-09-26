"""统一运行预算 Core（计划 §16.6 P2：统一预算和有限并发）。

一次研究 run 的全部共享资源额度：活动墙钟（含交付预留）、底层模型尝试
（含失败/重试）、输入/输出 token、来源 HTTP 尝试、材料数量、补充批次、
交付修复次数。调用前原子预占（check-and-deduct，并发下不会多个任务同时
花掉最后余额），结束后按实际用量结算并退还差额；暂停停走活动时钟，恢复
沿用剩余额度——任何路径都不重置已消耗量。

预设表是 §16.6 的新流程实验初值（lite 50 篇为用户指定上限，不要求凑满），
不是对现有 legacy 流程直接换常量：legacy run 默认只做记账（enforce=False），
新 lite 实验路径（P3）显式开启强制。

时间语义：活动墙钟按 run 实际执行累计，暂停区间不计（"不把暂停算计算"
的观测口径）；`research_seconds_left` 扣除交付预留——研究侧先到预留线即
停止新增研究，交付侧继续用 `delivery_seconds_left`。测试可注入 clock。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

# §16.6 预设：资源上限（实验初值，非完成指标；单位见键名）
# lite 按 v7.4.1 run6 无约束实测校准（65 次调用 / 29.8 万 token，其中 10.3 万
# reasoning）：enforce 开启后限额约束新增调用，配合分级降级（跳过增强类调用）
# 应完整跑完「50 篇召回 + 8 篇选材提取 + 报告」而非中途硬停。
# v7.5：output_tokens 改为软限制（不再硬拦截调用，见 before_model_attempt /
# llm_output_hint），预设放大 10 倍兜底——思考型模型（如 deepseek-flash）
# reasoning 可占输出 84%，30k 硬额度在写作阶段前即被击穿（run4
# DRAFT_WRITE_FAILED）；硬停兜底交给 model_attempts / input_tokens / 总时长。
DEPTH_BUDGET_PRESETS: dict[str, dict[str, float]] = {
    "lite": {
        "total_active_seconds": 900, "delivery_reserve_seconds": 240,
        "candidate_papers": 50, "read_papers": 8,
        "model_attempts": 40, "source_requests": 80,
        "input_tokens": 160_000, "output_tokens": 300_000,
        "supplement_batches": 0, "delivery_repairs": 1,
    },
    "quick": {
        "total_active_seconds": 480, "delivery_reserve_seconds": 120,
        "candidate_papers": 80, "read_papers": 12,
        "model_attempts": 40, "source_requests": 32,
        "input_tokens": 150_000, "output_tokens": 250_000,
        "supplement_batches": 1, "delivery_repairs": 1,
    },
    "standard": {
        "total_active_seconds": 1200, "delivery_reserve_seconds": 300,
        "candidate_papers": 180, "read_papers": 24,
        "model_attempts": 80, "source_requests": 64,
        "input_tokens": 400_000, "output_tokens": 600_000,
        "supplement_batches": 2, "delivery_repairs": 1,
    },
    "deep": {
        "total_active_seconds": 2700, "delivery_reserve_seconds": 600,
        "candidate_papers": 360, "read_papers": 40,
        "model_attempts": 150, "source_requests": 120,
        "input_tokens": 800_000, "output_tokens": 1_200_000,
        "supplement_batches": 3, "delivery_repairs": 1,
    },
}

# output_tokens 软限制催促文案：按已用比例取最高命中档，由 LLM Core 拼接到
# 用户消息末尾（不动 system 与正文前缀，保住供应商前缀缓存）。
_OUTPUT_SOFT_HINTS: tuple[tuple[float, str], ...] = (
    (0.85,
     "【输出预算警告】本次运行的输出 token 预算即将耗尽。请立即大幅压缩："
     "跳过冗余推理与铺垫，只输出完成任务所必需的最短内容，宁短勿长。"),
    (0.60,
     "【输出预算提醒】本次运行的输出 token 预算已消耗过半。请精简思考与"
     "输出：避免重复解释与不必要的展开，直接给出必要结果。"),
)

# 预算性资源（整数额度，预占/结算/退款）；时间单独处理
_COUNTABLE = ("candidate_papers", "read_papers", "model_attempts",
              "source_requests", "input_tokens", "output_tokens",
              "supplement_batches", "delivery_repairs")


class BudgetExhausted(RuntimeError):
    """某项资源额度已用尽（enforce 模式下由 LLM Core 转为不可重试 LLMError）。"""

    def __init__(self, resource: str, remaining: float) -> None:
        self.resource = resource
        self.remaining = remaining
        super().__init__(f"预算已用尽：{resource}（剩余 {remaining}）")


class RunBudget:
    """单次 run 的共享预算账本。线程安全（记录器线程可能并发结算用量）。

    enforce=False（legacy 默认）：只记账与产生耗尽事件，reserve 永远成功，
    不改变既有行为；enforce=True（新流程实验）：超额 reserve 失败并抛
    BudgetExhausted，由调用边界决定停止新工作（在途调用允许完成）。
    例外：output_tokens 为软限制（v7.5）——enforce 下超限也只记账，靠
    llm_output_hint() 的末尾提示词催促模型节约，不再硬停调用。
    """

    def __init__(self, preset: dict | None = None, *, enforce: bool = False,
                 clock: Callable[[], float] = time.monotonic) -> None:
        config = dict(preset or DEPTH_BUDGET_PRESETS["standard"])
        self.enforce = bool(enforce)
        self._clock = clock
        self._lock = threading.Lock()
        # baseline：迁移基线（legacy_usage）——enforce 限额只约束「基线之后的
        # 新增消耗」。旧 run 续跑迁移时，历史消耗进 used 并同步抬高 baseline，
        # 使 post_migration_budget 是真正的增量额度而不是被旧账立即击穿。
        self._resources: dict[str, dict[str, float]] = {
            name: {"limit": max(0.0, float(config.get(name, 0.0))),
                   "used": 0.0, "reserved": 0.0, "baseline": 0.0}
            for name in _COUNTABLE
        }
        self._total_active_seconds = max(0.0, float(config.get("total_active_seconds", 0.0)))
        self._delivery_reserve_seconds = max(
            0.0, float(config.get("delivery_reserve_seconds", 0.0)))
        # 活动时钟：_active_seconds 累计执行时间，暂停区间不计
        self._active_seconds = 0.0
        self._resumed_at: float | None = self._clock()
        self._paused = False
        self.exhaustion_log: list[dict] = []
        self.usage_unknown_attempts = 0

    @classmethod
    def restore(cls, preset: dict | None, *, enforce: bool = False,
                state: dict | None = None,
                baseline_used: dict[str, float] | None = None,
                clock: Callable[[], float] = time.monotonic) -> "RunBudget":
        """从检查点快照恢复（§16.6：暂停/崩溃后续跑沿用剩余额度，不重置）。

        恢复已用量与活动时钟；未决预占按未知结果转为已用，避免重启双花。
        limit 来自当前预设（预设表升级不改历史消耗）。
        baseline_used：迁移时显式传入的 legacy_usage（快照内已有的 baseline 优先），
        抬高各资源的计数基线，使 enforce 只约束迁移后的新增消耗。
        """
        budget = cls(preset, enforce=enforce, clock=clock)
        state = state or {}
        recovered_reservations: list[dict[str, float | str]] = []
        snapshot_baseline = state.get("baseline_used") or {}
        merged_baseline = dict(baseline_used or {})
        for name, value in snapshot_baseline.items():
            if name in budget._resources:
                merged_baseline[name] = max(0.0, float(value or 0.0))
        for name, row in budget._resources.items():
            if name in merged_baseline:
                # baseline 不能超过当前 used（否则出现负计数）；迁移路径由调用方
                # 保证 baseline <= used；restore 自身做钳制防脏快照。
                row["baseline"] = min(max(0.0, float(merged_baseline[name])),
                                      max(0.0, float((state.get("resources") or {}).get(name, {}).get("used") or 0.0)))
        for name, row in (state.get("resources") or {}).items():
            if name in budget._resources:
                used = max(0.0, float(row.get("used") or 0.0))
                reserved = max(0.0, float(row.get("reserved") or 0.0))
                # 进程崩溃后无法确认快照里的预占是否已发送。恢复时按上界转为
                # 已用，避免重启把同一额度再花一次；新进程不保留悬空 reservation。
                budget._resources[name]["used"] = used + reserved
                if reserved:
                    recovered_reservations.append({"resource": name, "amount": reserved})
        budget._active_seconds = max(0.0, float(state.get("active_seconds") or 0.0))
        budget.exhaustion_log = [dict(row) for row in state.get("exhaustion_log") or []]
        budget.usage_unknown_attempts = max(
            0, int(state.get("usage_unknown_attempts") or 0))
        if recovered_reservations:
            recovered_attempts = int(next(
                (row["amount"] for row in recovered_reservations
                 if row["resource"] == "model_attempts"), 0))
            budget.usage_unknown_attempts += max(0, recovered_attempts)
            budget.exhaustion_log.append({
                "resource": "recovered_reservations",
                "items": recovered_reservations,
                "at_active_seconds": round(budget._active_seconds, 3),
            })
        return budget

    # ── 时间 ────────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        if self._resumed_at is not None and not self._paused:
            self._active_seconds += self._clock() - self._resumed_at
            self._resumed_at = self._clock()

    def pause(self) -> None:
        """暂停停走活动时钟；额度保持剩余状态，恢复不重置任何计数。"""
        with self._lock:
            self._tick()
            self._resumed_at = None
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            if self._paused:
                self._paused = False
                self._resumed_at = self._clock()

    @property
    def paused(self) -> bool:
        return self._paused

    def active_seconds(self) -> float:
        with self._lock:
            self._tick()
            return self._active_seconds

    def delivery_seconds_left(self) -> float:
        """交付侧剩余时间（含交付预留）。"""
        return max(0.0, self._total_active_seconds - self.active_seconds())

    def research_seconds_left(self) -> float:
        """研究侧剩余时间 = 总时间 − 交付预留 − 已活动时间。

        降到 0 即停止新增研究（冻结已有成果），交付/核验继续用交付侧余量。
        """
        return max(0.0, self._total_active_seconds - self._delivery_reserve_seconds
                   - self.active_seconds())

    def research_time_exceeded(self) -> bool:
        return self.research_seconds_left() <= 0

    def deadline_after(self, seconds_left: float) -> float:
        """把剩余时间转成单调时钟绝对 deadline（传给 LLM Core 钳制退避）。"""
        return self._clock() + max(0.0, seconds_left)

    # ── 额度：预占 / 结算 / 退款 ────────────────────────────────────────────

    @staticmethod
    def _counted(row: dict[str, float]) -> float:
        """计入限额的消耗 = 已用 − 迁移基线 + 未决预占。"""
        return max(0.0, row["used"] - row["baseline"]) + row["reserved"]

    def remaining(self, resource: str) -> float:
        with self._lock:
            row = self._resources[resource]
            return max(0.0, row["limit"] - self._counted(row))

    def reserve(self, resource: str, amount: float = 1.0) -> bool:
        """原子预占：余额不足返回 False（并记一次耗尽事件）。

        记账模式（enforce=False）永远成功——只累计用量、不阻断业务；
        超限仍会记耗尽事件，供观测"若强制会在哪里停"。
        """
        amount = max(0.0, float(amount))
        with self._lock:
            self._tick()
            row = self._resources[resource]
            if self.enforce and self._counted(row) + amount > row["limit"] + 1e-9:
                self._log_exhaustion_locked(resource)
                return False
            row["reserved"] += amount
            if self._counted(row) > row["limit"] + 1e-9:
                self._log_exhaustion_locked(resource)
            return True

    def settle(self, resource: str, reserved_amount: float, actual: float) -> None:
        """按实际用量结算并退还差额；实际超出估算时不回拨（保守扣账）。"""
        with self._lock:
            self._tick()
            row = self._resources[resource]
            row["reserved"] = max(0.0, row["reserved"] - max(0.0, float(reserved_amount)))
            row["used"] += max(0.0, float(actual))
            if self._counted(row) > row["limit"] + 1e-9:
                self._log_exhaustion_locked(resource)

    def refund(self, resource: str, reserved_amount: float) -> None:
        self.settle(resource, reserved_amount, 0.0)

    def _log_exhaustion_locked(self, resource: str) -> None:
        if not any(row["resource"] == resource for row in self.exhaustion_log):
            row = self._resources[resource]
            self.exhaustion_log.append({
                "resource": resource,
                "limit": row["limit"], "used": row["used"], "reserved": row["reserved"],
                "baseline": row["baseline"],
                "at_active_seconds": round(self._active_seconds, 3),
            })

    # ── LLM Core 钩子（经 set_llm_budget 注入；方法缺失时零开销）────────────

    def before_model_attempt(self, estimated_output_tokens: float = 0.0,
                             estimated_input_tokens: float = 0.0) -> None:
        """原子预占一次模型尝试及其估算输入/输出额度。

        output_tokens 是软限制（v7.5）：超限不抛 BudgetExhausted，仅记账并
        记耗尽事件，由 llm_output_hint() 的末尾提示词催促模型节约输出；
        其余资源仍硬停。
        """
        amounts = {
            "model_attempts": 1.0,
            "input_tokens": max(0.0, float(estimated_input_tokens)),
            "output_tokens": max(0.0, float(estimated_output_tokens)),
        }
        with self._lock:
            self._tick()
            if self.enforce:
                for resource, amount in amounts.items():
                    row = self._resources[resource]
                    if self._counted(row) + amount > row["limit"] + 1e-9:
                        self._log_exhaustion_locked(resource)
                        if resource == "output_tokens":
                            continue
                        remaining = max(0.0, row["limit"] - self._counted(row))
                        raise BudgetExhausted(resource, remaining)
            for resource, amount in amounts.items():
                row = self._resources[resource]
                row["reserved"] += amount
                if self._counted(row) > row["limit"] + 1e-9:
                    self._log_exhaustion_locked(resource)

    def after_model_attempt(self, *, usage: dict | None = None,
                            reserved_output_tokens: float = 0.0,
                            reserved_input_tokens: float = 0.0,
                            usage_known: bool = True) -> None:
        """结算已发送尝试；未知 usage 按预估上界保守扣账。"""
        usage = usage or {}
        if usage_known:
            actual_output = float(usage.get("output_tokens") or 0)
            actual_input = float(usage.get("input_tokens") or 0)
        else:
            actual_output = max(0.0, float(reserved_output_tokens))
            actual_input = max(0.0, float(reserved_input_tokens))
            with self._lock:
                self.usage_unknown_attempts += 1
        self.settle("model_attempts", 1, 1)
        self.settle("output_tokens", reserved_output_tokens, actual_output)
        self.settle("input_tokens", reserved_input_tokens, actual_input)

    def cancel_model_attempt(self, *, reserved_output_tokens: float = 0.0,
                             reserved_input_tokens: float = 0.0) -> None:
        """撤销尚未发送的尝试；排队取消或 deadline 不消耗供应商额度。"""
        self.refund("model_attempts", 1)
        self.refund("output_tokens", reserved_output_tokens)
        self.refund("input_tokens", reserved_input_tokens)

    def llm_output_hint(self) -> str | None:
        """output_tokens 软限制：按已用比例返回追加到消息末尾的催促文案。

        仅 enforce 模式生效（legacy 记账模式行为不变）；未达阈值返回 None。
        LLM Core 以 duck-typing 读取（缺方法零开销）；文案必须拼在消息末尾，
        不改 system 与正文前缀，避免击穿供应商前缀缓存。
        """
        if not self.enforce:
            return None
        with self._lock:
            self._tick()
            row = self._resources["output_tokens"]
            limit = row["limit"]
            if limit <= 0:
                return None
            ratio = self._counted(row) / limit
        for threshold, text in _OUTPUT_SOFT_HINTS:
            if ratio >= threshold:
                return text
        return None

    # ── 观测 ────────────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._tick()
            resources = {
                name: {"limit": row["limit"], "used": round(row["used"], 1),
                       "reserved": round(row["reserved"], 1),
                       "baseline": round(row["baseline"], 1),
                       "counted_used": round(max(0.0, row["used"] - row["baseline"]), 1),
                       "remaining": round(max(0.0, row["limit"] - self._counted(row)), 1)}
                for name, row in self._resources.items()
            }
            return {
                "enforce": self.enforce,
                "paused": self._paused,
                "active_seconds": round(self._active_seconds, 1),
                "total_active_seconds": self._total_active_seconds,
                "delivery_reserve_seconds": self._delivery_reserve_seconds,
                "research_seconds_left": round(max(
                    0.0, self._total_active_seconds - self._delivery_reserve_seconds
                    - self._active_seconds), 1),
                "delivery_seconds_left": round(max(
                    0.0, self._total_active_seconds - self._active_seconds), 1),
                "resources": resources,
                "baseline_used": {name: round(row["baseline"], 1)
                                  for name, row in self._resources.items()
                                  if row["baseline"] > 0.0},
                "usage_unknown_attempts": self.usage_unknown_attempts,
                "exhaustion_log": list(self.exhaustion_log),
            }
