# 论文精读助手 — 完整规范文档

## 项目文件树

```
essay_read/
├── .env.example                    # 环境变量模板
├── README.md                       # 项目说明
├── start.sh                        # 一键启动脚本
├── docs/
│   └── SPECIFICATIONS.md           # 本文档
│
├── backend/
│   ├── __init__.py                 # 包标识
│   ├── __main__.py                 # python -m backend 入口
│   ├── main.py                     # FastAPI 应用入口（中间件、WebSocket、SSE）
│   ├── config.py                   # 配置管理（pydantic-settings）
│   ├── requirements.txt            # Python 依赖
│   ├── models/
│   │   ├── __init__.py
│   │   ├── paper.py                # Paper 数据库模型
│   │   ├── analysis.py             # AnalysisResult + 子结构模型
│   │   ├── code_analysis.py        # CodeAnalysis 数据库模型
│   │   └── schemas.py              # API 请求/响应 Schema
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── papers.py               # /api/papers/* 路由
│   │   ├── analysis.py             # /api/analysis/* 路由（含配置管理、文本操作）
│   │   └── code.py                 # /api/code/* 路由（代码仓库管理）
│   ├── services/
│   │   ├── __init__.py
│   │   ├── ai_analyzer.py          # MiMo/Anthropic API 调用（论文分析）
│   │   ├── ai_config.py            # AI 配置管理（读写 data/ai_config.json）
│   │   ├── code_analyzer.py        # 代码仓库 clone、文件树、静态分析、AI 分析
│   │   ├── database.py             # SQLite CRUD + schema 迁移
│   │   ├── log_stream.py           # 后端日志 SSE 流
│   │   ├── pdf_parser.py           # PDF 文本提取（PyMuPDF + pypdf 双路径）
│   │   ├── semantic_scholar.py     # Semantic Scholar 相关论文搜索
│   │   └── terminal.py             # WebSocket PTY 终端
│   └── utils/
│       ├── __init__.py
│       └── streaming.py            # SSE 格式化 + Markdown 导出
│
└── frontend/
    ├── package.json
    ├── tsconfig.json
    ├── vite.config.ts              # Vite 配置（含 SSE 代理优化）
    ├── tailwind.config.js
    ├── postcss.config.js
    ├── index.html
    └── src/
        ├── main.tsx                # React 入口
        ├── App.tsx                 # 路由配置
        ├── types/
        │   └── index.ts            # 全局 TypeScript 类型
        ├── api/
        │   ├── client.ts           # fetch 封装（get/post/upload/del）
        │   ├── papers.ts           # 论文 API 调用
        │   ├── code.ts             # 代码仓库 API 调用
        │   └── textAction.ts       # 文本翻译/总结 API
        ├── hooks/
        │   ├── useStreaming.ts     # SSE 流式 hook
        │   ├── useAnalysis.ts      # 解析生命周期 hook
        │   └── useConfig.ts        # 配置管理 hook
        ├── components/
        │   ├── Layout.tsx          # 全局布局（双层导航 + 底部终端）
        │   ├── Sidebar.tsx         # 论文列表侧边栏（折叠式覆盖层）
        │   ├── LoadingState.tsx    # 加载状态
        │   ├── SelectionPopup.tsx  # PDF 选中文本弹窗（翻译/AI 总结）
        │   └── TerminalPanel.tsx   # 底部终端面板（后端日志/前端日志/PTY）
        ├── features/
        │   ├── upload/
        │   │   └── UploadZone.tsx  # PDF 上传区
        │   ├── viewer/
        │   │   └── PdfViewer.tsx   # PDF 预览（react-pdf）
        │   ├── analysis/
        │   │   ├── AnalysisPanel.tsx  # 解析结果 Tab 面板
        │   │   ├── FlowChart.tsx      # Mermaid 流程图
        │   │   └── tabs/
        │   │       ├── OverviewTab.tsx   # 概览 Tab
        │   │       ├── OutlineTab.tsx    # 大纲 Tab（支持跳转 PDF 页面）
        │   │       ├── MethodTab.tsx     # 方法 Tab
        │   │       ├── ExperimentsTab.tsx # 实验 Tab（含 Recharts）
        │   │       ├── CodeTab.tsx       # 代码映射 Tab
        │   │       └── InterviewTab.tsx  # Q&A Tab（翻转卡片）
        │   ├── code/
        │   │   ├── CodeAnalysisPanel.tsx # 代码分析面板
        │   │   ├── CodeViewer.tsx        # 代码查看器（highlight.js）
        │   │   ├── FileBrowser.tsx       # 文件浏览器
        │   │   └── Terminal.tsx          # xterm.js 终端组件
        │   └── library/
        │       └── PaperList.tsx   # 论文库列表
        ├── pages/
        │   ├── HomePage.tsx        # 首页（上传 + 最近论文）
        │   ├── ReaderPage.tsx      # 论文精读（PDF + 解析结果）
        │   ├── CodePage.tsx        # 代码分析（三栏布局）
        │   ├── LibraryPage.tsx     # 论文库
        │   └── SettingsPage.tsx    # 配置页（AI 模型 + UI 设置）
        └── styles/
            └── globals.css         # Tailwind + 自定义样式
```

