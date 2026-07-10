# Deep Research 发现评测（P0–P9）

离线评测 + mock 回归门禁。详见 `docs/DEEP_RESEARCH_EVAL.md`。

## 快速开始

```bash
export EVAL_ALLOW_NETWORK=0

python -m backend.tests.evaluation.deep_research.eval.cli run --mode mock
python -m backend.tests.evaluation.deep_research.eval.cli gate --mode mock
python -m backend.tests.evaluation.deep_research.eval.cli compare \
  backend/tests/evaluation/deep_research/baselines/mock_v1.json \
  backend/tests/evaluation/deep_research/reports/mock_gate_report.json

pytest backend/tests/test_deep_research_eval.py -q
```

## Runners

| Suite | Runner | 说明 |
|-------|--------|------|
| discovery | `keyword` | 标题关键词重叠 |
| discovery | `s2_search` | mock S2 搜索回放 |
| discovery | `current_expand` | v1 单源 citation 排序 |
| discovery | `discovery_v2` | mock 多源 RRF 融合 |
| rag | `rag_vector` | dense only |
| rag | `rag_vector_rerank` | dense + mock rerank |
| rag | `rag_hybrid` | dense + BM25 + graph RRF + rerank |

## 目录

```
deep_research/
  eval_config.yaml
  thresholds.yaml          # mock（CI）+ release（信息性）门槛
  datasets/discovery/
  datasets/rag/
  fixtures/mock/
  baselines/mock_v1.json  # 版本控制内冻结的聚合基线
  eval/                    # harness, gate, cli, runners, metrics
  reports/                 # gitignore
```
