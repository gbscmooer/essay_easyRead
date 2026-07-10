# Deep Research 评测与回归门禁（P9）

离线评测框架，覆盖 **发现（discovery）** 与 **RAG 消融**，用于 CI mock 回归门禁。  
**不**自动抓取 Google/Scholar；**不**用 dev 标注集当最终 test 调参；**不**在 mock 达标时宣称真实发布合格。

## 评测范围

| Suite | Runners | 说明 |
|-------|---------|------|
| discovery | `keyword`, `s2_search`, `current_expand`, `discovery_v2` | 论文发现/扩展排序 |
| rag | `rag_vector`, `rag_vector_rerank`, `rag_hybrid` | 证据检索消融（mock chunk 分数） |

## 指标

| 指标 | 模块 | 说明 |
|------|------|------|
| Precision@K | `eval/metrics/ir.py` | Top-K 中相关项占比 |
| Recall@K | `eval/metrics/ir.py` | Top-K 覆盖多少 ground-truth |
| nDCG@K | `eval/metrics/ir.py` | 排序质量 |
| MRR | `eval/metrics/ir.py` | 首个相关项倒数排名 |
| Wrong-ID Rate | `eval/metrics/identity.py` | 预测 ID 与 canonical 不一致比例（discovery） |
| Duplicate Rate | `eval/metrics/identity.py` | 排序列表重复占比 |

## Mock 回归数据（非发布 test split）

- **Discovery**：`datasets/discovery/seeds.jsonl`（5 seeds）+ `corpus.jsonl`（22 papers）
- **RAG**：`datasets/rag/queries.jsonl`（5 queries，chunk 级 relevant 标注）
- **Fixtures**：`fixtures/mock/` — S2、expand、discovery_v2 多源池、RAG chunk 池

全部为人工构造/录制，**冻结**后仅用于回归，不作为 live 调参集，也不满足发布验收所需的真实 test split。
冻结聚合基线位于 `baselines/mock_v1.json`，CI 生成的新报告可通过 `compare` 命令与之比较。

## 门槛（thresholds.yaml）

两套门槛，务必区分：

### `modes.mock` — CI 强制执行

- 各 runner 绝对指标下限（基于当前 mock 基线略留余量）
- **回归约束**：
  - `discovery_v2` vs `current_expand`：`precision@5` Δ≥0.10，`recall@5` Δ≥0.20
  - `rag_hybrid` vs `rag_vector`：`recall@5` Δ≥0.15，`mrr` Δ≥0.30

### `modes.release` — 发布目标（信息性，CI 不强制）

- 更高绝对指标（discovery_v2 / rag_hybrid）
- 需配合 **人工 Scholar 盲测** 才能宣称“明显优于 Scholar”

## 人工 Scholar 盲测流程（发布前）

> **禁止**自动爬取 Scholar/Google 结果作为 test 集或调参依据。

1. **准备 seeds**：从真实研究工作区选至少 30 篇种子论文，覆盖至少 3 个 CV/ML 子领域，记录 title/year/DOI；dev 与最终 test 必须隔离。
2. **人工收集对照**：两位标注者独立在 Scholar/Semantic Scholar UI 搜索相同 seed，各记录 Top-10 相关论文（标题+DOI），合并为 blind ground-truth（分歧第三方裁决），标注一致性要求 κ≥0.70。
3. **双盲运行**：同一批 seed 分别跑 `discovery_v2`（live，`EVAL_ALLOW_NETWORK=1`）与整理好的 Scholar 列表，标注者不知来源。
4. **判定**：按 Precision@10 / Recall@20 / nDCG@10 与主观有用性投票；系统平均 nDCG@10 至少比固定查询 Scholar 对照高 0.05、至少 60% 查询胜出，且 wrong-id-rate ≤1%，方可讨论默认开启。
5. **归档**：结果 JSON 存私有目录（不进 git），仅摘要写入发布说明。

## 命令

从仓库根目录：

```bash
export EVAL_ALLOW_NETWORK=0

# 生成 mock 报告
python -m backend.tests.evaluation.deep_research.eval.cli run --mode mock

# 回归门禁（无报告则先 run）
python -m backend.tests.evaluation.deep_research.eval.cli gate --mode mock

# 与版本库中的冻结基线生成差异报告
python -m backend.tests.evaluation.deep_research.eval.cli compare \
  backend/tests/evaluation/deep_research/baselines/mock_v1.json \
  backend/tests/evaluation/deep_research/reports/mock_gate_report.json

# pytest
pytest backend/tests/test_deep_research_eval.py -q
```

报告默认：`backend/tests/evaluation/deep_research/reports/mock_gate_report.json`（gitignore）。

## discovery_v2 生产开关

`ai_config.json` → `research.discovery_v2`（默认 `false`）。在真实发布 test split 与人工 Scholar 盲测完成前只作为实验功能显式开启。  
`false` 时 `stage_expand` 回退 v1：`backward` 单源 + `pdf_url` 过滤 + `citation_count` 排序。

## CI

`.github/workflows/ci.yml` 的 `eval-fast` job：

```bash
EVAL_ALLOW_NETWORK=0 python -m backend.tests.evaluation.deep_research.eval.cli run --mode mock
EVAL_ALLOW_NETWORK=0 python -m backend.tests.evaluation.deep_research.eval.cli gate --mode mock
EVAL_ALLOW_NETWORK=0 python -m backend.tests.evaluation.deep_research.eval.cli compare \
  backend/tests/evaluation/deep_research/baselines/mock_v1.json \
  backend/tests/evaluation/deep_research/reports/mock_gate_report.json
```

当前仓库只完成 mock 门禁；真实 30+ seed、双人标注与 Scholar 盲测尚未归档，因此产品默认不开启 `discovery_v2`，也不宣称优于 Scholar。

## 与论文精读评测的关系

`backend/tests/evaluation/evaluate.py` 评估论文精读分析质量；本目录评估深度研究发现与 RAG 检索质量。两者独立。
