# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

论文精读助手 — 面向 CV/ML 研究者的论文精读 Web 应用。上传 PDF 论文，AI 自动解析为结构化内容：核心贡献、方法论、实验设置、代码映射、面试问答。支持关联代码仓库进行代码分析。支持工作区管理（多论文组织）与深度研究（引文图谱构建 + 向量检索 RAG 问答）。

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
复制 `.env.example` 为 `.env`，填入 `LLM_API_KEY`（及可选的 `LLM_BASE_URL` / `LLM_MODEL`）。协议仅 OpenAI 兼容或 Anthropic 兼容（由 base_url 判定）。AI 模型也可在运行时通过 `/settings` 页面配置。

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
│   ├── workspace.py      # Workspace + WorkspacePaper 模型（多对多）
│   └── schemas.py        # API Schema（PaperResponse, AnalysisResponse 等）
├── routers/         # API 路由
│   ├── papers.py         # /api/papers/*（上传、列表、删除、导出、PDF）
│   ├── analysis.py       # /api/analysis/*（解析、SSE 流、配置、文本操作、相关论文）
│   ├── code.py           # /api/code/*（URL 提取、clone、文件树、文件内容、分析）
│   ├── workspaces.py     # /api/workspaces/*（工作区 CRUD + 论文关联）
│   └── research.py       # /api/research/*（深度研究 run、SSE 进度、图谱、RAG 问答）
├── services/        # 业务逻辑
│   ├── ai_analyzer.py    # Anthropic API 调用（论文分析、流式输出）
│   ├── ai_config.py      # AI 配置读写（data/ai_config.json，含 GROBID/findpapers 配置）
│   ├── analysis_runner.py # 分析后台任务管理器（断点续传，SSE 解耦）
│   ├── code_analyzer.py  # 代码仓库 clone、文件树构建、静态分析、AI 分析
│   ├── database.py       # SQLite CRUD + schema 自动迁移
│   ├── findpapers_service.py # findpapers 多源学术搜索（可替代 Semantic Scholar）
│   ├── import_parser.py  # 多语言 import 解析（Python AST + JS/TS/C++ 正则）
│   ├── log_stream.py     # 后端日志 SSE 流
│   ├── pdf_parser.py     # PDF 提取（PyMuPDF 主路径 + pypdf 后备）
│   ├── semantic_scholar.py # Semantic Scholar 相关论文搜索
│   ├── terminal.py       # WebSocket PTY 终端
│   └── deep_research/    # 深度研究子模块
│       ├── pipeline.py        # 研究流水线编排（collect→expand→chunk→embed→graph→persist）
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
│           ├── relatedness.py     # ★ 唯一的关联度算法：八通道分位数归一 + 加权融合
│           ├── calibration.py     # ★ 通道权重自监督校准（工作区引用结构当弱标签）
│           ├── backbone.py        # 多尺度骨架提取（Disparity Filter + 目标密度反解）
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
5. **深度研究**: 工作区论文集合 → collect（下载 PDF）→ expand（GROBID 引用解析 + arXiv/CrossRef 扩展）→ chunk + embed（向量化）→ digest（图谱速读）→ graph（引文图谱）→ persist → 前端图谱可视化 + RAG 问答
6. **论文关联度**（图谱关系的唯一算法，`graphkit/relatedness.py`）: 主题向量 / 研究目的 / 方法 / IDF 术语 / 共享数据集 / 共享 baseline / 文献耦合 / 共引 八个通道各自分位数归一 → 加权融合成一个分数 → 多尺度骨架抽骨架 → 该分数同时决定连边、社区划分、节点重要性与图检索。设置页「论文关联度」调权重先验与是否自动调权
7. **权重自监督校准**（`graphkit/calibration.py`）: 哪个通道管用随语料变（实测真实工作区 topic 判别力 AUC 0.89、method 0.57≈随机；细分方向语料上恰好相反）。建图时用工作区自身的引用结构（直接引用 + 文献耦合）当弱标签，量出各**语义**通道的判别力（AUC→Gini，扣掉抽样噪声下限），据此重分配语义组权重，再按证据量向先验收缩。证据不足 / 判别力与随机无异 / 权重被调成 0 的通道 → 不动先验
8. **地图密度控制**（`graphkit/backbone.py`）: 骨架的密度旋钮是**目标平均度** `map_density`（每篇论文平均连几条边），二分反解"环境关联度地板"后再做 Disparity Filter 显著性检验。α 只管局部显著性。这样密度控制与打分尺度无关——v3.0 用 α 当密度旋钮，换了打分口径后在"树"和"毛球"之间没有可用区间

### 路由

| 路径 | 页面 |
|------|------|
| `/` | 首页（上传入口 + 最近论文）|
| `/reader/:id` | 论文精读（PDF 预览 + 解析结果 Tab）|
| `/code/:id` | 代码分析（文件浏览器 + 代码查看器 + 分析面板）|
| `/library` | 论文库 |
| `/settings` | 设置（AI 模型配置 + UI 配置）|
| `/workspace` | 工作区列表 |
| `/workspace/:id` | 工作区详情（论文管理）|
| `/workspace/:id/research` | 深度研究（引文图谱 + RAG 问答）|

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
