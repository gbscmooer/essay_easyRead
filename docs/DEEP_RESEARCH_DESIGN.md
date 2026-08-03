# 深度研究 Agent 系统 — 技术设计文档

> 本文档作为开发蓝图，所有路径、表名、字段、函数签名、SSE 事件结构都与现有代码对齐，**不引入 mock 数据，直接接入真实表 / 真实 API**。
>
> **v3.0.0 起，文中关于"相似边"的部分已过时**：`similarity_threshold` 阈值连边、
> `similar` 边类型、可切换的 similarity provider 都已删除。现行做法是唯一一套
> 「论文关联度」算法（`graphkit/relatedness.py`）：八通道融合成一个分数 → Disparity
> Filter 抽骨架 → `related` 边。其余章节（流水线阶段、引用解析、RAG、SSE）仍然有效。

---

## 一、概述与定位

### 1.1 在现有项目中的位置

现有功能层级：

```
工作区 (Workspace)                       ← 本设计扩展的层级
├── 论文 A
│   ├── 论文分析  (/reader/:id)            ← 已存在
│   └── 源码分析  (/code/:id)              ← 已存在
├── 论文 B
└── 论文 C
```

**深度研究**不是第四个论文级 Tab，而是**工作区级**的新视图：

```
工作区 (Workspace)
├── 论文列表           (/workspace/:wsId)              ← 现有 WorkspaceDetailPage
└── 深度研究 ⚡        (/workspace/:wsId/research)     ← 新增（本设计）
```

— 与"论文分析 / 源码分析"在概念上"同级"（都是工作区下的功能视图），但作用域不同：前两者作用于**单篇**，深度研究作用于**整个工作区**。

### 1.2 功能目标

输入：一个已存在的工作区（含若干已完成浅分析的论文）。
处理：
- **模式 1 — 仅本地**：直接对工作区论文构建知识图谱 + RAG 向量索引
- **模式 2 — 联网增量 depth=1**：先通过 Semantic Scholar 抽取每篇 seed 论文的 references，按引用数排序下载 Top-K，纳入工作区，再走与模式 1 相同的后续流程

输出（持久化 + 实时展示）：
- **知识图谱** — 节点 = 论文 / 方法 / 数据集；边 = cites / similar / shared-method / uses-dataset
- **RAG 向量索引** — 用于跨论文语义检索 + 问答

最终主展示形态：**交互式知识图谱**（react-force-graph-2d）。RAG 问答作为附加面板。**不输出 Markdown 报告**。

### 1.3 与现有功能不冲突的保证

| 现有 | 是否被改动 |
|------|------|
| `Paper` 表 | 仅 ADD 3 个可空列（不删不改现有列） |
| `AnalysisResult` 表 | **不动** |
| `Workspace / WorkspacePaper` 表 | **不动** |
| `/api/papers/*`, `/api/analysis/*`, `/api/code/*`, `/api/workspaces/*` | **不动** |
| `ai_config.json` | 新增可选 `embedding` 节，不影响 `translate / analysis / ui` |
| `Layout` 二级导航 | 新增分支：在 `/workspace/:id` 路径下显示 "论文列表 / 深度研究" |
| `WorkspaceDetailPage` | **不动**（保持单纯论文列表） |

新增是**纯增量**：新表、新路由前缀（`/api/research`）、新页面、新前端模块目录 `features/research/`。

---

## 二、技术选型

| 维度 | 选择 | 理由 |
|------|------|------|
| Embedding 模型 | `BAAI/bge-m3`（sentence-transformers，本地） | 多语言、最大 8192 tokens；项目目前没有 OpenAI key 的依赖；本地推理 |
| 备选 Embedding | OpenAI `text-embedding-3-small` | 用户在 `embedding` 配置中填 api_key 时启用；走 `httpx` 直接调用，不依赖额外 SDK |
| 向量库 | ChromaDB（PersistentClient，`./data/chroma/`） | 单文件持久化，零运维；Python 原生 |
| 知识图谱 | NetworkX 内存图 → JSON 持久化到 `research_graphs.nodes/edges` | 节点 < 10k 完全足够，不需要 Neo4j |
| 图谱前端 | `react-force-graph-2d` | 力导向布局；MIT；包体积可控；交互 API 简洁 |
| Agent 编排 | 自研 Pipeline（6 stage） | 不引入 LangChain；与现有 `analyze_paper_stream` 风格一致 |
| LLM 客户端 | 复用 `services/ai_analyzer._create_client` | 同一 MiMo API，同一重试逻辑 |
| 论文搜索/引用 | 扩展 `services/semantic_scholar.py` | 已有限流 + 重试，仅追加新函数 |
| PDF 下载 | 复用 `httpx`，路径写入 `settings.resolved_upload_dir`（与上传论文同目录） | 与现有上传逻辑统一 |
| SSE | 复用 `StreamEvent` + `format_sse` | 前端 `EventSource` 模式不变 |
| DB 迁移 | 复用 `_migrate_schema()` 增量 ADD COLUMN + `create_all` 建新表 | 启动时自动 |

---

## 三、数据模型（精确到 ALTER TABLE 语句）

### 3.1 Paper 表新增字段

文件：`backend/models/paper.py`

```python
# 在现有字段后追加：
source: str = "uploaded"               # uploaded | expanded
parent_paper_id: Optional[int] = None  # 仅 expanded：seed 论文 id
external_id: Optional[str] = None      # S2 paperId 或 arXivId，用于去重
```

迁移代码（追加到 `services/database.py::_migrate_schema()`）：

```python
paper_migrations.extend([
    ("source", "TEXT DEFAULT 'uploaded'"),
    ("parent_paper_id", "INTEGER"),
    ("external_id", "TEXT"),
])
```

> ⚠️ 不修改 `delete_paper()` 行为：删除 seed 论文不级联删除 expanded 子论文（避免误删用户后续手工管理的论文）。前端在删除前若发现 `parent_paper_id` 引用，仅断开关联（置 NULL）。

### 3.2 新增表

新文件：`backend/models/research.py`

```python
from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON, ForeignKey, Integer


class ResearchRun(SQLModel, table=True):
    """一次深度研究任务的执行记录（工作区维度）"""
    __tablename__ = "research_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(
        sa_column=Column(Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True),
    )

    mode: str = "local"        # local | online
    status: str = "pending"    # pending | running | done | failed
    current_stage: Optional[str] = None  # collect | expand | chunk | embed | graph | persist
    error_message: Optional[str] = None

    # 输入参数快照（paper_ids、expand_top_k、rebuild 等）
    params: dict = Field(default={}, sa_column=Column(JSON))

    # 阶段统计
    paper_count: int = 0
    expanded_count: int = 0
    chunk_count: int = 0
    embed_done: int = 0
    embed_total: int = 0
    node_count: int = 0
    edge_count: int = 0

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResearchGraph(SQLModel, table=True):
    """工作区最新图谱快照，每个 workspace 一行（upsert）"""
    __tablename__ = "research_graphs"

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("workspaces.id", ondelete="CASCADE"),
            unique=True,  # 一个工作区一份图谱
            index=True,
        ),
    )
    run_id: int = Field(foreign_key="research_runs.id")

    nodes: list = Field(default=[], sa_column=Column(JSON))   # List[GraphNode] (见 §3.3)
    edges: list = Field(default=[], sa_column=Column(JSON))   # List[GraphEdge]
    stats: dict = Field(default={}, sa_column=Column(JSON))   # {node_count, edge_count, by_kind, ...}

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

`init_db()` 中的 `SQLModel.metadata.create_all(engine)` 会自动建这两张新表。需要在 `services/database.py` 顶部 import：

```python
from ..models.research import ResearchRun, ResearchGraph  # 确保 metadata 注册
```

### 3.3 知识图谱节点/边 Schema

```python
# 不入表，只用于 JSON 序列化
class GraphNode(TypedDict):
    id: str                # "paper:42" / "method:transformer" / "dataset:coco"
    kind: Literal["paper", "method", "dataset"]
    label: str
    meta: dict             # paper 节点：{paper_id, year, venue, citation_count, source, status}

class GraphEdge(TypedDict):
    source: str            # node id
    target: str            # node id
    kind: Literal["cites", "similar", "shared-method", "uses-dataset"]
    weight: float          # 相似度或置信度（0~1）