---

## 一、数据结构规范

### 1.1 Paper（数据库模型）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int (PK) | 自增主键 |
| filename | str | 原始文件名 |
| pdf_path | str | 服务器存储路径 |
| file_size | int | 文件大小（字节） |
| title | str? | AI 提取的标题（解析后填充） |
| venue | str? | 会议/期刊 |
| year | int? | 发表年份 |
| status | str | `pending` / `extracting` / `analyzing` / `done` / `failed` |
| error_message | str? | 失败时的错误信息 |
| code_url | str? | AI 提取的 GitHub/GitLab URL |
| code_status | str | `none` / `cloning` / `ready` / `failed` |
| code_path | str? | 本地 clone 路径 |
| code_summary | str? | AI 生成的 README 摘要 |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

### 1.2 AnalysisResult（数据库模型）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int (PK) | 自增主键 |
| paper_id | int (FK) | 关联 Paper，一对一，**ondelete=CASCADE** |
| title | str | 论文标题 |
| authors | str | 作者列表 |
| venue | str | 会议/期刊 |
| year | int | 发表年份 |
| one_sentence | str | 一句话核心贡献 |
| problem | str | 研究问题 |
| key_contributions | list[str] | 贡献列表（JSON） |
| outline | list[dict] | 论文大纲（JSON，每项对应 OutlineItem） |
| method | dict | 方法论（JSON，对应 MethodModel） |
| experiments | dict | 实验结果（JSON，对应 ExperimentModel） |
| code_mapping | list[dict] | 代码映射（JSON，每项对应 CodeMapping） |
| interview_qa | list[dict] | 面试问答（JSON，每项对应 InterviewQA） |
| limitations | str | 局限性 |
| related_papers | list | 相关论文（JSON，每项对应 RelatedPaper） |
| mermaid_chart | str? | Mermaid 流程图（顶层字段，不在 method 内） |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

### 1.3 CodeAnalysis（数据库模型）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int (PK) | 自增主键 |
| paper_id | int (FK) | 关联 Paper，一对一，**ondelete=CASCADE** |
| file_tree | dict | 文件树结构（JSON） |
| data_flow | str? | AI 生成的数据流描述 |
| dependency_graph | list[dict] | 模块依赖图（JSON，每项: source, target, imports） |
| paper_code_mapping | list[dict] | 论文-代码映射（JSON，每项: concept, file, function, explanation） |
| status | str | `pending` / `analyzing` / `done` / `failed` |
| error_message | str? | 失败时的错误信息 |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

### 1.4 嵌套结构

**MethodModel:**
```json
{
  "overview": "str - 方法总览",
  "pipeline": "str - Step 1: ...\nStep 2: ...",
  "key_modules": [
    {"name": "str", "role": "str", "innovation": "str"}
  ]
}
```

**ExperimentModel:**
```json
{
  "datasets": ["str"],
  "baselines": ["str"],
  "main_results": "str",
  "ablation": "str",
  "result_table": [
    {"method": "str", "dataset": "str", "metric": "str", "value": "str"}
  ],
  "figures": [
    {
      "name": "str - 图表编号，如 Table 1",
      "caption": "str - 图表标题",
      "chart_type": "str - bar/line/scatter/table",
      "columns": ["str"],
      "rows": [["str"]],
      "interpretation": "str - AI 解读"
    }
  ]
}
```

**CodeMapping:**
```json
{
  "concept": "str - 论文概念/公式",
  "likely_implementation": "str - PyTorch 代码思路"
}
```

**InterviewQA:**
```json
{
  "q": "str - 面试问题",
  "a": "str - 参考回答",
  "source": "str - 原文出处，如 Section 3.2, Page 5"
}
```

