# document

## 1. 架构

### 1.1 后端

- `FastAPI` 提供 HTTP API、SSE、WebSocket。
- `SQLModel` 管理数据模型与 CRUD。
- `services/` 负责业务逻辑，`routers/` 只做路由转发。
- `SQLite` 为默认开发数据库，文件位于 `./data/essay_read.db`。

### 1.2 前端

- `React + TypeScript + Vite`
- `api/` 负责所有请求封装。
- `features/` 负责领域组件。
- `pages/` 负责路由级页面。

## 2. 数据流

### 2.1 论文主链路

1. 上传 PDF 到 `/api/papers/upload`
2. 后端落盘并创建 `Paper`
3. `auto_index` 提取全文与元数据
4. 前端在 `/reader/:id` 触发论文解析
5. `analysis_runner` 做流式解析、相关论文搜索、智能标签
6. 结果写入 `AnalysisResult`，解析状态写入 `Paper`

### 2.2 深度研究链路

1. 工作区聚合论文
2. `/api/research/workspaces/{ws_id}/runs` 创建研究任务
3. pipeline 依次执行 `collect -> expand -> chunk -> embed -> digest -> graph -> persist`；`expand` 内部并行执行在线多源召回与 PDF 引用解析，`digest` 可按配置跳过
4. `/api/research/workspaces/{ws_id}/graph` 返回图谱快照
5. `/api/research/workspaces/{ws_id}/query` 提供 RAG 问答；`mode=graph` 时叠加 PathRAG 风格图谱路径证据
6. `/api/research/runs/{run_id}/stream` 订阅进程内事件总线；15 秒无事件时发送 SSE 注释心跳；已完成任务回放 `graph_ready/done`；数据库残留但当前进程无事件总线的任务返回 `error` 事件

### 2.3 工作区洞察链路

1. `/api/workspaces/{workspace_id}/insights` 读取工作区论文与最新图谱
2. 统计阅读层级、来源、聚类、关键论文、桥梁论文
3. 前端工作区详情页展示阅读建议与图谱摘要

### 2.4 工作区智能规划链路

1. `/api/workspaces/{workspace_id}/reading-plan` 读取工作区论文画像与最新图谱
2. 将引用边转换为阅读前置关系，结合年份、解读层级、图谱重要性分阶段排序
3. `/api/workspaces/{workspace_id}/survey` 读取论文画像生成综述草稿、主题聚合与对比矩阵
4. 前端工作区详情页展示阅读路径、覆盖统计、综述片段和矩阵

### 2.5 Agent 工具链路

1. MCP 客户端通过 `python -m backend.mcp_server` 启动 stdio 服务
2. MCP `tools/list` 返回论文检索、证据检索、图谱读取、开发 brief 工具
3. 工具调用进入 `services/agent_tools.py`
4. `agent_tools` 只读取 `Paper`、`AnalysisResult`、`ResearchGraph`、向量索引和工作区关联
5. HTTP 客户端可通过 `/api/agent-tools/*` 使用同一组工具
6. `/api/agent-tools/workflow/langgraph-spec` 返回论文驱动开发工作流规范

### 2.6 Research Agent Harness 链路

1. 客户端通过 `/api/agent-tools/runs` 或 `python -m backend.research_agent run` 提交 `workflow/goal/workspace_id`
2. `parse_goal` 默认使用确定性计划；设置 `RESEARCH_AGENT_PLANNER_ENABLED=true` 后通过统一 LLM 配置执行一次有 token/超时上限的结构化 goal 规划，失败自动回退原始 goal
3. `services/research_harness.py` 用 LangGraph 编排固定 workflow，并通过 `services/agent_tools.py` 调用论文检索、证据检索、阅读计划和综述工具
4. 每个节点记录结构化 journal；goal planner 与可选证据检索失败按 `soft_fail` 处理，其余领域错误终止 workflow
5. 成功运行返回 Markdown、goal plan、论文 ID、证据、阅读计划和综述等 artifacts；HTTP 失败按 `{detail, code}` 返回 4xx/5xx，CLI/core 保留完整 failed run
6. CLI 可将 Markdown 与 journal 写入文件

## 3. 统一约定

### 3.1 响应格式

- 成功响应通常直接返回 JSON 对象或列表。
- 失败响应统一使用：

```json
{ "detail": "错误描述", "code": "ERROR_CODE" }
```

### 3.2 常见状态码