```

### 3.4 ChromaDB 集合命名

- 每个工作区独立 collection：`ws_{workspace_id}`
- 持久化目录：`./data/chroma/`（新增 `settings.chroma_dir`）
- 每条记录：
  ```python
  collection.add(
      ids=[f"{paper_id}:{chunk_index}"],
      documents=[chunk_text],
      metadatas=[{
          "paper_id": int, "paper_title": str,
          "section_name": str, "chunk_index": int,
          "page_hint": str,
      }],
      embeddings=[vec],   # 由 bge-m3 提前计算
  )
  ```
- 删除某论文：`collection.delete(where={"paper_id": paper_id})`

---

## 四、AI / Embedding 配置（与现有 `ai_config.json` 合并）

### 4.1 配置文件结构变更

`data/ai_config.json` 新增 `embedding` 节：

```json
{
  "translate": { ... },
  "analysis": { ... },
  "ui": { ... },
  "embedding": {
    "provider": "local",           // "local" | "openai"
    "model": "BAAI/bge-m3",        // local 时是 HF 仓库名；openai 时是 model id
    "api_key": "",                 // 仅 openai
    "base_url": "https://api.openai.com/v1",
    "batch_size": 32,
    "device": "auto"               // auto | cpu | cuda | mps
  }
}
```

### 4.2 后端集成

`backend/services/ai_config.py` 修改：

```python
# 在 DEFAULT_CONFIG 中加入
"embedding": {
    "provider": "local",
    "model": "BAAI/bge-m3",
    "api_key": "",
    "base_url": "https://api.openai.com/v1",
    "batch_size": 32,
    "device": "auto",
},

# 新增 getter
def get_embedding_config() -> dict:
    return load_config().get("embedding", DEFAULT_CONFIG["embedding"])
```

`backend/routers/analysis.py` 的 `AppConfig` 新增字段：

```python
class EmbeddingConfig(BaseModel):
    provider: str = "local"
    model: str = "BAAI/bge-m3"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    batch_size: int = 32
    device: str = "auto"

class AppConfig(BaseModel):
    translate: AIModelConfig = AIModelConfig()
    analysis: AIModelConfig = AIModelConfig()
    ui: UIConfig = UIConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()  # ← 新增
```

`update_config` 中追加 `"embedding": req.embedding.model_dump()`。

### 4.3 前端集成

`frontend/src/types/index.ts` 新增 `EmbeddingConfig` 类型；`SettingsPage` 增加"Embedding"标签页，与现有"翻译 / 解读 / UI"并列。

> ⚠️ 配置 schema 保持向后兼容：`load_config()` 已有 `{**DEFAULT_CONFIG[key], **saved.get(key, {})}` 合并逻辑，老 JSON 文件升级时自动填默认。

### 4.4 settings.py 新增

`backend/config.py::Settings` 添加：

```python
chroma_dir: str = "./data/chroma"

@property
def resolved_chroma_dir(self) -> str:
    return _resolve_path(self.chroma_dir)
```

### 4.5 依赖

`backend/requirements.txt` 新增：

```
chromadb>=0.5.0
sentence-transformers>=3.0.0
networkx>=3.2
torch>=2.0          # sentence-transformers 依赖；首次启动可能较慢
```

`frontend/package.json` 新增：

```
"react-force-graph-2d": "^1.25.0",
"force-graph": "^1.43.0"   // peer dep
```

---

## 五、API 设计（与 `client.ts` 严格对齐）

新路由：`backend/routers/research.py`，注册前缀 `/api/research`。

### 5.1 端点列表

| 方法 | 路径 | 用途 | 复用前端 client 函数 |
|------|------|------|---------------------|
| `POST` | `/api/research/workspaces/{ws_id}/runs` | 创建并启动一次研究 | `post` |
| `GET` | `/api/research/runs/{run_id}` | 查询单次 run 元数据 | `get` |
| `GET` | `/api/research/runs/{run_id}/stream` | SSE 阶段进度 | `EventSource` |
| `GET` | `/api/research/workspaces/{ws_id}/graph` | 获取工作区最新图谱（用于直接渲染） | `get` |
| `GET` | `/api/research/workspaces/{ws_id}/latest-run` | 获取最近一次 run | `get` |
| `POST` | `/api/research/workspaces/{ws_id}/query` | RAG 跨论文问答 | `post` |
| `DELETE` | `/api/research/workspaces/{ws_id}` | 清理索引和图谱（不删 PDF） | `del` |

### 5.2 请求/响应 Schema

`backend/models/schemas.py` 末尾追加：

```python
# ============================================================
# Research 相关 Schema
# ============================================================

class ResearchRunCreate(BaseModel):
    mode: str = "local"                                # "local" | "online"
    paper_ids: Optional[list[int]] = None              # None = 工作区全部
    expand_top_k: int = 5                              # 仅 online 模式
    expand_total_limit: int = 50                       # 安全上限
    rebuild: bool = False                              # True 时清空 ChromaDB & 图谱重建
    similarity_threshold: float = 0.78                 # similar 边阈值
    enable_shared_method: bool = False                 # 是否调用 AI 生成 shared-method 边

class ResearchRunResponse(BaseModel):
    id: int
    workspace_id: int
    mode: str
    status: str
    current_stage: Optional[str] = None
    error_message: Optional[str] = None
    params: dict
    paper_count: int
    expanded_count: int
    chunk_count: int
    embed_done: int
    embed_total: int
    node_count: int
    edge_count: int
    created_at: datetime
    updated_at: datetime

class ResearchGraphResponse(BaseModel):
    workspace_id: int
    run_id: int
    nodes: list[dict]
    edges: list[dict]
    stats: dict
    updated_at: datetime

class RagQueryRequest(BaseModel):
    question: str
    top_k: int = 5
    paper_ids: Optional[list[int]] = None              # None = 全工作区

class RagCitation(BaseModel):
    paper_id: int
    paper_title: str
    section_name: str
    snippet: str
    score: float

class RagQueryResponse(BaseModel):
    answer: str
    citations: list[RagCitation]
```

### 5.3 SSE 事件结构（严格沿用 `StreamEvent`）

所有事件复用现有 `StreamEvent(event, field_name, data)`，通过 `format_sse()` 序列化。

| event | field_name | data | 触发时机 |
|-------|-----------|------|----------|
| `status` | `null` | `{message: str}` | 通用进度文本 |
| `stage_start` | `null` | `{stage: str}` | 每个阶段开始 |
| `stage_done` | `null` | `{stage: str, summary: dict}` | 每个阶段结束 |
| `paper_added` | `null` | `{paper_id, title, source_paper_id, total_expanded}` | online 模式下载入库 |
| `embed_progress` | `null` | `{done: int, total: int}` | 每批 embed 完成 |
| `graph_ready` | `null` | `{nodes, edges, stats}` | Stage 5 结束、Stage 6 落库前 |
| `done` | `null` | `{run_id, node_count, edge_count}` | 全流程完成 |
| `error` | `null` | `{message: str, stage: str}` | 任意阶段抛错 |

> 注：`StreamEvent.event` 字段已是任意字符串，无需修改 schema。

### 5.4 错误响应

复用现有 `ErrorResponse(detail, code)` 格式。新增 code：
- `RESEARCH_RUNNING` — 该工作区已有运行中的 run
- `WORKSPACE_EMPTY` — 工作区无 done 状态的论文
- `EMBEDDER_UNAVAILABLE` — sentence-transformers 加载失败
- `CHROMA_NOT_INITIALIZED` — 查询时索引尚未构建

### 5.5 重连策略

与现有 `/api/analysis/{id}/stream` 一致：客户端断线时，前端 `EventSource` 自动重连（指数退避，最多 3 次）。服务端 `GET /runs/{run_id}/stream`：
- 若 `status=done` → 从 DB 重发 `graph_ready` + `done` 终止
- 若 `status=running` → 该 run 已有协程在跑；新连接订阅其事件总线（见 §六pipeline.py）
- 若 `status=failed` → 发 `error` 终止
- 若 `status=pending` → 触发新的协程

---

## 六、后端模块结构与关键代码片段

### 6.1 新增目录与文件

```
backend/
├── models/
│   └── research.py                         (§3.2)
├── routers/
│   └── research.py                         (§六 6.3)
└── services/
    └── deep_research/
        ├── __init__.py
        ├── pipeline.py                     (§六 6.5 编排)
        ├── embedder.py                     (§六 6.6 BGE-M3 + OpenAI 双 provider)
        ├── vector_store.py                 (§六 6.7 ChromaDB 封装)
        ├── graph_builder.py                (§六 6.8 NetworkX 构图)
        ├── reference_extractor.py          (§六 6.9 S2 references)
        ├── pdf_downloader.py               (§六 6.10 openAccessPdf 下载)
        └── stages/
            ├── __init__.py
            ├── collect.py
            ├── expand.py
            ├── chunk.py
            ├── embed.py
            ├── graph.py
            └── persist.py
