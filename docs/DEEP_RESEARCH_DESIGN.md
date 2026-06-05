# 深度研究 Agent 系统 — 技术设计文档

## 一、概述

### 1.1 目标

在现有"浅分析"（单次 prompt → JSON）基础上，新增"深度研究"模式。深度研究使用 Anthropic SDK 的 Tool Use 功能实现多轮 Agent 循环，能够自主搜索论文、阅读代码、交叉验证，产出结构化的研究报告和研究脉络树。

### 1.2 设计原则

- **不破坏现有功能**：浅分析保持不变，深度研究是独立的增强层
- **复用优先**：最大化复用现有基础设施（API 客户端、SSE 流、配置管理、Semantic Scholar）
- **渐进式**：先实现核心 Agent 循环，后续可扩展更多工具
- **可观测**：前端实时展示 Agent 的每一步工具调用和思考过程

---

## 二、技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| **Agent 框架** | Anthropic SDK Tool Use | 原生 tool use，不引入 LangChain 等第三方框架 |
| **LLM 调用** | `AsyncAnthropic.messages.create(tools=...)` | 支持 tool_use block 的多轮对话 |
| **论文搜索** | Semantic Scholar API | 复用现有 `semantic_scholar.py` |
| **PDF 阅读** | PyMuPDF | 复用现有 `pdf_parser.py` |
| **代码阅读** | 直接文件读取 | 复用现有 `code_analyzer.read_file_content` |
| **流式推送** | SSE (Server-Sent Events) | 复用现有 `StreamEvent` + `format_sse` |
| **数据持久化** | SQLModel + SQLite | 复用现有 ORM 模式 |
| **前端渲染** | React + Mermaid + Markdown | 复用现有组件 |

---

## 三、系统架构

### 3.1 整体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        前端 (React)                          │
│                                                             │
│  AnalysisPanel                                              │
│  ┌─────────┬─────────┬─────────┬─────────┬───────────────┐ │
│  │ 概览    │ 大纲    │ 方法    │ ...     │ 深度研究 ⚡   │ │
│  └─────────┴─────────┴─────────┴─────────┴───────┬───────┘ │
│                                                   │         │
│  DeepResearchTab                                   │         │
│  ┌─────────────────────────────────────────────────┐       │
│  │ [研究脉络树 Mermaid]                             │       │
│  │ [工具调用日志 - 可折叠]                           │       │
│  │ [最终研究报告 Markdown]                           │       │
│  └─────────────────────────────────────────────────┘       │
└─────────────────────────┬───────────────────────────────────┘
                          │ SSE (EventSource)
                          │ GET /api/research/{id}/deep
┌─────────────────────────▼───────────────────────────────────┐
│                     后端 (FastAPI)                           │
│                                                             │
│  routers/research.py                                        │
│  ┌─────────────────────────────────────────────────┐       │
│  │ GET /api/research/{paper_id}/deep               │       │
│  │ GET /api/research/{paper_id}                    │       │
│  └──────────────────────┬──────────────────────────┘       │
│                         │                                    │
│  services/deep_research.py                                  │
│  ┌──────────────────────▼──────────────────────────┐       │
│  │              Agent Loop (核心)                    │       │
│  │                                                  │       │
│  │  while turn < MAX_TURNS:                         │       │
│  │    response = client.messages.create(             │       │
│  │      tools=TOOLS, messages=history                │       │
│  │    )                                             │       │
│  │    if stop_reason == "tool_use":                  │       │
│  │      for each tool_call:                         │       │
│  │        result = execute_tool(name, args)          │       │
│  │        yield SSE(tool_call, tool_result)          │       │
│  │      append tool_results → continue               │       │
│  │    else:                                         │       │
│  │      yield SSE(final_report) → break              │       │
│  └──────────────────────┬──────────────────────────┘       │
│                         │                                    │
│  ┌──────────────────────▼──────────────────────────┐       │
│  │              工具执行层                           │       │
│  │                                                  │       │
│  │  search_papers    → semantic_scholar.py           │       │
│  │  search_citations → semantic_scholar.py (S2 API)  │       │
│  │  read_pdf_section → pdf_parser.py                 │       │
│  │  read_code_file   → code_analyzer.py              │       │
│  │  save_note        → 内存 list → 最终存 DB         │       │
│  └─────────────────────────────────────────────────┘       │
│                                                             │
│  models/research.py                                         │
│  ┌─────────────────────────────────────────────────┐       │
│  │ DeepResearchResult (SQLite)                      │       │
│  │ - paper_id, status, research_tree,               │       │
│  │   conversation_log, summary, tool_calls_count    │       │
│  └─────────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 与现有系统的关系