| 状态码 | 含义 |
|---|---|
| `200` | 成功 |
| `400` | 参数或业务错误 |
| `404` | 资源不存在 |
| `409` | 冲突，例如任务已运行 |
| `422` | Pydantic 校验失败 |
| `500` | 未处理异常 |

## 4. 数据模型

### 4.1 Paper

| 字段 | 说明 |
|---|---|
| `id` | 论文 ID |
| `filename` | 原始文件名 |
| `pdf_path` | 本地 PDF 路径 |
| `title/authors/venue/year/category` | 元信息 |
| `full_text` | 自动索引后的全文 |
| `index_status` | `none/indexing/indexed/failed` |
| `status` | `pending/extracting/analyzing/done/failed/indexed` |
| `code_url/code_status/code_path/code_summary` | 代码仓库信息 |
| `source` | `uploaded/expanded` |
| `level` | 论文层级 |
| `parent_paper_id` | 扩展来源 |
| `external_id` | 外部去重 ID |
| `canonical_id/doi/arxiv_id/openalex_id/s2_id` | 规范化论文身份 |
| `download_status` | `none/pending/available/unavailable/failed` |
| `source_ids` | 各 Provider 原始 ID 的可审计映射 |
| `graph_digest` | 图谱速读摘要 |

### 4.2 AnalysisResult

| 字段 | 说明 |
|---|---|
| `title/authors/venue/year` | 论文基础信息 |
| `one_sentence/problem` | 核心结论与问题 |
| `key_contributions` | 贡献列表 |
| `outline` | 章节大纲 |
| `method` | 方法结构 |
| `experiments` | 实验结构 |
| `code_mapping` | 概念到代码映射 |
| `interview_qa` | 面试问答 |
| `limitations` | 局限性 |
| `related_papers` | 相关论文 |
| `mermaid_chart` | 方法流程图 |

### 4.3 ResearchRun / ResearchGraph

| 模型 | 关键字段 |
|---|---|
| `ResearchRun` | `workspace_id/mode/status/current_stage/params/node_count/edge_count` |
| `ResearchGraph` | `workspace_id/run_id/nodes/edges/stats` |

### 4.4 Workspace / Note / Tag / Annotation

| 模型 | 关键字段 |
|---|---|
| `Workspace` | `id/name/created_at/updated_at` |
| `WorkspacePaper` | `workspace_id/paper_id/added_at` |
| `Note` | `paper_id/content/note_type/section_name/page_number` |
| `Tag` | `id/name/color` |
| `PaperTag` | `paper_id/tag_id/created_at` |
| `Annotation` | `paper_id/page_number/x/y/width/height/annotation_type/color/text/group_id` |

## 5. 接口

### 5.1 Papers

| 方法 | 路径 | 主要参数 | 返回 | 状态码 |
|---|---|---|---|---|
| POST | `/api/papers/upload` | `file`(multipart PDF) | `PaperResponse` | 200/400/422 |
| GET | `/api/papers` | `search/category/year/date_added/page/per_page/source` | `PaperListResponse` | 200/400 |
| GET | `/api/papers/smart-search` | `q/category/year/date_added/global/include_expanded/page/per_page` | `SearchResponse` | 200/400 |
| GET | `/api/papers/{paper_id}` | 路径参数 | `PaperResponse` | 200/404 |
| DELETE | `/api/papers/{paper_id}` | 路径参数 | `{"message": ...}` | 200/404 |
| POST | `/api/papers/{paper_id}/promote` | 路径参数 | `{"message": ...}` | 200/404 |
| POST | `/api/papers/reindex` | `include_expanded` | `{"queued": n}` | 200 |
| GET | `/api/papers/{paper_id}/export` | 路径参数 | Markdown 文本 | 200/404 |
| GET | `/api/papers/{paper_id}/pdf` | 路径参数 | PDF 文件 | 200/404 |
| GET | `/api/papers/{paper_id}/headings` | 路径参数 | 章节定位列表 | 200/404 |

### 5.2 Analysis