```

### 6.2 main.py 注册

```python
# backend/main.py
from .routers.research import router as research_router
app.include_router(research_router)
```

### 6.3 router 骨架

```python
# backend/routers/research.py
import asyncio, logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session, create_engine, select

from ..config import settings
from ..models.research import ResearchRun, ResearchGraph
from ..models.workspace import Workspace, WorkspacePaper
from ..models.paper import Paper
from ..models.schemas import (
    ResearchRunCreate, ResearchRunResponse, ResearchGraphResponse,
    RagQueryRequest, RagQueryResponse,
)
from ..utils.streaming import format_sse
from ..services.deep_research.pipeline import run_pipeline, get_event_bus
from ..services.deep_research.vector_store import query_workspace, drop_workspace_collection

router = APIRouter(prefix="/api/research", tags=["research"])

_engine = None
def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})
    return _engine


@router.post("/workspaces/{ws_id}/runs", response_model=ResearchRunResponse)
async def create_run(ws_id: int, req: ResearchRunCreate):
    engine = _get_engine()
    with Session(engine) as session:
        ws = session.get(Workspace, ws_id)
        if not ws:
            raise HTTPException(404, detail={"detail": "工作区不存在", "code": "NOT_FOUND"})

        # 拒绝同时存在 running 任务
        existing = session.exec(
            select(ResearchRun)
            .where(ResearchRun.workspace_id == ws_id)
            .where(ResearchRun.status == "running")
        ).first()
        if existing:
            raise HTTPException(409, detail={"detail": "已有运行中的研究任务", "code": "RESEARCH_RUNNING"})

        # 校验工作区有可用论文
        done_count = session.exec(
            select(Paper)
            .join(WorkspacePaper, WorkspacePaper.paper_id == Paper.id)
            .where(WorkspacePaper.workspace_id == ws_id)
            .where(Paper.status == "done")
        ).all()
        if not done_count:
            raise HTTPException(400, detail={"detail": "工作区没有已完成解析的论文", "code": "WORKSPACE_EMPTY"})

        run = ResearchRun(
            workspace_id=ws_id,
            mode=req.mode,
            status="pending",
            params=req.model_dump(),
        )
        session.add(run); session.commit(); session.refresh(run)
        run_id = run.id

    # 异步启动 pipeline（不在 HTTP 请求内阻塞）
    asyncio.create_task(run_pipeline(run_id))

    with Session(engine) as session:
        run = session.get(ResearchRun, run_id)
        return ResearchRunResponse.model_validate(run, from_attributes=True)


@router.get("/runs/{run_id}", response_model=ResearchRunResponse)
async def get_run(run_id: int):
    engine = _get_engine()
    with Session(engine) as session:
        run = session.get(ResearchRun, run_id)
        if not run:
            raise HTTPException(404, detail={"detail": "Run not found", "code": "NOT_FOUND"})
        return ResearchRunResponse.model_validate(run, from_attributes=True)


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: int):
    engine = _get_engine()
    with Session(engine) as session:
        run = session.get(ResearchRun, run_id)
        if not run:
            raise HTTPException(404, detail={"detail": "Run not found", "code": "NOT_FOUND"})

    async def gen():
        # 1. 历史已结束的 run：直接回放最终事件后关闭
        if run.status == "done":
            with Session(engine) as s:
                g = s.exec(select(ResearchGraph).where(ResearchGraph.workspace_id == run.workspace_id)).first()
                if g:
                    from ..models.schemas import StreamEvent
                    yield format_sse(StreamEvent(event="graph_ready",
                        data={"nodes": g.nodes, "edges": g.edges, "stats": g.stats}))
                yield format_sse(StreamEvent(event="done",
                    data={"run_id": run_id,
                          "node_count": run.node_count,
                          "edge_count": run.edge_count}))
            return
        if run.status == "failed":
            from ..models.schemas import StreamEvent
            yield format_sse(StreamEvent(event="error",
                data={"message": run.error_message or "Unknown error",
                      "stage": run.current_stage or ""}))
            return

        # 2. 进行中 / 未开始：订阅 event_bus
        bus = get_event_bus(run_id)
        async for evt in bus.subscribe():
            yield format_sse(evt)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/workspaces/{ws_id}/graph", response_model=ResearchGraphResponse)
async def get_graph(ws_id: int):
    engine = _get_engine()
    with Session(engine) as session:
        g = session.exec(select(ResearchGraph).where(ResearchGraph.workspace_id == ws_id)).first()
        if not g:
            raise HTTPException(404, detail={"detail": "尚未构建图谱", "code": "NOT_FOUND"})
        return ResearchGraphResponse(
            workspace_id=g.workspace_id, run_id=g.run_id,
            nodes=g.nodes, edges=g.edges, stats=g.stats, updated_at=g.updated_at,
        )


@router.get("/workspaces/{ws_id}/latest-run", response_model=ResearchRunResponse)
async def latest_run(ws_id: int):
    engine = _get_engine()
    with Session(engine) as session:
        run = session.exec(
            select(ResearchRun).where(ResearchRun.workspace_id == ws_id)
            .order_by(ResearchRun.created_at.desc())
        ).first()
        if not run:
            raise HTTPException(404, detail={"detail": "无历史运行", "code": "NOT_FOUND"})
        return ResearchRunResponse.model_validate(run, from_attributes=True)


@router.post("/workspaces/{ws_id}/query", response_model=RagQueryResponse)
async def query(ws_id: int, req: RagQueryRequest):
    from ..services.deep_research.embedder import embed_query
    from ..services.deep_research.vector_store import query_workspace

    vec = await embed_query(req.question)
    hits = query_workspace(ws_id, vec, top_k=req.top_k, paper_ids=req.paper_ids)
    if not hits:
        raise HTTPException(404, detail={"detail": "未构建索引或无命中", "code": "CHROMA_NOT_INITIALIZED"})

    # 调用 LLM 用 hits 生成回答
    from ..services.ai_analyzer import _create_client
    from ..services.ai_config import get_analysis_config
    cfg = get_analysis_config()
    context = "\n\n".join(
        f"[{i+1}] {h['paper_title']} / {h['section_name']}:\n{h['snippet']}"
        for i, h in enumerate(hits)
    )
    prompt = (
        f"你是一名学术研究助手。基于下列检索到的论文片段回答用户问题。"
        f"必须使用中文，并用 [编号] 标注引用。\n\n问题：{req.question}\n\n资料：\n{context}"
    )
    client = _create_client(cfg["api_key"], cfg["base_url"])
    try:
        msg = await client.messages.create(
            model=cfg["model"], max_tokens=2048,
            system="你是学术研究助手，必须基于提供的资料回答，引用编号写在文末。",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        answer = msg.content[0].text.strip()
    finally:
        await client.close()

    return RagQueryResponse(
        answer=answer,
        citations=[
            {"paper_id": h["paper_id"], "paper_title": h["paper_title"],
             "section_name": h["section_name"], "snippet": h["snippet"][:400],
             "score": h["score"]}
            for h in hits
        ],
    )


@router.delete("/workspaces/{ws_id}")
async def clear_research(ws_id: int):
    """清空该工作区的图谱与向量索引（不删除 expanded 论文文件）"""
    engine = _get_engine()
    with Session(engine) as session:
        g = session.exec(select(ResearchGraph).where(ResearchGraph.workspace_id == ws_id)).first()
        if g: session.delete(g); session.commit()
    drop_workspace_collection(ws_id)
    return {"message": "已清空"}
```

### 6.4 EventBus（进程内）

```python
# services/deep_research/event_bus.py
import asyncio
from typing import AsyncIterator
from ...models.schemas import StreamEvent

_buses: dict[int, "EventBus"] = {}


class EventBus:
    def __init__(self):
        self._subscribers: list[asyncio.Queue[StreamEvent | None]] = []
        self._closed = False

    def subscribe(self) -> AsyncIterator[StreamEvent]:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return self._iter(q)

    async def _iter(self, q):
        while True:
            evt = await q.get()
            if evt is None:
                return
            yield evt

    async def publish(self, evt: StreamEvent):
        for q in self._subscribers:
            await q.put(evt)

    async def close(self):
        self._closed = True
        for q in self._subscribers:
            await q.put(None)


def get_event_bus(run_id: int) -> EventBus:
    if run_id not in _buses:
        _buses[run_id] = EventBus()
    return _buses[run_id]


def drop_event_bus(run_id: int):
    _buses.pop(run_id, None)
```

> ⚠️ 仅支持单进程部署（与现有架构一致 — `_engine` 单例、`log_stream` 也是进程内）。多进程部署需要外接 Redis Pub/Sub，本期不做。

### 6.5 Pipeline 编排

```python
# services/deep_research/pipeline.py
import logging
from datetime import datetime, timezone
from sqlmodel import Session, create_engine

from ...config import settings
from ...models.research import ResearchRun
from ...models.schemas import StreamEvent
from .event_bus import get_event_bus, drop_event_bus
from .stages.collect import stage_collect
from .stages.expand import stage_expand
from .stages.chunk import stage_chunk
from .stages.embed import stage_embed
from .stages.graph import stage_graph
from .stages.persist import stage_persist

logger = logging.getLogger(__name__)

_engine = None
def _eng():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})
    return _engine


