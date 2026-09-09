"""Selected-text translation and summarization business service."""

from __future__ import annotations

from .ai_config import get_llm_config, get_translate_config
from .llm import complete_text

TRANSLATE_SYSTEM = (
    "你是一个专业的学术翻译助手。将用户提供的英文论文片段翻译为中文。"
    "只输出翻译结果，不要添加任何解释。保持学术术语的准确性。"
)
SUMMARY_SYSTEM = (
    "你是一个专业的学术论文分析助手。用中文简洁地总结用户提供的论文片段的核心要点。"
    "用 2-3 个要点概括，每个要点一行，以 • 开头。"
)


async def run_text_action(text: str, action: str) -> str:
    """Run a text action through the single global LLM provider."""

    cfg = get_llm_config()
    if action == "translate":
        # system_prompt remains a task prompt only; credentials/model come from llm.
        system = get_translate_config().get("system_prompt") or TRANSLATE_SYSTEM
    elif action == "summarize":
        system = SUMMARY_SYSTEM
    else:
        raise ValueError("action 必须是 translate 或 summarize")
    return await complete_text(
        cfg,
        system=system,
        user=text,
        max_tokens=min(int(cfg.get("max_tokens", 2048)), 2048),
        thinking_effort=cfg.get("thinking_effort", "off"),
    )