| 方法 | 路径 | 主要参数 | 返回 | 状态码 |
|---|---|---|---|---|
| GET | `/api/analysis/config` | 无 | `AppConfig` | 200 |
| POST | `/api/analysis/config` | `AppConfig` | `{"message": ...}` | 200 |
| GET | `/api/analysis/relations/channels` | 无 | `{"channels": [...]}` | 200 |
| GET | `/api/analysis/builtin-analysis-prompt` | 无 | `{"content": ...}` | 200 |
| GET | `/api/analysis/grobid/status` | 无 | GROBID 状态 | 200 |
| POST | `/api/analysis/grobid/pull` | 无 | 拉取状态 | 200 |
| POST | `/api/analysis/test-connection` | `type/api_key/base_url/model/provider` | 连通性结果 | 200/400 |
| POST | `/api/analysis/text-action` | `text/action` | `{"result": ...}` | 200/400/422 |
| POST | `/api/analysis/digest/batch` | `workspace_id/force/max_papers` | 批量速读状态 | 200/400 |
| GET | `/api/analysis/digest/status/{workspace_id}` | 路径参数 | 速读进度 | 200 |
| POST | `/api/analysis/digest/{paper_id}` | 路径参数 | 单篇速读结果 | 200/400/404 |
| POST | `/api/analysis/findpapers/test-databases` | `databases` | 数据库检测 | 200 |
| POST | `/api/analysis/import-related-papers` | `workspace_id/papers[]` | 导入结果 | 200/404 |
| GET | `/api/analysis/{paper_id}` | 路径参数 | `AnalysisResponse` | 200/404 |
| GET | `/api/analysis/{paper_id}/bibtex` | 路径参数 | BibTeX 文本 | 200/400/404 |
| GET | `/api/analysis/{paper_id}/stream` | 路径参数 | SSE | 200/404 |
| POST | `/api/analysis/{paper_id}/start` | 路径参数 | 进度信息 | 200/404 |
| GET | `/api/analysis/{paper_id}/progress` | 路径参数 | 进度信息 | 200/404 |
| POST | `/api/analysis/{paper_id}/reparse` | 路径参数 | `PaperResponse` | 200/404 |
| POST | `/api/analysis/{paper_id}/related-papers` | 路径参数 | `RelatedPaper[]` | 200/404 |

### 5.3 Code

| 方法 | 路径 | 主要参数 | 返回 | 状态码 |
|---|---|---|---|---|
| POST | `/api/code/{paper_id}/extract-url` | 路径参数 | `{"code_url": ...}` | 200/404 |
| POST | `/api/code/{paper_id}/clone` | 路径参数 | 克隆结果 | 200/400/404/500 |
| GET | `/api/code/{paper_id}/tree` | 路径参数 | 文件树 | 200/400/404 |
| GET | `/api/code/{paper_id}/file` | `path` | 文件内容或 notebook cell | 200/400/404 |
| GET | `/api/code/{paper_id}/analyze` | 路径参数 | SSE | 200/400/404 |
| GET | `/api/code/{paper_id}/analysis` | 路径参数 | `CodeAnalysisResponse` | 200/404 |
| GET | `/api/code/{paper_id}/summary` | 路径参数 | `CodeSummaryResponse` | 200/404 |

### 5.4 Workspaces

| 方法 | 路径 | 主要参数 | 返回 | 状态码 |
|---|---|---|---|---|
| GET | `/api/workspaces` | 无 | 工作区列表 | 200 |
| POST | `/api/workspaces` | `name` | `WorkspaceResponse` | 200 |
| GET | `/api/workspaces/{workspace_id}` | 路径参数 | `WorkspaceDetailResponse` | 200/404 |
| GET | `/api/workspaces/{workspace_id}/insights` | 路径参数 | `WorkspaceInsightResponse` | 200/404 |
| GET | `/api/workspaces/{workspace_id}/reading-plan` | `target_paper_id` 可选 | `WorkspaceReadingPlanResponse` | 200/404 |
| GET | `/api/workspaces/{workspace_id}/survey` | 路径参数 | `WorkspaceSurveyResponse` | 200/404 |
| POST | `/api/workspaces/{workspace_id}/compare` | `paper_a_id/paper_b_id` | `WorkspaceComparisonResponse` | 200/400/404 |
| PUT | `/api/workspaces/{workspace_id}` | `name` | `WorkspaceResponse` | 200/404 |
| DELETE | `/api/workspaces/{workspace_id}` | 路径参数 | `{"message": ...}` | 200/404 |
| POST | `/api/workspaces/{workspace_id}/papers` | `paper_id` | `{"message": ...}` | 200/404/400 |
| POST | `/api/workspaces/{workspace_id}/papers/{paper_id}/promote` | 路径参数 | `{"message": ...}` | 200/404 |
| DELETE | `/api/workspaces/{workspace_id}/papers/{paper_id}` | 路径参数 | `{"message": ...}` | 200/404 |

