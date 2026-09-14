# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 项目概述

论文精读助手 — 面向 CV/ML 研究者的论文精读 Web 应用。上传 PDF 论文，AI 自动解析为结构化内容：核心贡献、方法论、实验设置、代码映射、面试问答。支持关联代码仓库进行代码分析。支持工作区管理（多论文组织）与深度研究（引文图谱构建 + 向量检索 RAG 问答）。

**总目标与开发原则见根目录 `GOAL.md`**（毕设最高优先级指导：让学者免读单篇论文即可客观了解领域现状与 idea 查新；文档地图也在其中——写新文档前先查它，避免重复）。

## 技术栈

- **前端**: React 18 + TypeScript + Tailwind CSS + Vite + Recharts + Mermaid + xterm.js + highlight.js
- **后端**: Python + FastAPI + SQLModel + PyMuPDF/pypdf + OpenAI/Anthropic SDK（统一 base_url + api_key）+ httpx
- **数据库**: SQLite（开发环境 `./data/essay_read.db`）

## 常用命令

### 一键启动（推荐）
```bash
./start.sh
```

### 后端开发
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
# 或
python -m backend
```

### 前端开发
```bash
cd frontend
npm install
npm run dev        # 开发服务器 http://localhost:5173
npm run build      # TypeScript 编译 + Vite 构建
```

### 环境变量
复制 `.env.example` 为 `.env`，填入 `LLM_API_KEY`（及可选的 `LLM_PROVIDER` / `LLM_API_FORMAT` / `LLM_BASE_URL` / `LLM_MODEL`）。`LLM_API_FORMAT` 明确选择 `openai_chat` 或 `anthropic_messages`；仅旧配置迁移时按 URL 推断一次。AI 模型也可在运行时通过 `/settings` 页面统一配置。

## 架构概览

### 后端结构（FastAPI）

```
backend/
├── main.py          # FastAPI 入口，CORS、WebSocket PTY、SSE 日志流
├── config.py        # pydantic-settings 配置管理（单例 settings）
├── models/          # SQLModel 数据库模型 + Pydantic Schema
│   ├── paper.py          # Paper 模型（含 code_* 字段）
│   ├── analysis.py       # AnalysisResult + 子结构（MethodModel, ExperimentModel 等）
│   ├── code_analysis.py  # CodeAnalysis 模型（文件树、数据流、依赖图）
│   ├── research.py       # ResearchRun + ResearchGraph 模型
│   ├── exploration.py    # ExplorationRun 模型（问句驱动的领域探索，见 GOAL.md；含 v2 workflow 列）
│   ├── research_core.py  # academic_survey_v2 契约（S0 冻结）：状态常量、Core Pydantic 契约、research_* 十张表
│   ├── workspace.py      # Workspace + WorkspacePaper 模型（多对多）
│   └── schemas.py        # API Schema（PaperResponse, AnalysisResponse 等）
├── routers/         # API 路由
│   ├── papers.py         # /api/papers/*（上传、列表、删除、导出、PDF）
│   ├── analysis.py       # /api/analysis/*（解析、SSE 流、配置、文本操作、相关论文）
│   ├── code.py           # /api/code/*（URL 提取、clone、文件树、文件内容、分析）
│   ├── workspaces.py     # /api/workspaces/*（工作区 CRUD + 论文关联）
│   ├── research.py       # /api/research/*（深度研究 run、SSE 进度、图谱、RAG 问答）
│   └── exploration.py    # /api/exploration/*（领域探索 run、SSE 进度、产物查询）
├── services/        # 业务逻辑
│   ├── ai_analyzer.py    # 论文分析与增量 JSON 解析（仅依赖统一 LLM Core）
│   ├── ai_config.py      # 配置仓库（llm 单一真源、旧配置迁移、密钥隔离）
│   ├── llm/              # 统一 LLM Core：配置契约、协议 adapter、流式/JSON/重试/脱敏错误
│   ├── llm_proxy/        # 合规代理传输：显式网关地址切换，不伪造受限客户端身份
│   ├── analysis_runner.py # 分析后台任务管理器（断点续传，SSE 解耦）
│   ├── code_analyzer.py  # 代码仓库 clone、文件树构建、静态分析、AI 分析
│   ├── database.py       # SQLite CRUD + schema 自动迁移
│   ├── findpapers_service.py # findpapers 多源学术搜索（可替代 Semantic Scholar）
│   ├── import_parser.py  # 多语言 import 解析（Python AST + JS/TS/C++ 正则）
│   ├── log_stream.py     # 后端日志 SSE 流
│   ├── pdf_parser.py     # PDF 提取（PyMuPDF 主路径 + pypdf 后备）
│   ├── semantic_scholar.py # Semantic Scholar 相关论文搜索
│   ├── terminal.py       # WebSocket PTY 终端
│   ├── research_event_log.py # v2 持久化事件/预算聚合/运行状态 CAS/live-bus 注册缝（S0；worker 由 S3 接入）
│   └── deep_research/    # 深度研究子模块
│       ├── pipeline.py        # 研究流水线编排（collect→expand→chunk→embed→digest→graph→synthesize→persist）
│       ├── explorer.py        # 统一研究（问题+可选母论文→召回→计量→综合→报告）
│       ├── survey_report.py   # 报告证据循环、实验比较表、终稿核验、HTML/Markdown导出
│       ├── document_evidence.py # 已获取全文的章节/页码/表格索引与定向补读（不负责下载）
│       ├── report_evidence.py # 稳定证据 ID、去重、分页索引、详情与核验版本
│       ├── stage_agent.py     # 阶段级有界 ReAct（白名单工具、程序验收、预算/无进展终止）
│       ├── domain_synthesizer.py # 领域认知综合（节点角色/引用因果/流派/全景简报，LLM 批量注入）
│       ├── embedder.py        # 文本向量化
│       ├── vector_store.py    # 向量存储 + RAG 查询
│       ├── graph_builder.py   # 引文图谱构建
│       ├── event_bus.py       # 异步事件总线（SSE 进度推送）
│       ├── grobid_parser.py   # GROBID 结构化引用解析
│       ├── ref_parser.py      # 引用解析调度（GROBID / regex 回退）
│       ├── arxiv_client.py    # arXiv 论文下载
│       ├── crossref_client.py # CrossRef 元数据查询
│       ├── pdf_downloader.py  # PDF 批量下载
│       ├── reranker.py        # 搜索结果重排序
│       ├── paper_relatedness.py # 论文关联度的生产适配层（DB/向量库 → PaperFeatures）
│       ├── stages/            # 流水线各阶段实现
│       ├── providers/         # 外部数据源适配器
│       └── graphkit/          # 图谱算法纯净核心（无 DB 依赖）
│           ├── relatedness.py     # 唯一关联度：八通道对称排名 RRF（等权默认，不做弱标签调权）
│           ├── backbone.py        # 多尺度骨架提取（Disparity Filter）
│           ├── lexical.py         # IDF 加权术语重叠
│           ├── structural.py      # 文献耦合 / 共引（Salton）
│           ├── vectors.py         # 论文主题向量
│           └── features.py        # PaperFeatures 数据边界
└── utils/
    └── streaming.py      # SSE 格式化 + Markdown 导出