def _set_stage(run_id: int, stage: str, status: str = "running", **counters):
    with Session(_eng()) as s:
        run = s.get(ResearchRun, run_id)
        run.status = status
        run.current_stage = stage
        for k, v in counters.items():
            setattr(run, k, v)
        run.updated_at = datetime.now(timezone.utc)
        s.add(run); s.commit()


async def run_pipeline(run_id: int):
    bus = get_event_bus(run_id)

    async def emit(event: str, **data):
        await bus.publish(StreamEvent(event=event, data=data))

    try:
        with Session(_eng()) as s:
            run = s.get(ResearchRun, run_id)
            params = dict(run.params or {})
            ws_id = run.workspace_id

        _set_stage(run_id, "collect", "running")
        await emit("stage_start", stage="collect")
        papers = await stage_collect(ws_id, params)
        _set_stage(run_id, "collect", "running", paper_count=len(papers))
        await emit("stage_done", stage="collect", summary={"count": len(papers)})

        if params.get("mode") == "online":
            _set_stage(run_id, "expand")
            await emit("stage_start", stage="expand")
            papers = await stage_expand(ws_id, papers, params, on_paper_added=emit)
            _set_stage(run_id, "expand", "running", expanded_count=sum(1 for p in papers if p.source == "expanded"))
            await emit("stage_done", stage="expand", summary={"total": len(papers)})

        _set_stage(run_id, "chunk")
        await emit("stage_start", stage="chunk")
        chunks = await stage_chunk(papers)
        _set_stage(run_id, "chunk", "running", chunk_count=len(chunks))
        await emit("stage_done", stage="chunk", summary={"count": len(chunks)})

        _set_stage(run_id, "embed", embed_total=len(chunks))
        await emit("stage_start", stage="embed")
        async for done in stage_embed(ws_id, chunks, rebuild=params.get("rebuild", False)):
            _set_stage(run_id, "embed", embed_done=done, embed_total=len(chunks))
            await emit("embed_progress", done=done, total=len(chunks))
        await emit("stage_done", stage="embed", summary={"total": len(chunks)})

        _set_stage(run_id, "graph")
        await emit("stage_start", stage="graph")
        graph = await stage_graph(
            ws_id, papers,
            similarity_threshold=params.get("similarity_threshold", 0.78),
            enable_shared_method=params.get("enable_shared_method", False),
        )
        _set_stage(run_id, "graph", "running",
                   node_count=len(graph["nodes"]), edge_count=len(graph["edges"]))
        await emit("graph_ready", **graph)
        await emit("stage_done", stage="graph",
                   summary={"nodes": len(graph["nodes"]), "edges": len(graph["edges"])})

        _set_stage(run_id, "persist")
        await emit("stage_start", stage="persist")
        await stage_persist(ws_id, run_id, graph)
        await emit("stage_done", stage="persist", summary={})

        _set_stage(run_id, "persist", "done")
        await emit("done", run_id=run_id,
                   node_count=len(graph["nodes"]), edge_count=len(graph["edges"]))

    except Exception as e:
        logger.exception(f"Pipeline run {run_id} failed")
        with Session(_eng()) as s:
            run = s.get(ResearchRun, run_id)
            if run:
                run.status = "failed"
                run.error_message = str(e)
                s.add(run); s.commit()
        await emit("error", message=str(e), stage=run.current_stage if run else "")
    finally:
        await bus.close()
        drop_event_bus(run_id)
```

### 6.6 Embedder（双 provider）

```python
# services/deep_research/embedder.py
import asyncio, logging, httpx
from typing import Optional
from ..ai_config import get_embedding_config

logger = logging.getLogger(__name__)

_model = None  # 本地模型单例（lazy）
_model_lock = asyncio.Lock()


async def _get_local_model():
    global _model
    async with _model_lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer
            cfg = get_embedding_config()
            device = cfg.get("device", "auto")
            if device == "auto":
                import torch
                device = "cuda" if torch.cuda.is_available() else (
                    "mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cpu"
                )
            logger.info(f"Loading {cfg['model']} on {device}...")
            _model = SentenceTransformer(cfg["model"], device=device)
        return _model


async def embed_texts(texts: list[str]) -> list[list[float]]:
    cfg = get_embedding_config()
    if cfg["provider"] == "openai":
        return await _embed_openai(texts, cfg)
    return await _embed_local(texts, cfg)


async def embed_query(text: str) -> list[float]:
    vecs = await embed_texts([text])
    return vecs[0]


async def _embed_local(texts, cfg):
    model = await _get_local_model()
    bs = cfg.get("batch_size", 32)
    # encode 是 CPU/GPU bound，放线程池
    return await asyncio.to_thread(
        lambda: model.encode(texts, batch_size=bs, normalize_embeddings=True).tolist()
    )


async def _embed_openai(texts, cfg):
    if not cfg.get("api_key"):
        raise RuntimeError("OpenAI embedding 需要在配置中填写 api_key")
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{cfg['base_url'].rstrip('/')}/embeddings",
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            json={"model": cfg["model"], "input": texts},
        )
        r.raise_for_status()
        data = r.json()["data"]
        return [d["embedding"] for d in data]
```

### 6.7 Vector store

```python
# services/deep_research/vector_store.py
import logging
from typing import Optional
import chromadb
from chromadb.config import Settings as ChromaSettings
from ...config import settings

logger = logging.getLogger(__name__)
_client = None