### 5.5 Research

| 方法 | 路径 | 主要参数 | 返回 | 状态码 |
|---|---|---|---|---|
| POST | `/api/research/workspaces/{ws_id}/runs` | `ResearchRunCreate` | `ResearchRunResponse` | 200/400/404/409 |
| GET | `/api/research/runs/{run_id}` | 路径参数 | `ResearchRunResponse` | 200/404 |
| GET | `/api/research/runs/{run_id}/stream` | 路径参数 | SSE | 200/404 |
| GET | `/api/research/workspaces/{ws_id}/graph` | 路径参数 | `ResearchGraphResponse` | 200/404 |
| GET | `/api/research/workspaces/{ws_id}/latest-run` | 路径参数 | `ResearchRunResponse` | 200/404 |
| POST | `/api/research/workspaces/{ws_id}/query` | `question/top_k/paper_ids/mode(vector|hybrid|graph)` | `RagQueryResponse(answer/citations/path_evidence/debug)`；citation 含检索来源分数与 PDF 状态 | 200/404 |
| DELETE | `/api/research/workspaces/{ws_id}` | 路径参数 | `{"message": ...}` | 200 |

### 5.6 Notes / Tags / Annotations

| 模块 | 方法 | 路径 | 返回 | 状态码 |
|---|---|---|---|---|
| Notes | GET | `/api/notes/paper/{paper_id}` | `NoteResponse[]` | 200 |
| Notes | POST | `/api/notes/paper/{paper_id}` | `NoteResponse` | 200/404 |
| Notes | PUT | `/api/notes/{note_id}` | `NoteResponse` | 200/404 |
| Notes | DELETE | `/api/notes/{note_id}` | `{"message": ...}` | 200/404 |
| Tags | GET | `/api/tags` | `TagResponse[]` | 200 |
| Tags | POST | `/api/tags` | `TagResponse` | 200/400 |
| Tags | PUT | `/api/tags/{tag_id}` | `TagResponse` | 200/404/400 |
| Tags | DELETE | `/api/tags/{tag_id}` | `{"message": ...}` | 200/404 |
| Tags | GET | `/api/tags/paper/{paper_id}` | `TagResponse[]` | 200 |
| Tags | POST | `/api/tags/paper/{paper_id}` | `{"message": ...}` | 200/404 |
| Tags | DELETE | `/api/tags/paper/{paper_id}/{tag_id}` | `{"message": ...}` | 200 |
| Tags | GET | `/api/tags/papers/{tag_id}` | 论文列表 | 200 |
| Annotations | GET | `/api/annotations/paper/{paper_id}` | `AnnotationResponse[]` | 200 |
| Annotations | POST | `/api/annotations/paper/{paper_id}` | `AnnotationResponse` | 200/404 |
| Annotations | DELETE | `/api/annotations/group/{group_id}` | `{"message": ...}` | 200/404 |
| Annotations | DELETE | `/api/annotations/{annotation_id}` | `{"message": ...}` | 200/404 |

### 5.7 System

| 方法 | 路径 | 返回 | 状态码 |
|---|---|---|---|
| GET | `/api/logs/stream` | SSE 日志流 | 200 |
| WS | `/api/terminal/ws` | PTY 终端 | 101/4003/4029 |
| POST | `/api/system/open-vscode` | `{"message": ...}` 或 `{"error": ...}` | 200 |

### 5.8 Agent Tools / MCP

