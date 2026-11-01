"""循环内的模型调用：统一走 LLM Core，按调用类型给定输出上限与思考开关。

窄决策（检索式、判定串、章节名）一律关闭思考、限制输出长度——PaSa 的 Selector 只生成一个 token，
这里做不到取 logprob，就让模型按批只输出判定串，输出量同样在每篇 1~2 个 token 的量级。
"""

from __future__ import annotations

from typing import Awaitable, Callable

from ... import llm

LlmJson = Callable[[str, str], Awaitable[dict]]


def json_caller(cfg: dict, *, max_tokens: int, thinking: str | None = "off",
                temperature: float = 0.1) -> LlmJson:
    """返回 llm_json(system, user) -> dict；thinking=None 时沿用全局配置的思考强度。"""

    async def call(system: str, user: str) -> dict:
        data = await llm.complete_json(
            cfg, system=system, user=user, max_tokens=max_tokens, temperature=temperature,
            thinking_effort=cfg.get("thinking_effort") if thinking is None else thinking)
        return data if isinstance(data, dict) else {}

    return call


def configured(cfg: dict | None) -> bool:
    return bool(cfg and cfg.get("api_key"))
