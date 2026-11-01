"""研究评测工具集。

分环节（PaSa 式，便宜、可反复跑）：
  public_benchmarks synergy    Selector 筛选的查全/查准（人工纳入标签）
  public_benchmarks litsearch  ResearchLoop 检索 + 判定的召回（专家标注目标论文）
  researchqa recall            同一批检索式下循环的召回（细则所引文献是否被选中 / 是否进过候选池）
端到端（贵、噪声大，只做最终验收）：
  scholarqa_cs run|agent       ScholarQA-CS 细则评分
  researchqa run               ResearchQA 覆盖评分
system_arm 在临时库上完整跑一次研究；ledger 记录每次评测的出处与配对比较。原始输出在 data/eval/。
"""