| 方法 | 路径 | 主要参数 | 返回 | 状态码 |
|---|---|---|---|---|
| GET | `/api/agent-tools/manifest` | 无 | `AgentToolManifestResponse` | 200 |
| GET | `/api/agent-tools/workspaces` | 无 | 工作区列表 | 200 |
| GET | `/api/agent-tools/workspaces/{workspace_id}/papers` | 路径参数 | 工作区论文列表 | 200/404 |
| POST | `/api/agent-tools/papers/search` | `query/workspace_id/limit/include_expanded` | 论文检索结果 | 200/400/404 |
| GET | `/api/agent-tools/papers/{paper_id}` | 路径参数 | 论文结构化上下文 | 200/404 |
| GET | `/api/agent-tools/workspaces/{workspace_id}/graph` | 路径参数 | 图谱快照 | 200/404 |
| POST | `/api/agent-tools/workspaces/{workspace_id}/graph/neighbors` | `paper_id/depth/limit/edge_kinds` | 图谱邻域 | 200/400/404 |
| POST | `/api/agent-tools/workspaces/{workspace_id}/evidence` | `query/top_k/paper_ids` | 证据片段列表 | 200/400/404 |
| POST | `/api/agent-tools/workspaces/{workspace_id}/dev-brief` | `goal/top_k/paper_ids` | 论文证据约束开发 brief | 200/400/404 |
| GET | `/api/agent-tools/workflow/langgraph-spec` | 无 | LangGraph 工作流规范 | 200 |
| GET | `/api/agent-tools/workflows` | 无 | Research Agent workflow 列表 | 200 |
| GET | `/api/agent-tools/workflows/{workflow_name}` | 路径参数 | workflow 规范 | 200/404 |
| POST | `/api/agent-tools/workspaces/{workspace_id}/reading-plan` | `target_paper_id` 可选 | 工作区阅读计划 | 200/400/404 |
| POST | `/api/agent-tools/workspaces/{workspace_id}/survey` | 路径参数 | 工作区综述矩阵与草稿 | 200/400/404 |
| POST | `/api/agent-tools/runs` | `workflow/goal/workspace_id` | workflow journal 与 artifacts；失败 `{detail, code}` | 200/400/404/422/500 |

#### 5.8.1 MCP stdio

| 启动命令 | 工具来源 |
|---|---|
| `python -m backend.mcp_server` | `services/agent_tools.py` |

MCP 工具：

| 工具名 | 功能 | 参数 |
|---|---|---|
| `paper_search` | 检索本地论文库或工作区论文 | `query/workspace_id/limit/include_expanded` |
| `paper_get` | 获取单篇论文结构化上下文 | `paper_id` |
| `paper_retrieve_evidence` | 从工作区向量索引检索证据片段 | `workspace_id/query/top_k/paper_ids` |
| `graph_get` | 获取工作区图谱快照 | `workspace_id` |
| `graph_neighbors` | 获取图谱中某论文邻域 | `workspace_id/paper_id/depth/limit/edge_kinds` |
| `dev_brief` | 生成论文证据约束的开发 brief | `workspace_id/goal/top_k/paper_ids` |
| `reading_plan_build` | 构建工作区阅读计划 | `workspace_id/target_paper_id` |
| `survey_build` | 构建工作区综述矩阵与草稿 | `workspace_id` |

兼容旧工具名：`list_workspaces`、`list_papers_in_workspace`、`get_paper_analysis`、`get_citation_graph`、`query_workspace_rag`。

## 6. 前端结构

### 6.1 页面

- `HomePage`：上传入口、最近论文、工作区概览
- `ReaderPage`：PDF 预览、解析结果、标签、笔记、标注
- `LibraryPage`：论文库检索、筛选、全文搜索
- `WorkspacePage`：工作区列表
- `WorkspaceDetailPage`：工作区论文管理、洞察、阅读路径、综述矩阵
- `ResearchPage`：图谱构建、RAG、节点/边详情
- `CodePage`：代码分析与终端
- `SettingsPage`：模型、主题、研究配置

### 6.2 关键组件

- `AnalysisPanel`
- `PdfViewer`
- `GraphCanvas`
- `WorkspaceInsights`
- `WorkspaceInnovationPanel`
- `WorkspaceComparison`
- `TagManager`
- `NotesPanel`
- `SelectionPopup`
- `TerminalPanel`
- `VersionPopup`

## 7. 深度研究评测验收（P9）

离线 mock 评测位于 `backend/tests/evaluation/deep_research/`，文档见 `docs/DEEP_RESEARCH_EVAL.md`。

```bash
export EVAL_ALLOW_NETWORK=0
python -m backend.tests.evaluation.deep_research.eval.cli run --mode mock
python -m backend.tests.evaluation.deep_research.eval.cli gate --mode mock
python -m backend.tests.evaluation.deep_research.eval.cli compare \
  backend/tests/evaluation/deep_research/baselines/mock_v1.json \
  backend/tests/evaluation/deep_research/reports/mock_gate_report.json
pytest backend/tests/test_deep_research_eval.py -q
```

- CI `eval-fast` job 强制执行 mock gate（`EVAL_ALLOW_NETWORK=0`）。
- mock 门禁仅保证相对冻结基线不回归；真实发布需人工 Scholar 盲测（见文档），禁止自动爬取对照结果。
- 真实 30+ seed、至少 3 个子领域、双人标注与 Scholar 盲测完成前，`discovery_v2` 默认关闭。
