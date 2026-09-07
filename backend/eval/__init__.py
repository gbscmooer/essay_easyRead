"""离线评测工具集。

- explore_eval：领域探索（/explore）的判定质量与证据可核验性评测
  （系统 vs 仅凭记忆的裸 LLM 基线），见 docs/DOMAIN_ONBOARDING_AI_WORKFLOW_PLAN.md v1.5。

原则（沿用 docs/DEEP_RESEARCH_EVAL.md）：原始运行结果落 data/eval/（gitignore，
不进仓库）；预期判定是人工标注草案，正式出数前须用户/导师确认；CI 只跑桩测试，
真实评测显式执行。
"""
