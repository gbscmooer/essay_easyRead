"""Per-call LLM token usage records for cross-business consumption stats."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


class LLMUsageRecord(SQLModel, table=True):
    """一次 LLM 调用尝试的归一化用量，按业务类目/阶段/运行/实例/操作归因。

    成功调用（status=ok）携带供应商 usage；失败尝试（status=error）token 全 0、
    不计耗时，只用于统计尝试次数与失败率（计划 §16.5：失败调用计数可见）。
    """

    __tablename__ = "llm_usage_records"

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), index=True,
    )
    category: str = Field(default="other", index=True)
    stage: str = ""
    run_id: str | None = Field(default=None, index=True)
    # 运行实例归属（P1 §16.5）：精确匹配 ExplorationRun.run_instance_id；
    # 空串 = 身份列启用前的旧记录，只能按调用方传入的时间下限兜底隔离。
    run_instance_id: str = ""
    # 操作归属：research（首次/续跑）| report_regenerate | chat；空 = 未标注
    operation: str = ""
    # ok = 成功；error = 失败尝试（重试中的每次失败各记一行）
    status: str = "ok"
    # 失败异常类别，只存类名（异常文本可能携带 URL/密钥，不落库）
    error_code: str = ""
    model: str = ""
    api_format: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    # 该次成功请求的墙钟耗时（不含重试等待）；0 表示未计时（旧记录/失败尝试）
    elapsed_ms: int = 0
