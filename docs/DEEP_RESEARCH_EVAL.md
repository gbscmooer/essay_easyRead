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

## 真实数据评测（零 mock）

`backend/tests/evaluation/deep_research/real_graph_eval.py` 只读真实数据库（ResearchGraph +
PaperReference），不用任何 fixture、不联网、不调 LLM/embedding，用于验证**图检索通道本身**的有效性
（mock 门禁只验证 RRF 管道正确性，不构成有效性证据）：

- **结构指标**：重复节点、dangling 占比、连通分量、边类型分布
- **通道行为**：PPR 质量困在 seed 的比例、Top-10 一跳邻居占比、跨 seed 组 Top-10 Jaccard
  （霸榜稳定性）、PPR 与入度的 Spearman 相关（区分拓扑信号 vs 单纯数边）
- **held-out 引用恢复**（核心）：随机遮蔽 20% 已解析 cites 对，用 citing 论文做 seed 跑 PPR，
  测被遮蔽 cited 论文的 Recall@5/10/20 与 MRR；三臂对比 旧配置 PPR / 新配置 PPR（反向边 +
  排除 seed）/ 入度基线。bibcoupling / cocitation 在遮蔽后引用集上用生产函数重算，杜绝泄漏。

```bash
python -m backend.tests.evaluation.deep_research.real_graph_eval --ws 1 \
    --tag post_fix --out backend/tests/evaluation/deep_research/real_reports/ws1_post_fix.json
```

`real_reports/` 下的报告作为证据提交进版本库（与 gitignore 的 mock `reports/` 区分）。
老工作区 PaperReference 为空时，先在研究页点「修复引用数据」（或调
`POST /api/research/workspaces/{id}/backfill-references`）回填真实引用。

## 关联度 / 地图质量评测（零 mock，v3.1 新增）

`real_graph_eval.py` 评的是**图检索**；`relation_quality_eval.py` 评的是**图本身**——
关联度打分准不准、权重该怎么定、骨架该留多密。弱真值不需要人工标注：

> 工作区内的真实引用对 = "这两篇确实相关"（引文推荐文献里的标准弱标签）

```bash
python -m backend.tests.evaluation.deep_research.relation_quality_eval --ws 1 \
    --out backend/tests/evaluation/deep_research/real_reports/ws1_relation_quality.json
```

首跑需要 embedding API（purpose/method 通道），通道矩阵会缓存到 `--cache` 指定的文件，
之后可完全离线复跑。四张表：通道判别力 / 权重口径对比（FIT-TEST 切分）/ 稀疏化方法对比
（同边预算）/ 密度工作点扫描。

### ws1 实测结论（179 篇，573 对工作区内真实引用，2026-08-03）

**A. 通道判别力差异极大，而且和先验权重不匹配**

| 通道 | AUC | 先验权重 | |
|------|-----|---------|---|
| topic | 0.888 | 0.18 | 判别力最强，权重最低 |
| term | 0.797 | 0.14 | |
| purpose | 0.643 | 0.24 | 权重最高，判别力中等 |
| method | 0.571 | 0.16 | **≈随机** |
| dataset / baseline | 0.596 / 0.537 | 0.06 / 0.04 | 覆盖率低，纯加分 |
| bibcoupling / cocitation | 0.827 / 0.703 | 0.12 / 0.06 | 监督来源，AUC 有泄漏 |

**B. 自监督校准（引用对 50/50 切分，只在 FIT 半边拟合）**

| 权重口径 | held-out AUC | 语义通道权重 |
|---------|-------------|------------|
| 先验（v3.0 默认） | 0.8515 | topic .18 / purpose .24 / method .16 / term .14 |
| **自监督校准** | **0.8922** | topic .30 / purpose .13 / method .07 / term .22 |
| 只用 topic | 0.8941 | — |

校准逼近该工作区的单通道上界，且是**自适应**得来的：换一份"一个大方向下含多个细分方向"
的语料（`tests/test_relation_quality.py` 合成对照），同一机制会把权重拟合成四通道均衡，
而只用 topic 的口径在那里骨架纯度只有 0.47。

**C. 稀疏化方法对比（同边预算，均用校准后权重）**

| 方法 | 边数 | 平均度 | 引用对召回 | 单边命中提升 | modularity | 连通块 | 孤点 |
|------|-----|-------|----------|------------|-----------|-------|-----|
| 全局阈值 | 716 | 8.0 | 49.7% | 11.1x | 0.617 | 21 | 18 |
| 互近邻 mutual-kNN(k=4) | 183 | 2.0 | 16.8% | 14.6x | 0.887 | 39 | 24 |
| **多尺度骨架 @deg8** | 715 | 8.0 | **50.8%** | **11.3x** | **0.636** | **1** | **0** |

同样边预算下骨架与全局阈值精度相当，但地图**只有一个连通块、零孤立点**——全局阈值留下
18 个谁也连不上的节点，是"看不出重点"的另一种形态。

**D. 密度工作点（生产默认 target_degree=8）**

| target_degree | 边数 | 引用对召回 | 单边命中提升 | modularity | 子方向 |
|--------------|-----|----------|------------|-----------|-------|
| 4 | 411 | 36.8% | 14.3x | 0.744 | 12 |
| **8（默认）** | 715 | 50.8% | 11.3x | 0.636 | 9 |
| 12 | 1096 | 61.4% | 8.9x | 0.554 | 7 |
| 40（≈v3.0 实际密度） | 3575 | 83.6% | 3.7x | 0.313 | 5 |

**E. 稀疏化不伤图检索**（`ppr_arms` 协议：遮蔽 20% 引用，related 层只用语义通道以杜绝泄漏）