```
浅分析（现有）                    深度研究（新增）
─────────────                    ──────────────
单次 prompt → JSON               多轮 Agent → 工具调用 → 报告
/api/analysis/{id}/stream        /api/research/{id}/deep
结果存 AnalysisResult             结果存 DeepResearchResult
前端 Tab 展示                     新增"深度研究"Tab 展示

共用：_create_client, semantic_scholar, pdf_parser, code_analyzer,
      format_sse, get_analysis_config, StreamEvent
```

---

## 四、项目结构

### 4.1 新增文件

```
backend/
├── models/
│   └── research.py              # DeepResearchResult 数据库模型
├── routers/
│   └── research.py              # /api/research/* 路由
├── services/
│   └── deep_research.py         # Agent 循环核心 + 工具定义 + 工具执行

frontend/src/
├── api/
│   └── research.ts              # 深度研究 API 调用
├── features/analysis/tabs/
│   └── DeepResearchTab.tsx       # 深度研究结果展示 Tab
```

### 4.2 修改文件

```
backend/main.py                  # 注册 research router
frontend/src/features/analysis/AnalysisPanel.tsx  # 添加"深度研究"Tab
frontend/src/types/index.ts      # 添加 DeepResearchResult 类型
```

---

## 五、API 设计

### 5.1 端点列表

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/research/{paper_id}/deep` | SSE 流式启动深度研究 |
| `GET` | `/api/research/{paper_id}` | 获取已有深度研究结果 |
| `DELETE` | `/api/research/{paper_id}` | 删除深度研究结果（重新研究） |

### 5.2 GET /api/research/{paper_id}/deep

**触发深度研究 Agent 运行，通过 SSE 实时推送进度。**

**前置条件**：论文必须已完成浅分析（status=done）

**SSE 事件类型**：

| event | data 结构 | 说明 |
|-------|-----------|------|
| `status` | `{"message": "正在初始化 Agent..."}` | 进度状态 |
| `thinking` | `{"content": "我需要先搜索这篇论文的引用..."}` | Agent 思考过程 |
| `tool_call` | `{"tool": "search_papers", "args": {"query": "..."}, "turn": 3}` | 工具调用 |
| `tool_result` | `{"tool": "search_papers", "result": [...], "turn": 3}` | 工具返回 |
| `field` | `{"field_name": "research_tree", "value": {...}}` | 最终报告字段 |
| `done` | `{"message": "深度研究完成", "tool_calls_count": 12}` | 完成 |
| `error` | `{"message": "Agent 运行失败: ..."}` | 失败 |

### 5.3 GET /api/research/{paper_id}

**Response 200**:
```json
{
  "paper_id": 1,
  "status": "done",
  "research_tree": {
    "parents": [
      {"title": "Attention Is All You Need", "relevance": "基础架构", "year": 2017}
    ],
    "derivatives": [
      {"title": "DeiT", "relevance": "数据增强改进", "year": 2021}
    ],
    "baselines": [
      {"title": "ResNet-50", "relevance": "对比基线", "year": 2016}
    ]
  },
  "summary": "## 研究脉络\n\n本文基于 Transformer 架构...",
  "tool_calls_count": 12,
  "created_at": "2026-06-05T10:00:00Z"
}
```

### 5.4 重连策略

与浅分析 SSE 相同：
- `status=done` → 从 DB 读取结果重发
- `status=running` → 重新启动 Agent
- `status=pending` → 启动新的深度研究
- `status=failed` → 发 error 事件

---

## 六、数据模型

### 6.1 DeepResearchResult

```python
class DeepResearchResult(SQLModel, table=True):
    __tablename__ = "deep_research_results"

    id: Optional[int] = Field(default=None, primary_key=True)
    paper_id: int = Field(ForeignKey("papers.id", ondelete="CASCADE"), unique=True)

    # 状态
    status: str = "pending"  # pending | running | done | failed
    error_message: Optional[str] = None

    # 研究脉络树（JSON）
    research_tree: dict = Field(default={}, sa_column=Column(JSON))
    # {
    #   "parents": [{"title", "paper_id", "relevance", "year", "venue"}],
    #   "derivatives": [{"title", "paper_id", "relevance", "year", "venue"}],
    #   "baselines": [{"title", "paper_id", "relevance", "year", "venue"}]
    # }

    # Agent 对话记录（JSON）
    conversation_log: list = Field(default=[], sa_column=Column(JSON))
    # [{"role": "assistant"|"user", "content": "...", "tool_calls": [...]}]

    # 最终研究报告（Markdown）
    summary: str = ""

    # 元数据
    tool_calls_count: int = 0
    turns_used: int = 0

    # 时间戳
    created_at: datetime
    updated_at: datetime