def _get_client():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(
            path=settings.resolved_chroma_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
    return _client


def _collection_name(ws_id: int) -> str:
    return f"ws_{ws_id}"


def get_or_create(ws_id: int):
    return _get_client().get_or_create_collection(
        name=_collection_name(ws_id),
        metadata={"hnsw:space": "cosine"},
    )


def upsert_chunks(ws_id: int, ids: list[str], docs: list[str], embeddings: list[list[float]], metas: list[dict]):
    col = get_or_create(ws_id)
    col.upsert(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)


def delete_paper(ws_id: int, paper_id: int):
    col = get_or_create(ws_id)
    col.delete(where={"paper_id": paper_id})


def query_workspace(ws_id: int, vec: list[float], top_k: int = 5, paper_ids: Optional[list[int]] = None) -> list[dict]:
    col = get_or_create(ws_id)
    where = {"paper_id": {"$in": paper_ids}} if paper_ids else None
    res = col.query(query_embeddings=[vec], n_results=top_k, where=where,
                    include=["documents", "metadatas", "distances"])
    out = []
    for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        out.append({
            **meta,
            "snippet": doc,
            "score": 1.0 - float(dist),   # cosine distance → similarity
        })
    return out


def drop_workspace_collection(ws_id: int):
    name = _collection_name(ws_id)
    try:
        _get_client().delete_collection(name)
    except Exception as e:
        logger.warning(f"delete_collection failed: {e}")
```

### 6.8 Graph builder

```python
# services/deep_research/graph_builder.py
import networkx as nx
import numpy as np
from .vector_store import get_or_create


def _paper_node(p) -> dict:
    return {
        "id": f"paper:{p.id}",
        "kind": "paper",
        "label": (p.title or p.filename)[:80],
        "meta": {
            "paper_id": p.id, "year": p.year, "venue": p.venue,
            "source": p.source, "status": p.status,
            "external_id": p.external_id,
        },
    }


def _cosine(a, b):
    a = np.array(a); b = np.array(b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


async def build_graph(ws_id: int, papers, *, similarity_threshold: float, enable_shared_method: bool) -> dict:
    g = nx.DiGraph()

    # 1) 节点
    for p in papers:
        g.add_node(f"paper:{p.id}", **_paper_node(p))

    # 2) cites 边：基于 expanded 关系（parent_paper_id → child）
    by_id = {p.id: p for p in papers}
    for p in papers:
        if p.parent_paper_id and p.parent_paper_id in by_id:
            g.add_edge(f"paper:{p.parent_paper_id}", f"paper:{p.id}",
                       kind="cites", weight=1.0)

    # 3) similar 边：基于每篇论文摘要级向量
    col = get_or_create(ws_id)
    # 取每篇论文的"摘要 chunk"或全部 chunk 的平均
    paper_vecs: dict[int, list[float]] = {}
    for p in papers:
        res = col.get(where={"paper_id": p.id}, include=["embeddings"])
        if res["embeddings"]:
            vec = np.mean(np.array(res["embeddings"]), axis=0).tolist()
            paper_vecs[p.id] = vec
    ids = list(paper_vecs)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            sim = _cosine(paper_vecs[ids[i]], paper_vecs[ids[j]])
            if sim >= similarity_threshold:
                g.add_edge(f"paper:{ids[i]}", f"paper:{ids[j]}",
                           kind="similar", weight=sim)
                # 无向：再加反向
                g.add_edge(f"paper:{ids[j]}", f"paper:{ids[i]}",
                           kind="similar", weight=sim)

    # 4) （可选）shared-method 边：对相似度高的 pair 调一次 LLM
    if enable_shared_method:
        from .method_extractor import infer_shared_methods
        await infer_shared_methods(g, papers, paper_vecs, threshold=0.85)

    # 5) 序列化为 dict
    nodes = [g.nodes[n] for n in g.nodes]
    edges = [{**g.edges[s, t], "source": s, "target": t} for s, t in g.edges]
    stats = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "by_kind": {k: sum(1 for e in edges if e["kind"] == k) for k in ("cites", "similar", "shared-method")},
    }
    return {"nodes": nodes, "edges": edges, "stats": stats}
```

### 6.9 Reference extractor — 扩展 semantic_scholar.py

```python
# services/semantic_scholar.py 追加（不改现有函数）

S2_REF_FIELDS = "title,year,citationCount,externalIds,openAccessPdf,authors"

async def find_s2_paper_id(title: str, doi: str = "", arxiv_id: str = "") -> Optional[str]:
    """通过 DOI/arXivId/标题 解析 S2 paperId"""
    if doi:
        try:
            data = await _rate_limited_get(f"{S2_BASE}/paper/DOI:{doi}", params={"fields": "paperId"})
            return data.get("paperId")
        except Exception:
            pass
    if arxiv_id:
        try:
            data = await _rate_limited_get(f"{S2_BASE}/paper/arXiv:{arxiv_id}", params={"fields": "paperId"})
            return data.get("paperId")
        except Exception:
            pass
    if title:
        try:
            data = await _rate_limited_get(f"{S2_BASE}/paper/search",
                params={"query": title, "fields": "paperId,title", "limit": 1})
            arr = data.get("data") or []
            if arr:
                return arr[0].get("paperId")
        except Exception:
            pass
    return None


async def get_paper_references(s2_paper_id: str, limit: int = 50) -> list[dict]:
    """返回结构化 references 列表，包含 openAccessPdf 信息"""
    data = await _rate_limited_get(
        f"{S2_BASE}/paper/{s2_paper_id}/references",
        params={"fields": f"citedPaper.{S2_REF_FIELDS}", "limit": limit},
    )
    results = []
    for item in data.get("data") or []:
        cited = item.get("citedPaper") or {}
        if not cited.get("title"):
            continue
        ext = cited.get("externalIds") or {}
        oa = cited.get("openAccessPdf") or {}
        results.append({
            "s2_id": cited.get("paperId"),
            "title": cited.get("title"),
            "year": cited.get("year"),
            "citation_count": cited.get("citationCount") or 0,
            "doi": ext.get("DOI"),
            "arxiv_id": ext.get("ArXiv"),
            "pdf_url": oa.get("url"),
        })
    return results
```

### 6.10 PDF downloader

```python
# services/deep_research/pdf_downloader.py
import os, uuid, asyncio, httpx, logging
from ...config import settings

logger = logging.getLogger(__name__)


async def download_pdf(url: str, timeout: float = 30.0) -> Optional[str]:
    """下载 PDF 到 uploads/，返回本地路径或 None"""
    upload_dir = settings.resolved_upload_dir
    os.makedirs(upload_dir, exist_ok=True)
    fname = f"{uuid.uuid4()}.pdf"
    path = os.path.join(upload_dir, fname)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            if not r.headers.get("content-type", "").lower().startswith(("application/pdf", "application/octet-stream")):
                logger.warning(f"Non-PDF content from {url}")
            with open(path, "wb") as f:
                f.write(r.content)
        return path
    except Exception as e:
        logger.warning(f"Download failed for {url}: {e}")
        try: os.remove(path)
        except OSError: pass
        return None
```

### 6.11 各 Stage 关键实现

#### Stage 1 — collect.py

```python
from sqlmodel import Session, create_engine, select
from ...config import settings
from ...models.paper import Paper
from ...models.workspace import WorkspacePaper

_engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})

async def stage_collect(ws_id: int, params: dict) -> list[Paper]:
    pids = params.get("paper_ids")
    with Session(_engine) as s:
        stmt = (select(Paper).join(WorkspacePaper, WorkspacePaper.paper_id == Paper.id)
                .where(WorkspacePaper.workspace_id == ws_id)
                .where(Paper.status == "done"))
        if pids:
            stmt = stmt.where(Paper.id.in_(pids))
        return s.exec(stmt).all()
```

#### Stage 2 — expand.py（核心：联网增量）

```python
import asyncio
from sqlmodel import Session, create_engine, select
from datetime import datetime, timezone

from ...config import settings
from ...models.paper import Paper
from ...models.workspace import WorkspacePaper
from ...models.schemas import StreamEvent
from ..semantic_scholar import find_s2_paper_id, get_paper_references
from .pdf_downloader import download_pdf
from ..ai_analyzer import analyze_paper_stream
from ..ai_config import get_analysis_config
from ..pdf_parser import extract_pdf_content
from ..database import save_analysis_result, update_paper_metadata, update_paper_status

_engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})

SEM = asyncio.Semaphore(3)  # 并发下载上限


async def _download_and_register(ref: dict, parent: Paper, ws_id: int) -> Paper | None:
    if not ref.get("pdf_url"):
        return None
    async with SEM:
        local = await download_pdf(ref["pdf_url"])
    if not local:
        return None

    with Session(_engine) as s:
        # 去重：external_id / DOI 已存在则跳过
        ext = ref.get("s2_id") or ref.get("doi") or ref.get("arxiv_id")
        if ext:
            existing = s.exec(select(Paper).where(Paper.external_id == ext)).first()
            if existing:
                # 已存在，仅确保挂在该工作区
                wp = s.exec(select(WorkspacePaper)
                            .where(WorkspacePaper.workspace_id == ws_id)
                            .where(WorkspacePaper.paper_id == existing.id)).first()
                if not wp:
                    s.add(WorkspacePaper(workspace_id=ws_id, paper_id=existing.id))
                    s.commit()
                return None

        p = Paper(
            filename=f"{ref['title'][:60]}.pdf",
            pdf_path=local,
            file_size=0,                       # 后续无关键依赖
            title=ref.get("title"),
            year=ref.get("year"),
            status="pending",
            source="expanded",
            parent_paper_id=parent.id,
            external_id=ext,
        )
        s.add(p); s.commit(); s.refresh(p)
        s.add(WorkspacePaper(workspace_id=ws_id, paper_id=p.id))
        s.commit()
        return p