**OutlineItem:**
```json
{
  "section": "str - 章节标题",
  "page": "str - 页码范围",
  "summary": "str - 内容概述"
}
```

**RelatedPaper:**
```json
{
  "title": "str - 论文标题",
  "authors": "str - 作者列表",
  "venue": "str - 会议/期刊",
  "year": "int - 发表年份",
  "doi": "str - DOI",
  "url": "str - 论文链接",
  "relevance": "str - 与本文的关系",
  "citation_count": "int - 引用数（Semantic Scholar）"
}
```

**AppConfig:**
```json
{
  "translate": {
    "api_key": "str",
    "base_url": "str",
    "model": "str",
    "system_prompt": "str",
    "temperature": 0.1,
    "max_tokens": 4096
  },
  "analysis": {
    "api_key": "str",
    "base_url": "str",
    "model": "str",
    "system_prompt": "str",
    "temperature": 0.1,
    "max_tokens": 4096
  },
  "ui": {
    "highlight_duration": 3000,
    "zoom_step": 0.2,
    "zoom_min": 0.4,
    "zoom_max": 3.0
  }
}
```

---

## 二、API 接口规范

### 2.1 POST /api/papers/upload

上传 PDF 并创建论文记录。

- **Request**: `multipart/form-data`, field: `file` (PDF, max 50MB)
- **Response 200**: `PaperResponse`
- **Error 400**: `{detail, code}` — `FILE_TOO_LARGE` / `INVALID_FORMAT`

### 2.2 GET /api/papers

获取论文列表。

- **Query**: `search?`, `venue?`, `year?`, `page=1`, `per_page=20`
- **Response 200**: `{papers: PaperResponse[], total: int}`

### 2.3 GET /api/papers/{paper_id}

获取单篇论文。

- **Response 200**: `PaperResponse`
- **Error 404**: `NOT_FOUND`

### 2.4 DELETE /api/papers/{paper_id}

删除论文（级联删除 AnalysisResult、CodeAnalysis，删除 PDF 文件和代码仓库目录）。

- **Response 200**: `{message: "Paper deleted successfully"}`
- **Error 404**: `NOT_FOUND`

### 2.5 GET /api/papers/{paper_id}/export

导出为 Markdown。

- **Response 200**: `text/markdown`, Content-Disposition attachment
- **Error 400**: `NOT_READY` — 论文尚未解析完成

### 2.6 GET /api/papers/{paper_id}/pdf

获取 PDF 文件用于预览。

- **Response 200**: `application/pdf`, Content-Disposition: inline
- **Error 404**: `NOT_FOUND` / `FILE_NOT_FOUND`

### 2.7 GET /api/analysis/{paper_id}

获取完整解析结果。

- **Response 200**: `AnalysisResponse`
- **Error 404**: `NOT_FOUND`
- **Error 400**: `NOT_READY` / `PARSING_FAILED`

### 2.8 GET /api/analysis/{paper_id}/stream

SSE 真流式解析端点。

- **Response**: `text/event-stream`
- **真流式**: 逐 chunk 增量解析 JSON，字段完成即推送
- **事件序列**:
  1. `status` → "正在提取 PDF 文本..."
  2. `status` → "正在分析论文内容..."
  3. `field` × N → 逐字段推送（title, venue, year, ..., mermaid_chart）
  4. `status` → "正在搜索相关论文..."
  5. `field` → related_papers
  6. `done`

- **重连策略**:
  - `status="done"` → 从 DB 读取完整结果，逐字段以 field 事件重发
  - `status="failed"` → 发 error 事件
  - `status="analyzing"/"extracting"` → 重新启动分析
  - `status="pending"` → 启动新分析

### 2.9 POST /api/analysis/{paper_id}/reparse

重新解析。

- **Response 200**: `PaperResponse` (status="pending")

### 2.10 POST /api/analysis/{paper_id}/related-papers

刷新相关论文（通过 Semantic Scholar 搜索）。

- **Response 200**: `RelatedPaper[]`
- **Error 404**: 分析结果不存在
- **Error 400**: 论文标题为空

### 2.11 GET /api/analysis/config

获取全部配置。

- **Response 200**: `AppConfig`

### 2.12 POST /api/analysis/config

保存全部配置。

- **Request**: `AppConfig`
- **Response 200**: `{message: "配置已保存"}`

### 2.13 POST /api/analysis/text-action