```

### 6.2 数据库迁移

在 `database.py` 的 `_migrate_schema` 中无需额外迁移，因为是新表，`create_all` 会自动创建。

---

## 七、Agent 核心设计

### 7.1 System Prompt

```
你是一个学术研究助手，负责对论文进行深度研究分析。

你的任务：
1. 深入理解论文的核心贡献和方法论
2. 搜索这篇论文的学术脉络：
   - 它基于哪些工作（parents/baselines）
   - 哪些后续工作引用了它（derivatives）
   - 它与哪些方法进行了对比（baselines）
3. 如果有代码仓库，阅读关键代码验证论文描述的准确性
4. 整理研究发现，输出结构化报告

你可以使用以下工具：
- search_papers: 通过关键词搜索学术论文
- search_citations: 查询某篇论文的引用/被引用关系
- read_pdf_section: 阅读论文 PDF 的特定页面
- read_code_file: 阅读代码仓库中的文件
- save_note: 保存一条研究发现

工作流程：
1. 先阅读论文的摘要和结论，理解核心贡献
2. 搜索论文的 references（它引用了哪些工作）找到 parents
3. 搜索论文的 citations（哪些工作引用了它）找到 derivatives
4. 对重要的 parent/derivative 论文，进一步了解它们与本文的关系
5. 如果有代码，阅读核心实现文件验证方法描述
6. 整理所有发现，输出最终报告