async def _shallow_analyze(paper: Paper):
    """对 expanded 论文跑一遍浅分析（复用 analyze_paper_stream）"""
    cfg = get_analysis_config()
    try:
        with Session(_engine) as s:
            update_paper_status(s, paper.id, "extracting")
        content = await asyncio.to_thread(extract_pdf_content, paper.pdf_path)

        result = {}
        async for evt in analyze_paper_stream(
            content.full_text, cfg["api_key"], cfg["base_url"], cfg["model"],
            temperature=cfg.get("temperature", 0.1),
            max_tokens=cfg.get("max_tokens", 16384),
        ):
            if evt.event == "field" and evt.field_name:
                if evt.field_name == "mermaid_chart":
                    result.setdefault("method", {})["mermaid_chart"] = evt.data.get("value")
                else:
                    result[evt.field_name] = evt.data.get("value")

        for k, dft in [("title", paper.title or ""), ("venue", "Unknown"), ("year", paper.year or 0),
                       ("one_sentence", ""), ("problem", ""), ("key_contributions", []),
                       ("outline", []), ("method", {"overview": "", "pipeline": "", "key_modules": []}),
                       ("experiments", {"datasets": [], "baselines": [], "main_results": "",
                                        "ablation": "", "result_table": [], "figures": []}),
                       ("code_mapping", []), ("interview_qa", []), ("limitations", ""),
                       ("related_papers", [])]:
            result.setdefault(k, dft)

        with Session(_engine) as s:
            update_paper_metadata(s, paper.id, result["title"], result["venue"], result["year"])
            save_analysis_result(s, paper.id, result)
            update_paper_status(s, paper.id, "done")
    except Exception as e:
        with Session(_engine) as s:
            update_paper_status(s, paper.id, "failed", str(e))


async def stage_expand(ws_id: int, seeds: list[Paper], params: dict, on_paper_added) -> list[Paper]:
    top_k = int(params.get("expand_top_k", 5))
    total_limit = int(params.get("expand_total_limit", 50))

    expanded: list[Paper] = []
    expanded_count = 0

    for seed in seeds:
        if expanded_count >= total_limit:
            break
        s2_id = await find_s2_paper_id(
            title=seed.title or seed.filename,
            doi=None, arxiv_id=None,
        )
        if not s2_id:
            continue
        refs = await get_paper_references(s2_id, limit=top_k * 3)
        # 排序：有 pdf_url 优先，再按 citationCount 降序
        refs = [r for r in refs if r.get("pdf_url")]
        refs.sort(key=lambda r: r.get("citation_count") or 0, reverse=True)

        added_for_seed = 0
        for ref in refs:
            if added_for_seed >= top_k or expanded_count >= total_limit:
                break
            p = await _download_and_register(ref, seed, ws_id)
            if p:
                expanded.append(p)
                expanded_count += 1
                added_for_seed += 1
                await on_paper_added(
                    "paper_added",
                    paper_id=p.id, title=p.title,
                    source_paper_id=seed.id, total_expanded=expanded_count,
                )

    # 并发跑浅分析
    await asyncio.gather(*[_shallow_analyze(p) for p in expanded])

    # 重新查询，只保留 status=done 的扩展论文
    with Session(_engine) as s:
        done_ids = {p.id for p in s.exec(
            select(Paper).where(Paper.id.in_([p.id for p in expanded]))
            .where(Paper.status == "done")
        ).all()}
    expanded = [p for p in expanded if p.id in done_ids]
    return seeds + expanded
```

#### Stage 3 — chunk.py

```python
from sqlmodel import Session, create_engine, select
from ...config import settings
from ...models.analysis import AnalysisResult
from ..pdf_parser import extract_pdf_content

_engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})


def _sliding_window(text: str, chunk_size: int = 1200, overlap: int = 200):
    out = []
    i = 0
    while i < len(text):
        out.append(text[i:i + chunk_size])
        i += chunk_size - overlap
    return out


async def stage_chunk(papers) -> list[dict]:
    chunks = []
    for p in papers:
        # 优先用浅分析的 outline 章节切片
        outline_sections = []
        with Session(_engine) as s:
            a = s.exec(select(AnalysisResult).where(AnalysisResult.paper_id == p.id)).first()
            if a and a.outline:
                outline_sections = a.outline

        if outline_sections:
            # 用 outline 给的 section name + summary 作为粗粒度索引；
            # 再用滑窗 PDF 全文打细粒度索引
            for i, sec in enumerate(outline_sections):
                if sec.get("summary"):
                    chunks.append({
                        "id": f"{p.id}:sec:{i}",
                        "paper_id": p.id, "paper_title": p.title,
                        "section_name": sec.get("section", f"Section {i}"),
                        "chunk_index": i,
                        "page_hint": sec.get("page", ""),
                        "text": f"{sec.get('section', '')}: {sec.get('summary', '')}",
                    })

        # PDF 全文滑窗
        try:
            content = extract_pdf_content(p.pdf_path)
            full = content.full_text or ""
            for j, ck in enumerate(_sliding_window(full)):
                chunks.append({
                    "id": f"{p.id}:body:{j}",
                    "paper_id": p.id, "paper_title": p.title,
                    "section_name": "Body",
                    "chunk_index": j, "page_hint": "",
                    "text": ck,
                })
        except Exception:
            continue
    return chunks
```

#### Stage 4 — embed.py

```python
from typing import AsyncIterator
from ..embedder import embed_texts
from ..vector_store import upsert_chunks, drop_workspace_collection

BATCH = 32

async def stage_embed(ws_id: int, chunks: list[dict], rebuild: bool) -> AsyncIterator[int]:
    if rebuild:
        drop_workspace_collection(ws_id)

    done = 0
    for i in range(0, len(chunks), BATCH):
        batch = chunks[i:i + BATCH]
        vecs = await embed_texts([c["text"] for c in batch])
        upsert_chunks(
            ws_id,
            ids=[c["id"] for c in batch],
            docs=[c["text"] for c in batch],
            embeddings=vecs,
            metas=[{
                "paper_id": c["paper_id"], "paper_title": c["paper_title"],
                "section_name": c["section_name"], "chunk_index": c["chunk_index"],
                "page_hint": c["page_hint"],
            } for c in batch],
        )
        done += len(batch)
        yield done
```

#### Stage 5 — graph.py

```python
from ..graph_builder import build_graph

async def stage_graph(ws_id: int, papers, *, similarity_threshold: float, enable_shared_method: bool):
    return await build_graph(ws_id, papers,
                             similarity_threshold=similarity_threshold,
                             enable_shared_method=enable_shared_method)
```

#### Stage 6 — persist.py

```python
from datetime import datetime, timezone
from sqlmodel import Session, create_engine, select
from ...config import settings
from ...models.research import ResearchGraph

_engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})

async def stage_persist(ws_id: int, run_id: int, graph: dict):
    with Session(_engine) as s:
        existing = s.exec(select(ResearchGraph).where(ResearchGraph.workspace_id == ws_id)).first()
        if existing:
            existing.nodes = graph["nodes"]
            existing.edges = graph["edges"]
            existing.stats = graph["stats"]
            existing.run_id = run_id
            existing.updated_at = datetime.now(timezone.utc)
            s.add(existing)
        else:
            s.add(ResearchGraph(workspace_id=ws_id, run_id=run_id,
                                nodes=graph["nodes"], edges=graph["edges"], stats=graph["stats"]))
        s.commit()
```

---

## 七、前端集成

### 7.1 路由与导航融合

**App.tsx 新增路由**（不动现有）：

```tsx
import { ResearchPage } from '@/pages/ResearchPage'
// ...
<Route path="/workspace/:id/research" element={<ResearchPage />} />
```

**Layout.tsx 二级导航扩展**：把"匹配 reader|code/:id"的逻辑扩展为：当在 `/workspace/:id` 或 `/workspace/:id/research` 时显示工作区二级导航（论文列表 / 深度研究）：

```tsx
function useCurrentWorkspaceId(): number | null {
  const m = useLocation().pathname.match(/^\/workspace\/(\d+)(?:\/|$)/)
  return m ? Number(m[1]) : null
}