对选中文本进行翻译或 AI 总结。

- **Request**: `{text: str, action: "translate" | "summarize"}`
- **Response 200**: `{result: str}`
- **Error 400**: 文本为空 / action 无效 / 未配置 API Key

### 2.14 POST /api/code/{paper_id}/extract-url

重新从论文中提取代码仓库 URL（正则匹配）。

- **Response 200**: `{code_url: str | null}`

### 2.15 POST /api/code/{paper_id}/clone

Clone 论文的代码仓库。

- **Response 200**: `{message: "克隆成功", path: str}`
- **Error 400**: 未找到代码仓库 URL
- **Error 404**: 论文不存在
- **Error 500**: 克隆失败

### 2.16 GET /api/code/{paper_id}/tree

获取代码仓库的文件树。

- **Response 200**: `FileNode`（递归结构: name, type, path, children, size）
- **Error 400**: 代码仓库未就绪

### 2.17 GET /api/code/{paper_id}/file

获取代码文件内容。

- **Query**: `path` — 文件相对路径
- **Response 200**: `{path: str, content: str}` 或 `{path: str, cells: list}`（.ipynb 文件）
- **Error 404**: 文件不存在

### 2.18 GET /api/code/{paper_id}/analyze

触发代码分析（SSE 流式）。

- **Response**: `text/event-stream`
- **事件序列**:
  1. `status` → "正在分析文件结构..."
  2. `status` → "正在分析模块依赖..."
  3. `status` → "正在读取核心文件..."
  4. `status` → "AI 正在分析数据流..."
  5. `status` → "AI 正在映射论文与代码..."
  6. `status` → "正在保存分析结果..."
  7. `done`

### 2.19 GET /api/code/{paper_id}/analysis

获取代码分析结果。

- **Response 200**: `CodeAnalysisResponse`
- **Error 404**: 代码分析结果不存在

### 2.20 GET /api/code/{paper_id}/summary

获取代码摘要（侧边栏用）。

- **Response 200**: `CodeSummaryResponse` — `{code_url, code_status, code_summary}`

### 2.21 GET /api/logs/stream

SSE 端点：实时推送后端日志。

- **Response**: `text/event-stream`

### 2.22 WebSocket /api/terminal/ws

WebSocket PTY 终端。

- **Query**: `cwd` — 工作目录（可选，默认为当前目录）

### 2.23 POST /api/system/open-vscode

打开 VSCode。

- **Request**: `{path: str}`
- **Response 200**: `{message: str}` 或 `{error: str}`

---

## 三、前端页面规范

### 3.1 路由

| 路径 | 页面 | 说明 |
|------|------|------|
| `/` | HomePage | 上传入口 + 最近论文 |
| `/reader/:id` | ReaderPage | PDF 预览 + 解析结果 |
| `/code/:id` | CodePage | 代码分析（三栏布局）|
| `/library` | LibraryPage | 论文库管理 |
| `/settings` | SettingsPage | AI 模型 + UI 配置 |

### 3.2 Layout 结构

```
┌─────────────────────────────────────────┐
│  一级导航栏: Logo | 首页 · 论文库 · 配置   │
├─────────────────────────────────────────┤
│  二级导航栏（查看论文时）:                  │
│  论文标题 | 论文分析 · 源码分析              │
├─────────────────────────────────────────┤
│                                         │
│              页面内容区                   │
│                                         │
├─────────────────────────────────────────┤
│  底部终端面板: 后端日志 | 前端日志 | 控制台   │
└─────────────────────────────────────────┘
```

### 3.3 ReaderPage 布局

```
┌─────────────────────────────────────────┐
│  顶部导航栏                               │
├──────────────┬──────────────────────────┤
│              │  Tab: 概览│大纲│方法│实验│  │
│  Sidebar     │       代码│Q&A           │
│  (折叠式)     ├──────────────────────────┤
│              │                          │
│  PDF 预览     │  解析结果内容区             │
│  (可拖拽)     │  (对应 Tab 的组件)         │
│              │                          │
└──────────────┴──────────────────────────┘
```

- 左侧面板宽度可拖拽调整，默认 35%，范围 20%-60%
- Sidebar 为折叠式覆盖层（固定宽度 256px）
- PDF 选中文本弹出 SelectionPopup（翻译/AI 总结）

### 3.4 CodePage 布局