注意：
- 每次工具调用要有明确目的，不要盲目搜索
- 对搜索结果进行筛选，只保留与本文最相关的论文
- 研究脉络树中每个节点都需要说明与本文的关系
```

### 7.2 工具定义

```python
TOOLS = [
    {
        "name": "search_papers",
        "description": "通过 Semantic Scholar 搜索学术论文。返回论文标题、作者、年份、引用数、摘要。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，可以是论文标题、作者名、或研究主题"
                },
                "limit": {
                    "type": "integer",
                    "description": "返回结果数量，默认 5",
                    "default": 5
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "search_citations",
        "description": "查询某篇论文的引用关系。direction='references' 返回它引用的论文，direction='citations' 返回引用它的论文。",
        "input_schema": {
            "type": "object",
            "properties": {
                "paper_title": {
                    "type": "string",
                    "description": "论文标题（用于搜索匹配）"
                },
                "direction": {
                    "type": "string",
                    "enum": ["citations", "references"],
                    "description": "citations=谁引用了它，references=它引用了谁"
                },
                "limit": {
                    "type": "integer",
                    "default": 10
                }
            },
            "required": ["paper_title", "direction"]
        }
    },
    {
        "name": "read_pdf_section",
        "description": "读取论文 PDF 的特定页面范围。返回该范围内的文本内容。",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_page": {
                    "type": "integer",
                    "description": "起始页码（从 1 开始）"
                },
                "end_page": {
                    "type": "integer",
                    "description": "结束页码（包含）"
                }
            },
            "required": ["start_page", "end_page"]
        }
    },
    {
        "name": "read_code_file",
        "description": "读取代码仓库中的文件内容。仅在论文有代码仓库时可用。",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "文件相对路径，如 'models/backbone.py'"
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "save_note",
        "description": "保存一条研究发现。用于记录中间结果，最终会整合到报告中。",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["parent", "derivative", "baseline", "method", "finding"],
                    "description": "发现类别"
                },
                "title": {
                    "type": "string",
                    "description": "相关论文标题或发现标题"
                },
                "content": {
                    "type": "string",
                    "description": "详细内容"
                }
            },
            "required": ["category", "title", "content"]
        }
    }
]
```

### 7.3 Agent 循环流程

```
┌─────────────────────────────────────────────┐
│ 初始化                                       │
│ - 加载论文信息（title, pdf_path, code_path） │
│ - 构建初始 prompt（论文摘要 + 全文前 10k 字）  │
│ - 创建 Anthropic 客户端                      │
└──────────────────┬──────────────────────────┘
                   ▼
┌─────────────────────────────────────────────┐
│ Turn N (N = 1..MAX_TURNS=15)                │
│                                             │
│  1. 调用 client.messages.create(             │
│       model=model,                           │
│       system=SYSTEM_PROMPT,                  │
│       messages=conversation_history,         │
│       tools=TOOLS,                           │
│       max_tokens=4096                        │
│     )                                        │
│                                             │
│  2. 检查 stop_reason:                        │
│                                             │
│     ┌─ "tool_use" ──────────────────────┐   │
│     │ for each content block:           │   │
│     │   if block.type == "tool_use":    │   │
│     │     result = execute_tool(        │   │
│     │       block.name,                 │   │
│     │       block.input                 │   │
│     │     )                             │   │
│     │     yield SSE("tool_call", ...)   │   │
│     │     yield SSE("tool_result", ...) │   │
│     │                                   │   │
│     │ append assistant message          │   │
│     │ append tool_results message       │   │
│     │ → continue to Turn N+1            │   │
│     └───────────────────────────────────┘   │
│                                             │
│     ┌─ "end_turn" ─────────────────────┐   │
│     │ 提取最终文本响应                    │   │
│     │ 解析研究脉络树                      │   │
│     │ yield SSE("field", research_tree) │   │
│     │ yield SSE("field", summary)       │   │
│     │ yield SSE("done")                 │   │
│     │ → 结束循环                         │   │
│     └───────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

### 7.4 工具执行函数

```python
async def execute_tool(
    tool_name: str,
    tool_input: dict,
    paper_id: int,
    pdf_text: str,
    code_path: Optional[str],
    notes: list,
) -> str:
    """执行单个工具调用，返回结果字符串"""

    if tool_name == "search_papers":
        results = await search_related_papers(
            tool_input["query"],
            limit=tool_input.get("limit", 5)
        )
        return json.dumps(results, ensure_ascii=False)

    elif tool_name == "search_citations":
        # 调用 S2 API 的 citations/references endpoint
        results = await search_citations(
            tool_input["paper_title"],
            tool_input["direction"],
            tool_input.get("limit", 10)
        )
        return json.dumps(results, ensure_ascii=False)

    elif tool_name == "read_pdf_section":
        pages = pdf_text.split("\n--- Page ")
        start = tool_input["start_page"] - 1
        end = tool_input["end_page"]
        section = "\n".join(pages[start:end])
        return section[:8000]  # 限制长度

    elif tool_name == "read_code_file":
        if not code_path:
            return "错误：该论文没有代码仓库"
        content = read_file_content(code_path, tool_input["file_path"])
        return content[:10000]  # 限制长度

    elif tool_name == "save_note":
        notes.append({
            "category": tool_input["category"],
            "title": tool_input["title"],
            "content": tool_input["content"],
        })
        return f"已保存 {tool_input['category']} 类型的笔记: {tool_input['title']}"

    return f"未知工具: {tool_name}"
```