// 已有 useCurrentPaperId 保持不变
const paperId = useCurrentPaperId()
const wsId = useCurrentWorkspaceId()

// 渲染：当 paperId 存在 → 渲染现有 paper-level 二级 nav
// 当 wsId 存在 → 渲染 workspace-level 二级 nav：
//    [论文列表]  [深度研究 ⚡]
```

> ⚠️ 互斥：当用户在论文页（/reader/:id, /code/:id），不显示工作区二级导航；在工作区页则不显示论文级二级导航。

### 7.2 类型定义（types/index.ts 末尾追加）

```ts
// ============================================================
// Research 相关
// ============================================================

export type ResearchMode = 'local' | 'online'
export type ResearchStatus = 'pending' | 'running' | 'done' | 'failed'
export type ResearchStage = 'collect' | 'expand' | 'chunk' | 'embed' | 'graph' | 'persist'

export interface ResearchRunCreate {
  mode: ResearchMode
  paper_ids?: number[] | null
  expand_top_k?: number
  expand_total_limit?: number
  rebuild?: boolean
  similarity_threshold?: number
  enable_shared_method?: boolean
}

export interface ResearchRun {
  id: number
  workspace_id: number
  mode: ResearchMode
  status: ResearchStatus
  current_stage: ResearchStage | null
  error_message: string | null
  params: Record<string, unknown>
  paper_count: number
  expanded_count: number
  chunk_count: number
  embed_done: number
  embed_total: number
  node_count: number
  edge_count: number
  created_at: string
  updated_at: string
}

export type GraphNodeKind = 'paper' | 'method' | 'dataset'
export type GraphEdgeKind = 'cites' | 'similar' | 'shared-method' | 'uses-dataset'

export interface GraphNode {
  id: string
  kind: GraphNodeKind
  label: string
  meta: {
    paper_id?: number
    year?: number | null
    venue?: string | null
    source?: 'uploaded' | 'expanded'
    status?: string
    external_id?: string | null
  }
}

export interface GraphEdge {
  source: string
  target: string
  kind: GraphEdgeKind
  weight: number
}

export interface ResearchGraph {
  workspace_id: number
  run_id: number
  nodes: GraphNode[]
  edges: GraphEdge[]
  stats: {
    node_count: number
    edge_count: number
    by_kind: Record<GraphEdgeKind, number>
  }
  updated_at: string
}

export interface RagCitation {
  paper_id: number
  paper_title: string
  section_name: string
  snippet: string
  score: number
}

export interface RagQueryResponse {
  answer: string
  citations: RagCitation[]
}

export interface EmbeddingConfig {
  provider: 'local' | 'openai'
  model: string
  api_key: string
  base_url: string
  batch_size: number
  device: string
}
```

### 7.3 API 客户端

新文件 `frontend/src/api/research.ts`：

```ts
import { get, post, del } from './client'
import type {
  ResearchRunCreate, ResearchRun, ResearchGraph, RagQueryResponse,
} from '@/types'

export async function createRun(wsId: number, body: ResearchRunCreate): Promise<ResearchRun> {
  return post<ResearchRun>(`/research/workspaces/${wsId}/runs`, body)
}

export async function getRun(runId: number): Promise<ResearchRun> {
  return get<ResearchRun>(`/research/runs/${runId}`)
}

export async function getLatestRun(wsId: number): Promise<ResearchRun | null> {
  try { return await get<ResearchRun>(`/research/workspaces/${wsId}/latest-run`) }
  catch (e: any) { if (e.code === 'NOT_FOUND' || e.code === '404') return null; throw e }
}

export async function getGraph(wsId: number): Promise<ResearchGraph | null> {
  try { return await get<ResearchGraph>(`/research/workspaces/${wsId}/graph`) }
  catch (e: any) { if (e.code === 'NOT_FOUND' || e.code === '404') return null; throw e }
}

export async function queryRag(wsId: number, question: string, top_k = 5, paper_ids?: number[]): Promise<RagQueryResponse> {
  return post<RagQueryResponse>(`/research/workspaces/${wsId}/query`, { question, top_k, paper_ids })
}

export async function clearResearch(wsId: number): Promise<void> {
  await del(`/research/workspaces/${wsId}`)
}
```

### 7.4 SSE Hook

新文件 `frontend/src/hooks/useResearchStream.ts`，仿照 `useStreaming.ts` 模式（同样 EventSource、重连、unmount 清理）：

```ts
import { useState, useEffect, useRef } from 'react'
import type { GraphNode, GraphEdge, ResearchStage, ResearchStatus } from '@/types'

interface StageEvent { stage: ResearchStage; summary?: Record<string, unknown> }
interface PaperAdded { paper_id: number; title: string; source_paper_id: number; total_expanded: number }
interface EmbedProgress { done: number; total: number }
interface GraphReady { nodes: GraphNode[]; edges: GraphEdge[]; stats: Record<string, unknown> }

