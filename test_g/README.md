# test_g — 图谱算法独立实验场

专门用来打磨"深度研究"的图谱算法，**不依赖 FastAPI / SSE / DB / 网络**，避免其他系统干扰。
算法核心在 `backend/services/deep_research/graphkit/`（纯净、可注入、零系统依赖），
本目录只负责：喂数据 → 跑算法 → 看结果 → 调参。

## 为什么能独立跑

`graphkit` 不 import DB / config.settings / embedder，向量化能力靠**注入** `embed_fn`。
所以这里用 `fake_embedder.py`（确定性、离线、零依赖）就能跑通全流程，结果可复现。
想用真模型时，把 `embed_fn` 换成真实 embedder 即可。

## 用法

```bash
# 在仓库根目录运行（确定性，无需 MIMO_API_KEY / 无需联网）
.venv/bin/python -m test_g.run                      # 用内置 fixtures
.venv/bin/python -m test_g.run --fixture test_g/fixtures/sample.json

# 从真实工作区导出数据成 fixture（这一步会连 DB，是"系统↔算法"的桥）
.venv/bin/python -m test_g.dump_workspace --ws 1 --out test_g/fixtures/ws1.json
```

## 文件

- `fake_embedder.py` —— 确定性假向量（词袋哈希），cosine ≈ 词重叠，用于离线复现。
- `fixtures/sample.json` —— 手写的几篇 PaperFeatures，覆盖"同目的不互引""同数据集"等情形。
- `run.py` —— 加载 fixture → FacetEngine 算多维关系 → 建图 → 跑 PageRank/Louvain → 打印。
- `dump_workspace.py` —— 把真实工作区（DB + AnalysisResult + 引用边）导成 PaperFeatures JSON。

## 开发节奏（与 docs/DEEP_RESEARCH_REDESIGN.md 对齐）

1. 在这里试"每维独立成边"（`graphkit/graph.py:faceted_edges`）、剪枝、有向 PageRank 等；
2. 用 fixtures 或真实工作区导出物做对比，挑出好算法；
3. 满意后，让生产侧 `similarity/facet_weighted.py` / `graph_builder.py` 委托给同一份 graphkit。