---

## 八、数据流

### 8.1 深度研究完整数据流

```
用户点击"深度研究"
       │
       ▼
前端: EventSource(`/api/research/${paperId}/deep`)
       │
       ▼
后端: stream_deep_research(paper_id)
       │
       ├─ 1. 查询论文信息（title, pdf_path, code_path）
       ├─ 2. 提取 PDF 全文（复用 pdf_parser）
       ├─ 3. 更新状态为 "running"
       │
       ▼
   Agent Loop（多轮）
       │
       ├─ Turn 1: AI 思考 → 调用 search_papers("论文标题")
       │   └─ SSE: tool_call → tool_result
       │
       ├─ Turn 2: AI 分析搜索结果 → 调用 search_citations("论文标题", "references")
       │   └─ SSE: tool_call → tool_result
       │
       ├─ Turn 3: AI 发现重要 baseline → 调用 read_pdf_section(2, 3) 读相关章节
       │   └─ SSE: tool_call → tool_result
       │
       ├─ Turn 4: AI 找到代码 → 调用 read_code_file("model.py")
       │   └─ SSE: tool_call → tool_result
       │
       ├─ Turn 5: AI 保存发现 → 调用 save_note("parent", "Transformer", "...")
       │   └─ SSE: tool_call → tool_result
       │
       ├─ ...（更多轮次）
       │
       └─ Turn N: AI 完成分析 → 输出最终报告
           └─ SSE: field(research_tree) → field(summary) → done
       │
       ▼
后端: 保存 DeepResearchResult 到 SQLite
       │
       ▼
前端: 渲染研究脉络树 + 工具调用日志 + 最终报告
```

### 8.2 前端状态管理

```
DeepResearchTab
├── status: 'idle' | 'connecting' | 'running' | 'done' | 'failed'
├── progressMessage: string          // SSE status 事件
├── toolCalls: ToolCallLog[]         // SSE tool_call + tool_result 事件
├── researchTree: ResearchTree | null // SSE field 事件
├── summary: string                  // SSE field 事件
└── error: string | null             // SSE error 事件

ToolCallLog {
  turn: number
  tool: string
  args: object
  result: string
  timestamp: number
}
```

---

## 九、错误处理与限制

### 9.1 安全限制

| 限制项 | 值 | 说明 |
|--------|-----|------|
| 最大轮次 | 15 | 防止无限循环 |
| 单次工具结果长度 | 8000-10000 字符 | 防止上下文溢出 |
| 总上下文窗口 | 保留最近 20 轮消息 | 超出时截断早期消息 |
| 工具调用总次数 | 50 | 防止滥用 |

### 9.2 错误处理

- **API 调用失败**: 复用现有重试机制（指数退避，最多 5 次）
- **工具执行失败**: 返回错误字符串给 Agent，让其自行调整策略
- **Agent 卡死**: 超时保护（单轮 60s，总计 10min）
- **上下文溢出**: 截断早期消息，保留系统 prompt + 最近 N 轮

### 9.3 降级策略

- 如果 Tool Use 不被模型支持，降级为单次 prompt 分析
- 如果 Semantic Scholar 不可用，跳过论文搜索，仅分析 PDF 内容
- 如果代码仓库不可用，跳过代码分析，仅做论文分析

---

## 十、前端 UI 设计

### 10.1 AnalysisPanel Tab 扩展

