"""研究评测工具集，入口与限制见 docs/DEEP_RESEARCH_EVAL.md。

explore_eval 会在线运行当前系统并与仅凭模型记忆的基线比较，不是纯离线工具。
survey_metrics 计算显式标注的离线指标。原始输出保存在 data/eval/；预期判定
为人工草案，正式出数前校核。新 workflow 对照协议见
 docs/workflow_refactor/VALIDATION.md；现有 CI 桩不代表真实研究质量。
"""