export function useResearchStream(runId: number | null) {
  const [status, setStatus] = useState<ResearchStatus | 'idle'>('idle')
  const [stage, setStage] = useState<ResearchStage | null>(null)
  const [progressMessage, setMsg] = useState('')
  const [papersAdded, setPapersAdded] = useState<PaperAdded[]>([])
  const [embed, setEmbed] = useState<EmbedProgress>({ done: 0, total: 0 })
  const [graph, setGraph] = useState<GraphReady | null>(null)
  const [error, setError] = useState<string | null>(null)
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (runId === null) return
    const es = new EventSource(`/api/research/runs/${runId}/stream`)
    esRef.current = es

    setStatus('running'); setError(null)

    es.addEventListener('status', (e: MessageEvent) => {
      try { setMsg(JSON.parse(e.data).message || '') } catch { /* noop */ }
    })
    es.addEventListener('stage_start', (e: MessageEvent) => {
      try { setStage(JSON.parse(e.data).stage as ResearchStage) } catch { /* noop */ }
    })
    es.addEventListener('paper_added', (e: MessageEvent) => {
      try { setPapersAdded(prev => [...prev, JSON.parse(e.data) as PaperAdded]) } catch { /* noop */ }
    })
    es.addEventListener('embed_progress', (e: MessageEvent) => {
      try { setEmbed(JSON.parse(e.data) as EmbedProgress) } catch { /* noop */ }
    })
    es.addEventListener('graph_ready', (e: MessageEvent) => {
      try { setGraph(JSON.parse(e.data) as GraphReady) } catch { /* noop */ }
    })
    es.addEventListener('done', () => { setStatus('done'); es.close() })
    es.addEventListener('error', (e: MessageEvent) => {
      try { setError(JSON.parse(e.data).message || '失败') } catch { setError('连接错误') }
      setStatus('failed'); es.close()
    })

    return () => { es.close(); esRef.current = null }
  }, [runId])

  return { status, stage, progressMessage, papersAdded, embed, graph, error }
}
```

### 7.5 页面与组件

```
frontend/src/
├── pages/
│   └── ResearchPage.tsx              路由 /workspace/:id/research
├── features/research/
│   ├── ControlPanel.tsx              模式选择 + 论文勾选 + Top-K + rebuild + 运行
│   ├── ProgressPanel.tsx             阶段进度（来自 useResearchStream）
│   ├── GraphCanvas.tsx               react-force-graph-2d 封装；点击节点 onSelect
│   ├── NodeDetail.tsx                右侧抽屉 — 显示节点详情 + 跳转 /reader/:id
│   ├── EdgeFilter.tsx                cites/similar/shared-method 复选框
│   ├── RagQueryBox.tsx               输入问题 → queryRag → 展示 answer + citations
│   └── CitationChip.tsx              点击跳 /reader/:id
```

#### ResearchPage.tsx 结构

```tsx
export function ResearchPage() {
  const { id } = useParams<{ id: string }>()
  const wsId = Number(id)
  const [runId, setRunId] = useState<number | null>(null)
  const [graph, setGraph] = useState<ResearchGraph | null>(null)
  const stream = useResearchStream(runId)

  // 1) 初始加载历史：图谱 + 最近 run
  useEffect(() => {
    getGraph(wsId).then(setGraph).catch(() => {})
    getLatestRun(wsId).then(r => {
      if (r && r.status === 'running') setRunId(r.id)
    }).catch(() => {})
  }, [wsId])

  // 2) stream.graph 覆盖历史
  useEffect(() => {
    if (stream.graph) setGraph({
      workspace_id: wsId, run_id: runId!,
      nodes: stream.graph.nodes, edges: stream.graph.edges,
      stats: stream.graph.stats as any,
      updated_at: new Date().toISOString(),
    })
  }, [stream.graph, wsId, runId])

  const onStart = async (body: ResearchRunCreate) => {
    const run = await createRun(wsId, body)
    setRunId(run.id)
  }

  return (
    <div className="flex flex-col h-full">
      <ControlPanel wsId={wsId} onStart={onStart} disabled={stream.status === 'running'} />
      {stream.status === 'running' && <ProgressPanel s={stream} />}
      <div className="flex-1 flex gap-4 p-4 overflow-hidden">
        <GraphCanvas graph={graph} />
        <RagQueryBox wsId={wsId} />
      </div>
    </div>
  )
}
```

> ⚠️ **不使用 mock 数据**：所有 `nodes/edges` 来自后端 `getGraph` 或 SSE 实时事件。组件初始态为空数组，UI 上展示"暂无图谱，点击开始研究"。

### 7.6 设置页

`SettingsPage.tsx` 新增 "Embedding" Tab，对应 §四中的 `EmbeddingConfig`。读写复用现有 `GET/POST /api/analysis/config`。

---

## 八、迁移、向后兼容与运维

### 8.1 数据库迁移路径

应用启动顺序（已存在）：`lifespan` → `init_db()` → `_migrate_schema()`。
对深度研究的新增 3 步：

1. `init_db()` 中 `SQLModel.metadata.create_all(engine)` — 自动创建 `research_runs` / `research_graphs`（前提：`models/research.py` 已被 `database.py` import 注册）
2. `_migrate_schema()` 中 `paper_migrations` 追加 3 列 — 已有 `Paper` 表加列
3. 启动时确保 `./data/chroma/` 目录存在（`lifespan` 中 `os.makedirs(settings.resolved_chroma_dir, exist_ok=True)`）

### 8.2 失败处理

| 场景 | 行为 |
|------|------|
| BGE-M3 加载失败 | 抛 `EMBEDDER_UNAVAILABLE`，run.status=failed；UI 提示 "请检查模型下载或切换 OpenAI provider" |
| PDF 下载 timeout | 跳过该 ref，不影响其他；汇总写入 `params.failed_downloads` |
| 扩展论文浅分析失败 | 该论文 status=failed，**不进入图谱**，但已下载文件保留在 uploads/ |
| ChromaDB 损坏 | UI 提供"重建索引"按钮 → 调用 `clearResearch(wsId)` 后重新发起 run |
| S2 429 | 复用现有 `_rate_limited_get` 退避机制 |
| 同工作区并发 run | API 返回 409 `RESEARCH_RUNNING` |

### 8.3 资源上限

| 项 | 默认 | 在 `ResearchRunCreate.params` 中可调 |
|----|------|-----|
| `expand_top_k` | 5 | ✓ |
| `expand_total_limit` | 50 | ✓ |
| 下载并发 | 3（Semaphore） | 否（代码常量） |
| Chunk batch | 32 | 否 |
| Embedding max_length | bge-m3 默认 8192 | 否 |
| 相似边阈值 | 0.78 | ✓ |
| 单 run 总耗时 | 无硬超时（受 S2 速率 + 模型推理速度限制） | — |

### 8.4 部署注意

- BGE-M3 首次加载 ~2GB 到 `~/.cache/huggingface`；建议在 README 提醒首次运行较慢
- ChromaDB 路径加入 `.gitignore`（`/data/chroma/`）
- 若用户切换 provider 到 openai，需要在 SettingsPage 提示"重建索引"以避免维度不匹配

---

## 九、实施计划（按文件粒度）

### Phase 1 — 后端骨架（无 expand）

| # | 文件 | 动作 |
|---|------|------|
| 1 | `requirements.txt` | 加 chromadb / sentence-transformers / networkx |
| 2 | `models/paper.py` | 新增 3 字段 |
| 3 | `models/research.py` | 新建：`ResearchRun` + `ResearchGraph` |
| 4 | `services/database.py::_migrate_schema` | 加 3 列迁移；顶部 import research models |
| 5 | `config.py` | 加 `chroma_dir` + `resolved_chroma_dir` |
| 6 | `main.py::lifespan` | mkdir chroma 目录 |
| 7 | `services/ai_config.py` | 加 `embedding` 默认 + `get_embedding_config` |
| 8 | `routers/analysis.py` | `AppConfig` 加 `embedding` 字段 |
| 9 | `models/schemas.py` | 加 Research/Rag schemas |
| 10 | `services/deep_research/embedder.py` | bge-m3 + openai 双 provider |
| 11 | `services/deep_research/vector_store.py` | ChromaDB 封装 |
| 12 | `services/deep_research/event_bus.py` | EventBus |
| 13 | `services/deep_research/stages/{collect,chunk,embed,graph,persist}.py` | 5 阶段实现 |
| 14 | `services/deep_research/graph_builder.py` | NetworkX 构图（无 cites 边逻辑） |
| 15 | `services/deep_research/pipeline.py` | 编排（暂不调用 expand） |
| 16 | `routers/research.py` | 6 个端点 |
| 17 | `main.py` | include router |

> 此阶段可端到端跑通 local 模式（不联网扩展）。

### Phase 2 — 联网扩展

| # | 文件 | 动作 |
|---|------|------|
| 18 | `services/semantic_scholar.py` | 加 `find_s2_paper_id` + `get_paper_references` |
| 19 | `services/deep_research/pdf_downloader.py` | 新建 |
| 20 | `services/deep_research/stages/expand.py` | 实现下载 → 入库 → 浅分析 |
| 21 | `services/deep_research/pipeline.py` | online 模式分支接入 expand |
| 22 | `services/deep_research/graph_builder.py` | cites 边（基于 parent_paper_id） |

### Phase 3 — 前端

| # | 文件 | 动作 |
|---|------|------|
| 23 | `package.json` | 加 react-force-graph-2d / force-graph |
| 24 | `types/index.ts` | 加 Research 类型 |
| 25 | `api/research.ts` | 新建 |
| 26 | `hooks/useResearchStream.ts` | 新建 |
| 27 | `components/Layout.tsx` | 加工作区二级导航 |
| 28 | `App.tsx` | 加 `/workspace/:id/research` 路由 |
| 29 | `features/research/*` | 7 个组件 |
| 30 | `pages/ResearchPage.tsx` | 主页面 |
| 31 | `pages/SettingsPage.tsx` | 加 Embedding Tab |

### Phase 4 — 验证

| # | 验证项 | 通过标准 |
|---|--------|----------|
| 32 | 5 篇论文 local 模式 | 图谱含 5 节点 + 至少 1 条 similar 边；RAG 检索可返回引用 |
| 33 | 3 篇论文 online 模式 | 扩展 ≤15 篇 → 入库 → 浅分析 done → 图谱含 cites + similar 边 |
| 34 | 重连测试 | 关闭浏览器再打开同 run_id → 历史 done 直接渲染图谱 |
| 35 | 并发限制 | 同工作区发起两次 run → 第二次返回 409 |
| 36 | rebuild=true | ChromaDB 清空后重新构建 |
| 37 | OpenAI provider 切换 | 配置 api_key 后重新 run → 走 OpenAI embed |

---

## 十、待回答的开放问题

1. **是否需要中断 / 取消 run** — 当前设计无 cancel 端点。低优先级。
2. **Expand 是否覆盖去重已删除论文** — 当前用 `external_id` 唯一性，若用户删除 expanded 论文，下次 run 会重新下载。可接受。
3. **图谱节点是否包含 method 节点** — 默认不生成（避免噪声）；通过 `enable_shared_method=true` 启用，调用 LLM 提取共享方法名（潜在 token 消耗大）。
4. **多用户隔离** — 当前没有用户系统，所有工作区共享一个 chroma 目录。未来要加用户时，`ws_{id}` 命名足够隔离。