| 地图密度 | held-out 引用恢复 R@20 |
|---------|---------------------|
| 4 | 0.678 |
| 8 | 0.670 |
| 20 | 0.722 |
| 42 | 0.635 |

差异在 115 条样本的噪声范围内：**地图稀疏 5 倍不损失图检索能力**。同一协议下还比较了 PPR
该走哪些边层（cites+related+bibcoupling+cocitation vs 只走 cites+related），R@20 0.670 vs
0.661，同样在噪声内——因此**没有**改动 `graph_retriever` 的边层默认值（理论上 bibcoupling
的分值已计入 related 边存在重复计数，但实测无收益，不做无证据的改动）。

## 与论文精读评测的关系

`backend/tests/evaluation/evaluate.py` 评估论文精读分析质量；本目录评估深度研究发现与 RAG 检索质量。两者独立。

## 盲测校准：以 RAG Survey (2312.10997v5) 为标准答案（2026-09-08）

> 校正说明（同日复核）：本节下方旧数值保留为历史记录，不作为当前评测结论。旧临时脚本把相似医疗综述误判为泄漏，数据集/指标/框架名单未严格对应原表。实际 Table I 有71个方法条目，Table II按表内名称计48个数据集条目（Amazon三个领域合计一个），Table III有11类指标，Table IV有6个框架。当前脚本为 `backend/eval/survey_holdout.py`，不再使用 `/tmp/compare_rag_survey.py`。

方法：让系统对与该综述相同的研究方向独立跑完整研究（问题、深度=deep），把综述本身从语料
整链路排除（`exclude_dois`/`exclude_arxiv_ids` 输入快照，`explorer.apply_exclusions` 在召回
合并后过滤，计量/综合/报告全文下载均不接触），再对比系统调查报告与综述的 Table I–IV。

### 实验发现（驱动的工作流修复）

| # | 发现 | 修复 | 效果 |
|---|------|------|------|
| 1 | 单一 relevance 排序召回**天然偏新**：LLM 拆解式含「LLM」等 2023 后流行词，奠基论文（Lewis 2020、FLARE 等）标题摘要不含这些词，AND 全词直接不匹配——218 篇语料 2023 前仅 1 篇，Table I 核心 107 篇覆盖 0% | 双通道召回：每条检索式 relevance 主路 + 前 3 词**引号短语**经典路（`order="cited"` 被引降序；Scopus/OpenAlex 均支持） | 经典层 0 → 5 篇（Lewis 2020 居首），核心覆盖 0% → 8%；引号短语消除截短式跨域噪声（rags/增强现实/全息检索实测案例） |
| 2 | 报告证据提取 25/40 失败（DeepSeek APIConnectionError/Timeout 无重试） | 瞬时故障错峰退避重试一次（`survey_report.py` 提取循环） | 提取成功 15 → 24/40，指标覆盖 8% → 46% |
| 3 | 报告全文获取 0/12：Scopus 记录基本不带 `pdf_url`/`arxiv_id` | `full_text()` 接入 `resolve_candidate_pdf_urls` OA 级联（Unpaywall/EuropePMC/OpenAlex/机构直链） | 0 → 2/12（隔离验证）；语料偏 IEEE/ACM/MDPI 正式发表版本时受付费墙限制 |

### 残留差距与后续方向（记录，未实施）

- **数据源结构性偏差**：Scopus 主源的语料偏正式发表（付费）版本，综述核心方法论文多在
  arXiv；评估框架（RAGAS/TruLens/ARES/RGB）为工具型 arXiv 论文，Scopus 覆盖弱。后续可做
  「经典路走 OpenAlex、relevance 路走 Scopus」的多源分工。
- **数据集覆盖仍为 0%**：数据集名多出现在实验节正文，摘要级证据天然缺失；全文获取率
  （发现 3）是提升上限的钥匙。
- 提取超时（80s）对长输出仍偏紧，失败 13/16 为 TimeoutError；可按 depth 放宽或换更快端点。
- 本实验为单题校准（n=1），数值不外推为普遍性能；复现脚本思路见本文档与
  `/tmp/compare_rag_survey.py`（标题归一化含 NFKC + 跨行断词去除后匹配）。

### 可复现对比（2026-09-08修订）

问题固定为「检索增强生成（RAG）在大语言模型中的主要技术路线、评估框架、常用指标与数据集有哪些？」；deep深度、无母论文、联网检索。目标PDF仅由离线评分程序读取，不作为系统输入。run 23为已有基线；run 24为本轮改进后同题运行。

```bash
.venv/bin/python -m backend.eval.survey_holdout --pdf /Users/katisarrow/Downloads/2312.10997v5.pdf --runs 23 24 --output data/eval/rag-holdout-20260908
```

产物：`reference.json`（含PDF SHA256和页码）、`run-N.json`、`report-N.html/md`、`comparison.json`。评分只读现有运行，不发送LLM或论文下载请求。

基线run 23：184篇召回，24/40篇证据提取成功、0篇全文；Table I对应文献命中6/71，数据集名0/48，指标名6/11，评估框架名0/6，精确身份与标题检查未发现目标论文。150篇发表于2024年之后，说明材料明显偏新；新论文不能替代对旧基础方法的覆盖。

解释范围：文献匹配使用精确规范标题或arXiv身份，未做前缀模糊匹配；实体命中只查看带证据表格的对应名称字段。原表的拼写错误/明确缩写有少量显式别名。命中衡量覆盖，不证明指标适用关系、任务映射或论断正确；不是完整系统综述评测，也不外推到其他题目。2024年后的资料允许进入研究并单独计数，未宣称严格时间冻结实验。