```
┌──────────────────────────────────────────────────────┐
│ 概览 │ 大纲 │ 方法 │ 实验 │ 代码 │ Q&A │ 深度研究 ⚡ │
└──────────────────────────────────────────────────────┘
```

- "深度研究" Tab 旁显示闪电图标
- 如果论文未完成浅分析，Tab 灰色不可点击
- 如果已完成浅分析但未进行深度研究，显示"开始深度研究"按钮
- 如果已完成深度研究，显示结果

### 10.2 DeepResearchTab 布局

```
┌──────────────────────────────────────────────────────┐
│ [研究脉络树 - Mermaid]                                │
│                                                      │
│  ┌─────────┐     ┌─────────────┐     ┌────────────┐ │
│  │Parent   │────▶│ This Paper  │────▶│ Derivative │ │
│  │Paper A  │     │ (当前论文)   │     │ Paper B    │ │
│  └─────────┘     └─────────────┘     └────────────┘ │
│                                                      │
├──────────────────────────────────────────────────────┤
│ [工具调用日志 - 可折叠]                                │
│                                                      │
│ ▶ Turn 1: search_papers("论文标题")                   │
│   └─ 找到 5 篇相关论文                                │
│ ▶ Turn 2: search_citations("论文标题", "references")  │
│   └─ 找到 23 篇引用论文                               │
│ ▶ Turn 3: read_pdf_section(2, 3)                     │
│   └─ 读取第 2-3 页方法论章节                          │
│ ...                                                  │
├──────────────────────────────────────────────────────┤
│ [最终研究报告 - Markdown]                              │
│                                                      │
│ ## 研究脉络                                          │
│                                                      │
│ 本文基于 Transformer 架构 (Vaswani et al., 2017)...   │
│                                                      │
│ ## Baseline 来源                                     │
│ - ResNet-50 (He et al., 2016): 作为视觉特征提取器...  │
│ - ViT (Dosovitskiy et al., 2020): 首次将 Transformer │
│   应用于图像分类...                                   │
│                                                      │
│ ## 后续工作                                          │
│ - DeiT (Touvron et al., 2021): 引入知识蒸馏...        │
│ - BEiT (Bao et al., 2021): 提出掩码图像建模...        │
└──────────────────────────────────────────────────────┘
```

### 10.3 运行中的 UI

```
┌──────────────────────────────────────────────────────┐
│ 🔄 深度研究进行中... (Turn 5/15)                      │
│                                                      │
│ [实时工具调用流]                                      │
│                                                      │
│ 🔍 Turn 1: 搜索相关论文... ✓ 找到 5 篇               │
│ 🔍 Turn 2: 查询引用关系... ✓ 找到 23 篇引用           │
│ 📖 Turn 3: 阅读论文第 2-3 页... ✓ 已读取              │
│ 💻 Turn 4: 阅读 model.py... ✓ 已读取                  │
│ 🔍 Turn 5: 搜索 "DeiT citations"... ⏳ 进行中         │
│                                                      │
│ [Agent 思考]                                          │
│ "我需要进一步了解 DeiT 这篇论文，它是本文最直接..."     │
└──────────────────────────────────────────────────────┘
```

---

## 十一、实施计划

### Phase 1: 核心 Agent 循环（后端）

1. 创建 `backend/models/research.py` — 数据模型
2. 创建 `backend/services/deep_research.py` — Agent 循环 + 工具定义
3. 创建 `backend/routers/research.py` — API 端点
4. 注册路由到 `main.py`

### Phase 2: 前端集成

5. 创建 `frontend/src/api/research.ts` — API 调用
6. 创建 `frontend/src/features/analysis/tabs/DeepResearchTab.tsx` — 结果展示
7. 修改 `AnalysisPanel.tsx` — 添加深度研究 Tab
8. 修改 `types/index.ts` — 添加类型定义

### Phase 3: 验证与调优

9. 端到端测试：选择一篇已解析论文触发深度研究
10. 调优 System Prompt 和工具参数
11. 优化前端展示效果
