# 论文精读助手 — 完整规范文档

## 项目文件树

```
essay_read/
├── .env.example                    # 环境变量模板
├── README.md                       # 项目说明
├── docs/
│   └── SPECIFICATIONS.md           # 本文档
│
├── backend/
│   ├── main.py                     # FastAPI 应用入口
│   ├── config.py                   # 配置管理（pydantic-settings）
│   ├── requirements.txt            # Python 依赖
│   ├── models/
│   │   ├── __init__.py             # 模型导出
│   │   ├── paper.py                # Paper 数据库模型
│   │   ├── analysis.py             # AnalysisResult 数据库模型
│   │   └── schemas.py              # API 请求/响应 Schema
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── papers.py               # /api/papers/* 路由
│   │   └── analysis.py             # /api/analysis/* 路由
│   ├── services/
│   │   ├── __init__.py
│   │   ├── pdf_parser.py           # PDF 文本提取
│   │   ├── ai_analyzer.py          # MiMo API 调用
│   │   └── database.py             # 数据库 CRUD
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
        │   ├── client.ts           # fetch 封装
        │   └── papers.ts           # 论文 API 调用
        ├── hooks/
        │   ├── useStreaming.ts     # SSE 流式 hook（accepts null paperId）
        │   └── useAnalysis.ts      # 解析生命周期 hook（顶层调用 useStreaming）
        ├── components/
        │   ├── Layout.tsx          # 全局布局
        │   ├── Sidebar.tsx         # 论文列表侧边栏
        │   └── LoadingState.tsx    # 加载状态
        ├── features/
        │   ├── upload/
        │   │   └── UploadZone.tsx  # PDF 上传区
        │   ├── viewer/
        │   │   └── PdfViewer.tsx   # PDF 预览
        │   ├── analysis/
        │   │   ├── AnalysisPanel.tsx
        │   │   ├── FlowChart.tsx   # Mermaid 流程图
        │   │   └── tabs/
        │   │       ├── OverviewTab.tsx
        │   │       ├── MethodTab.tsx
        │   │       ├── ExperimentsTab.tsx
        │   │       ├── CodeTab.tsx
        │   │       └── InterviewTab.tsx
        │   └── library/
        │       └── PaperList.tsx   # 论文库列表
        ├── pages/
        │   ├── HomePage.tsx
        │   ├── ReaderPage.tsx
        │   └── LibraryPage.tsx
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
| created_at | datetime | 创建时间（server_default=now()） |
| updated_at | datetime | 更新时间（server_default=now(), onupdate=now()） |

### 1.2 AnalysisResult（数据库模型）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int (PK) | 自增主键 |
| paper_id | int (FK) | 关联 Paper，一对一，**ondelete=CASCADE** |
| title | str | 论文标题 |
| venue | str | 会议/期刊 |
| year | int | 发表年份 |
| one_sentence | str | 一句话核心贡献 |
| problem | str | 研究问题 |
| key_contributions | list[str] | 贡献列表（JSON） |
| method | dict | 方法论（JSON） |
| experiments | dict | 实验结果（JSON） |
| code_mapping | list[dict] | 代码映射（JSON） |
| interview_qa | list[dict] | 面试问答（JSON） |
| limitations | str | 局限性 |
| related_papers | list[str] | 相关论文（JSON） |
| mermaid_chart | str? | Mermaid 流程图 |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

### 1.3 嵌套结构

**MethodModel:**
```json
{
  "overview": "str - 方法总览",
  "pipeline": "str - Step 1: ...\nStep 2: ...",
  "key_modules": [
    {"name": "str", "role": "str", "innovation": "str"}
  ],
  "mermaid_chart": "str - Mermaid graph TD 语法"
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
  ]
}
```

**ResultTableRow:**
```json
{
  "method": "方法名称（如 Ours, ResNet-50）",
  "dataset": "数据集名称（如 COCO, ImageNet）",
  "metric": "指标名称（如 mAP, BLEU, FID）",
  "value": "数值（字符串，方便标注如 52.3*）"
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
  "a": "str - 参考回答"
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

删除论文（级联删除 AnalysisResult）。

- **Response 200**: `{message: "Paper deleted successfully"}`
- **Error 404**: `NOT_FOUND`

### 2.5 GET /api/papers/{paper_id}/export

导出为 Markdown。

- **Response 200**: `text/markdown`, Content-Disposition attachment

### 2.6 GET /api/papers/{paper_id}/pdf

获取 PDF 文件用于预览。

- **Response 200**: `application/pdf`, Content-Disposition: inline
- **Error 404**: `NOT_FOUND`
- **用途**: 前端 PdfViewer 通过此 URL 加载 PDF

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
  1. `status` → "正在分析论文内容..."
  2. `field` × N → 逐字段推送（title, venue, year, ..., mermaid_chart）
  3. `done`

- **重连策略**:
  - `status="done"` → 从 DB 读取完整结果，逐字段以 field 事件重发
  - `status="failed"` → 发 error 事件
  - `status="analyzing"/"extracting"` → 重新启动分析
  - `status="pending"` → 启动新分析

### 2.9 POST /api/analysis/{paper_id}/reparse

重新解析。

- **Response 200**: `PaperResponse` (status="pending")

---

## 三、前端页面规范

### 3.1 路由

| 路径 | 页面 | 说明 |
|------|------|------|
| `/` | HomePage | 上传入口 + 最近论文 |
| `/reader/:id` | ReaderPage | PDF 预览 + 解析结果 |
| `/library` | LibraryPage | 论文库管理 |

### 3.2 ReaderPage 布局

```
┌─────────────────────────────────────────┐
│  顶部导航栏                               │
├──────────────┬──────────────────────────┤
│              │  Tab: 概览│方法│实验│代码│面试 │
│  Sidebar     ├──────────────────────────┤
│  (论文列表)   │                          │
│              │  解析结果内容区             │
│  PDF 预览     │  (对应 Tab 的组件)         │
│              │                          │
│              │                          │
└──────────────┴──────────────────────────┘
```

- 左侧 40%: Sidebar + PdfViewer
- 右侧 60%: AnalysisPanel (含 Tab)

### 3.3 Tab 内容

| Tab | 组件 | 内容 |
|-----|------|------|
| 概览 | OverviewTab | 标题、venue、one_sentence、problem、contributions、related_papers、limitations |
| 方法 | MethodTab | method.overview、Mermaid 流程图、pipeline 步骤、key_modules 表格 |
| 实验 | ExperimentsTab | datasets 标签、baselines、main_results、**Recharts(result_table)**、ablation |
| 代码 | CodeTab | code_mapping 卡片列表、伪代码框架 |
| 面试 | InterviewTab | 翻转卡片网格（Q→A），min-h-48 + overflow-auto |

### 3.4 流式更新（真流式）

- 上传后立即连接 SSE (`/api/analysis/{id}/stream`)
- `useStreaming(paperId: number | null)` hook 管理连接
  - paperId 为 null 时不连接（idle）
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

1. **后端**: 函数有 docstring 但无实现（`...` 占位），开发时填充
2. **前端**: 组件有 JSDoc 注释但无实现，开发时填充
3. **类型**: 前后端类型严格对应（后端使用强类型 Schema，非 dict）
4. **错误处理**: API 统一返回 `{detail, code}` 格式
5. **数据库**: SQLite，开发环境文件存储在 `./data/essay_read.db`
6. **文件上传**: 存储在 `./uploads/` 目录，文件名使用 UUID
7. **级联删除**: AnalysisResult 设置 `ondelete=CASCADE`，删除 Paper 时自动清理
8. **PDF 文本**: 超过 100k 字符时智能截断（保留前3页+最后1页）
9. **AI 调用**: 单次调用同时输出分析结果和 Mermaid 流程图
10. **SSE 代理**: Vite proxy 配置禁用 SSE 缓冲

---

## 六、审查修正记录

| # | 问题 | 处理 |
|---|------|------|
| 1 | 假流式 | **已修正**: 增量 JSON 解析，字段完成即 yield |
| 2 | Hook 违规 | **已修正**: useStreaming 接受 null，useAnalysis 顶层调用 |
| 3 | 缺 PDF 端点 | **已修正**: 新增 GET /api/papers/{id}/pdf |
| 4 | Recharts 无数据 | **已修正**: experiments 增加 result_table 字段 |
| 5 | 两次 AI 调用 | **已修正**: Mermaid 合并到主 prompt 的 method 中 |
| 6 | 类型不一致 | **已修正**: AnalysisResponse 使用强类型 Model |
| 7 | updated_at 不更新 | **已修正**: 使用 SQLAlchemy onupdate |
| 8 | 重连无机制 | **已修正**: 按 paper.status 决定重连策略 |
| 9 | PDF 长度无策略 | **已修正**: 增加截断策略（MAX_CHARS=100k） |
| 10 | 缺 cascade 删除 | **已修正**: ondelete=CASCADE |
| 11 | 缺时间戳 | **已修正**: AnalysisResult 增加 created_at/updated_at |
| 12 | ExportRequest 未使用 | **已修正**: 删除该 schema |
| 13 | Vite SSE 缓冲 | **已修正**: proxy 配置禁用压缩 |
| 14 | 卡片截断 | **已修正**: h-48 → min-h-48 + overflow-auto |
| 15 | 无日志/认证/限流 | **不修正**: 本地应用，MVP 阶段不需要，后续可加 |