```

### 前端结构（React）

```
frontend/src/
├── api/             # fetch 封装（client.ts）+ 各模块 API 调用
│   ├── client.ts         # 通用 get/post/upload/del
│   ├── papers.ts         # 论文 API
│   ├── code.ts           # 代码仓库 API
│   ├── textAction.ts     # 翻译/AI 总结 API
│   ├── settings.ts       # 设置 API
│   ├── workspaces.ts     # 工作区 API
│   └── research.ts       # 深度研究 API
├── hooks/           # 自定义 Hook
│   ├── useStreaming.ts      # SSE 流式连接（支持 shouldConnect 参数）
│   ├── useAnalysis.ts      # 解析生命周期（顶层调用 useStreaming）
│   ├── useConfig.ts        # 配置管理
│   └── useResearchStream.ts # 深度研究 SSE 进度流
├── features/        # 功能模块
│   ├── upload/           # PDF 上传
│   ├── viewer/           # PDF 预览（react-pdf）
│   ├── analysis/         # 解析结果（6 个 Tab：概览/大纲/方法/实验/代码/Q&A）
│   ├── code/             # 代码分析（文件浏览器 + 代码查看器 + 分析面板 + 终端）
│   ├── library/          # 论文库列表
│   └── research/         # 深度研究（引文图谱 + RAG 问答）
├── pages/           # 路由页面（Home, Reader, Code, Library, Settings, Workspace, Research）
├── components/      # 通用组件（Layout 双层导航, Sidebar, TerminalPanel, SelectionPopup, ThemeProvider, Tooltip）
├── theme/           # 主题系统（themes.ts 主题定义，useTheme.ts Hook）
└── types/           # 全局 TypeScript 类型（与后端 Schema 一一对应）
```

### 关键数据流

1. **PDF 上传 → AI 解析**: 上传 PDF → 提取文本（PyMuPDF，超 100k 字符截断）→ 后台 `analysis_runner` 异步执行 → Anthropic API 分析 → Semantic Scholar / findpapers 搜索相关论文 → 存入 SQLite（支持断点续传）
2. **SSE 流式推送**: 分析运行在后台任务中，前端连接 `/api/analysis/{id}/stream` 订阅进度；断开只取消订阅，分析继续执行；重连后回放已缓冲事件
3. **代码仓库分析**: 正则提取代码 URL → clone → 构建文件树 → 静态分析依赖（import_parser）→ AI 分析数据流和论文-代码映射
4. **文本操作**: PDF 选中文本 → SelectionPopup → `/api/analysis/text-action`（翻译/总结）
5. **深度研究**: 工作区论文集合 → collect（下载 PDF）→ expand（GROBID 引用解析 + arXiv/CrossRef 扩展）→ chunk + embed（向量化）→ digest（图谱速读）→ graph（引文图谱）→ synthesize（领域认知综合：节点角色/引用因果/流派/全景简报）→ persist → 前端图谱可视化（角色徽章 + 因果边 + 📜 领域全景面板）+ RAG 问答。扩展源默认 auto：有 Elsevier Key 首选 Scopus（全学科），鉴权失败自动回退 OpenAlex
6. **论文关联度**（`graphkit/relatedness.py`）: 八通道逐端排名的对称 RRF（k=60、默认等权）→ mutual-kNN（k=10）→ Leiden 与关系 PageRank。原始 Disparity Filter（alpha=.05）独立标记，不控制选边；展示桥不参与统计；自定义权重仅表示偏好，不做引文弱监督拟合。实际方法与解释随图谱保存。
7. **统一研究**：`research_sources.py`适配可选工作区母论文，复用深度研究关联召回；多检索式 RRF → 预训练向量/mutual-kNN/Leiden → Poisson + BH → BM25/向量/RRF/重排 → 综合及证据核验 → 结构化调查报告（框架、指标、任务/数据集、实验比较、热力图）。报告侧由 `document_evidence.py` 对已获取文本建立页/节/表索引，`report_evidence.py` 持久保存稳定证据 ID 和完整原文；agent 可按子问题分页读索引、读取详情、定向补读已有论文，并对实际终稿逐断言核验。外层阶段单向推进，拆解/综合/逐篇证据/报告节点由 `stage_agent.py` 执行有界 ReAct，程序验收后才能完成；零召回仍产出带搜索审计与补搜方向的 `no_results` 报告，不宣称领域空白。`ExplorationRun.options`保存输入快照；历史材料或搜索记录可单独补生成报告。阶段边界落检查点（`ExplorationRun.checkpoint`：已完成阶段游标 + 报告逐篇提取与共享证据缓存），语料/计量/综合产物随 `result` 列逐阶段持久化；失败/暂停的 run 经 `POST /runs/{id}/resume` 从断点续跑（跳过已完成阶段与已提取篇目），`POST /runs/{id}/pause` 在安全点（阶段/召回轮/agent 迭代/篇目边界）协作式暂停；进程重启遗留的僵尸 run 启动自愈置 failed 后同样可续传。契约见 `document.md` 2.9–2.14。
8. **academic_survey_v2（契约层，实验性）**：`models/research_core.py` 冻结任务账本/资产/证据/关系/claim/报告 revision/预算/事件/身份判定十张表与状态常量；`research_event_log.py` 提供持久化事件回放、运行状态 CAS（数据库条件更新为并发权威，守卫 UPDATE 校验受影响行数）与 live-bus 注册缝；`/api/exploration` 新增 protocol/tasks/coverage/budget/pause/resume/cancel/retry/revisions/evidence/stream 扩展（v1 运行走 legacy 投影，行为不变）；任务幂等键以 (run_id, idempotency_key) 唯一；默认报告入口对 v2 解析当前选定 revision（隔离资产 403 拒绝导出）。执行器（S3–S6）接入前，显式 `workflow_version=academic_survey_v2` 创建返回 `WORKFLOW_NOT_IMPLEMENTED`。契约见 `document.md` 2.12 与 `docs/ACADEMIC_SURVEY_AGENT_PLAN_2026-09-08.md`。

### 路由

| 路径 | 页面 |
|------|------|
| `/` | 首页（上传入口 + 最近论文）|
| `/reader/:id` | 论文精读（PDF 预览 + 解析结果 Tab）|
| `/code/:id` | 代码分析（文件浏览器 + 代码查看器 + 分析面板）|
| `/library` | 论文库 |
| `/settings` | 设置（AI 模型配置 + UI 配置）|
| `/explore` | 统一文献研究（自然语言问题 + 可选母论文 + 联网补充）|
| `/explorer/:id` | 研究报告、分析结果、全部资料与连续追问 |
| `/workspace` | 工作区列表 |
| `/workspace/:id` | 工作区详情（论文管理）|
| `/workspace/:id/research` | 兼容跳转到统一入口并携带工作区 |
| `/workspace/:id/graph` | 原工作区图谱与全文索引工具 |

## 开发约定

- **提交流程**: 涉及提交代码时，**必须先阅读 `docs/commit_regular.md`**，按其规范确定版本号、同步 VersionPopup 更新日志和 package.json 版本号，再执行提交
- **类型对应**: 前端 `types/index.ts` 与后端 `models/schemas.py` 严格对应
- **错误格式**: API 统一返回 `{detail, code}`
- **UI 主题**: 支持多主题（`theme/themes.ts` 定义，ThemeProvider 注入 CSS 变量），默认深色，主色 blue-500
- **SSE 代理**: Vite proxy 已配置禁用 SSE 缓冲（`vite.config.ts`）
- **数据库迁移**: 简易迁移在 `services/database.py` 的 `_migrate_schema()` 中，启动时自动执行
- **文件上传**: 存储在 `./uploads/`，文件名使用 UUID
- **代码仓库**: clone 到 `./code_downloads/{paper_id}/`
- **PDF 截断**: 超过 100k 字符保留前 3 页 + 最后 1 页
- **配置存储**: AI 配置存储在 `./data/ai_config.json`，通过 SettingsPage 管理

## Cursor Cloud specific instructions

启动脚本会自动运行 update script（创建 `.venv`、`pip install -r backend/requirements.txt`、`npm install --prefix frontend`、缺失时从 `.env.example` 生成 `.env`）。后续 agent 无需重装依赖，只需启动服务。以下为非显而易见的注意事项：

- **Python 环境**: 后端依赖装在项目根目录的 `.venv`（`python3 -m venv .venv`，需系统包 `python3.12-venv`，已在镜像中预装）。运行后端用 `.venv/bin/python -m backend`。`torch`/`sentence-transformers` 体积大，首次安装耗时较长。
- **LLM 配置**: 统一使用 `LLM_PROVIDER` / `LLM_API_FORMAT` / `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`（见 `.env.example`）；所有文本生成业务只调用 `services/llm`，不得自行创建 SDK 客户端或按 URL 判断协议。`llm_api_key` **允许为空**，缺少 key 时后端仍可启动（PDF 上传、论文库、前端 UI 正常）；真正的 AI 解析/翻译/深度研究需要真实 key。旧变量名 `MIMO_*` / `OPENAI_*` / `ANTHROPIC_*` 会映射到 `LLM_*`，旧 `analysis/translate` 配置只作为一次性读取迁移输入。
- **启动服务**: 一键用 `./start.sh`（前端 5173 + 后端 8000 + GROBID）。云环境**无 Docker**，请用 `./start.sh --no-grobid`，或分别启动：根目录 `DEV=true HOST=0.0.0.0 .venv/bin/python -m backend`（`DEV=true` 开启热重载）与 `npm run dev --prefix frontend`。前端 Vite 已把 `/api` 代理到 `http://localhost:8000`。
- **GROBID 可选**: 深度研究的引用解析依赖 GROBID（Docker，8070 端口）。云环境无 Docker 时会自动回退到正则解析（`ref_parser.py`），不阻塞其余功能。
- **Lint / Test / Build**（命令来源见 `.github/workflows/ci.yml`）:
  - 后端 lint: `.venv/bin/ruff check backend/ --select E,F,W --ignore E501,E402,F401,F841,W291,W293`（`ruff` 非 requirements 依赖，需单独 `pip install ruff`）。
  - 后端测试: `cd backend && LLM_API_KEY=test-key-for-ci ../.venv/bin/python -m pytest tests/`（测试用桩 key，不发真实网络请求）。
  - 前端 typecheck: `npm run typecheck --prefix frontend`；前端测试: `npm run test:run --prefix frontend`（`ErrorBoundary.test.tsx` 会故意抛错并打印堆栈，属正常现象）。
  - 前端生产构建: `npm run build --prefix frontend`（开发用 `npm run dev`）。