```
┌─────────────────────────────────────────┐
│  顶部导航栏                               │
├────────┬───────────────┬────────────────┤
│        │               │                │
│ File   │  Code Viewer  │  Analysis      │
│ Browser│  (highlight)  │  Panel         │
│        │               │  (dataflow,    │
│        │               │   dep graph,   │
│        │               │   mapping)     │
│        │               │                │
└────────┴───────────────┴────────────────┘
```

- 三栏布局：文件浏览器 + 代码查看器 + 分析面板
- 支持 Jupyter Notebook (.ipynb) 渲染

### 3.5 Tab 内容

| Tab | 组件 | 内容 |
|-----|------|------|
| 概览 | OverviewTab | 标题、venue、one_sentence、problem、contributions、related_papers、limitations |
| 大纲 | OutlineTab | 论文大纲列表，支持点击章节跳转 PDF 对应页面 |
| 方法 | MethodTab | method.overview、Mermaid 流程图、pipeline 步骤、key_modules 表格 |
| 实验 | ExperimentsTab | datasets 标签、baselines、main_results、Recharts(result_table)、figures 逐图分析、ablation |
| 代码 | CodeTab | code_mapping 卡片列表 |
| Q&A | InterviewTab | 翻转卡片网格（Q→A），min-h-48 + overflow-auto |

### 3.6 流式更新（真流式）

- 上传后立即连接 SSE (`/api/analysis/{id}/stream`)
- `useStreaming(paperId: number | null, shouldConnect: boolean = true)` hook 管理连接
  - paperId 为 null 时不连接（idle）
  - shouldConnect 为 false 时不连接
  - 在组件顶层无条件调用（遵守 React 规则）
- 每收到一个 `field` 事件，更新 `partialResult`，该字段立即渲染
- 连接断开自动重连（最多 3 次，间隔 1s/2s/4s）
- 重连时根据 paper.status 决定行为（见 2.8）

---

## 四、UI 设计规范

- **主题**: 深色（bg-gray-950, text-gray-100）
- **字体**: Inter / 系统字体
- **卡片**: bg-gray-800, border-gray-700, rounded-lg
- **主色**: blue-500 (#3b82f6)
- **成功**: green-500
- **警告**: yellow-500
- **错误**: red-500
- **滚动条**: 自定义深色样式
- **响应式**: 移动端 ReaderPage 上下布局

---

## 五、开发约定

1. **类型**: 前后端类型严格对应（后端使用强类型 Schema，非 dict）
2. **错误处理**: API 统一返回 `{detail, code}` 格式
3. **数据库**: SQLite，开发环境文件存储在 `./data/essay_read.db`
4. **文件上传**: 存储在 `./uploads/` 目录，文件名使用 UUID
5. **级联删除**: AnalysisResult 和 CodeAnalysis 均设置 `ondelete=CASCADE`
6. **PDF 文本**: 超过 100k 字符时智能截断（保留前3页+最后1页）
7. **AI 调用**: 使用 Anthropic SDK，通过配置页管理 API Key/Base URL/Model
8. **SSE 代理**: Vite proxy 配置禁用 SSE 缓冲
9. **代码仓库**: clone 到 `./code_downloads/{paper_id}/` 目录
10. **配置存储**: AI 配置存储在 `./data/ai_config.json`

---

## 六、依赖清单

### 后端（requirements.txt）

| 包 | 用途 |
|----|------|
| fastapi | Web 框架 |
| uvicorn[standard] | ASGI 服务器 |
| python-multipart | 文件上传 |
| pydantic-settings | 配置管理 |
| sqlmodel | ORM（SQLAlchemy + Pydantic）|
| pymupdf | PDF 文本提取（主路径）|
| pypdf | PDF 文本提取（后备路径）|
| anthropic | Anthropic SDK（MiMo API）|
| httpx | HTTP 客户端（Semantic Scholar）|

### 前端（package.json）

| 包 | 用途 |
|----|------|
| react / react-dom | UI 框架 |
| react-router-dom | 路由 |
| react-pdf | PDF 渲染 |
| recharts | 图表可视化 |
| mermaid | 流程图渲染 |
| react-markdown / remark-gfm | Markdown 渲染 |
| highlight.js | 代码高亮 |
| @xterm/xterm / @xterm/addon-fit | 终端模拟 |
| @tailwindcss/typography | Tailwind 排版插件 |
| tailwindcss / autoprefixer / postcss | CSS 工具链 |
| vite / @vitejs/plugin-react | 构建工具 |
| typescript | 类型系统 |
