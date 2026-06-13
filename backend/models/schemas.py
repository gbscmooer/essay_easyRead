"""
API 请求/响应 Schema

Pydantic 模型，用于 FastAPI 的请求验证和响应序列化。
与数据库模型分离，API 层面的字段可以与 DB 不同。
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from .analysis import MethodModel, ExperimentModel, CodeMapping, InterviewQA, OutlineItem, RelatedPaper, FigureAnalysis


# ============================================================
# Paper 相关 Schema
# ============================================================


class PaperCreate(BaseModel):
    """
    创建论文记录的请求体

    实际上论文记录在文件上传时自动创建，此 schema 用于
    内部流程中传递文件信息。
    """

    filename: str
    file_size: int


class PaperResponse(BaseModel):
    """
    单篇论文的 API 响应

    包含论文元信息和解析状态，但不包含完整解析结果
    （完整结果通过 AnalysisResponse 返回）。
    """

    id: int
    filename: str
    title: Optional[str] = None
    authors: Optional[str] = None       # 作者列表（逗号分隔），自动索引/解读填充
    venue: Optional[str] = None
    year: Optional[int] = None
    category: Optional[str] = None      # arXiv 学科分类，如 "cs.LG"
    status: str  # pending | extracting | analyzing | done | failed | indexed
    index_status: str = "none"          # none | indexing | indexed | failed（自动索引）
    error_message: Optional[str] = None
    code_url: Optional[str] = None
    code_status: str = "none"
    source: str = "uploaded"   # uploaded | expanded
    level: int = 1             # 1=用户手动加; 2+=联网扩展下载
    read_level: str = "none"       # none | digest | full（解读层级）
    created_at: datetime
    updated_at: datetime


class PaperListResponse(BaseModel):
    """
    论文列表的 API 响应

    用于论文库页面，支持分页和筛选。
    """

    papers: list[PaperResponse]
    total: int  # 总数（用于分页）


# ============================================================
# Analysis 相关 Schema
# ============================================================


class AnalysisResponse(BaseModel):
    """
    完整解析结果的 API 响应

    包含一篇论文的全部结构化分析数据。
    嵌套字段使用强类型模型，与前端 TypeScript 类型一一对应。
    """

    paper_id: int
    title: str
    authors: str = ""
    venue: str
    year: int
    one_sentence: str
    problem: str
    key_contributions: list[str]
    outline: list[OutlineItem] = []
    method: MethodModel  # 强类型，非 dict
    experiments: ExperimentModel  # 强类型，非 dict
    code_mapping: list[CodeMapping]  # 强类型列表
    interview_qa: list[InterviewQA]  # 强类型列表
    limitations: str
    related_papers: list[RelatedPaper]
    mermaid_chart: Optional[str] = None


# ============================================================
# Streaming 事件 Schema
# ============================================================


class StreamEvent(BaseModel):
    """
    SSE 流式事件

    用于分析过程中的实时进度推送。
    通过 Server-Sent Events (SSE) 发送到前端。

    event 类型：
    - "status": 进度状态更新（如 "正在提取文本"）
    - "field": 某个字段解析完成，包含字段名和值
    - "done": 全部解析完成
    - "error": 解析失败

    field_name: 当 event="field" 时，表示完成了哪个字段
    data: 事件数据，结构取决于 event 类型
    """

    event: str  # "status" | "field" | "done" | "error"
    field_name: Optional[str] = None  # 如 "title", "method", "experiments"
    data: dict = {}  # 事件负载


# ============================================================
# 通用 Schema
# ============================================================


class ErrorResponse(BaseModel):
    """
    统一错误响应

    所有 API 错误都使用此格式返回。
    """

    detail: str  # 错误描述
    code: str  # 错误码，如 "FILE_TOO_LARGE", "PARSING_FAILED"


# ============================================================
# Code Analysis 相关 Schema
# ============================================================


class CodeAnalysisResponse(BaseModel):
    """代码分析结果响应"""

    paper_id: int
    file_tree: dict = {}
    data_flow: Optional[str] = None
    dependency_graph: list[dict] = []
    paper_code_mapping: list[dict] = []
    node_metrics: list[dict] = []
    status: str = "pending"
    error_message: Optional[str] = None


class CodeSummaryResponse(BaseModel):
    """代码摘要响应（侧边栏用）"""

    code_url: Optional[str] = None
    code_status: str = "none"
    code_summary: Optional[str] = None


# ============================================================
# Research 相关 Schema
# ============================================================

class ResearchRunCreate(BaseModel):
    """创建深度研究任务的请求体"""
    mode: str = Field("local", pattern=r"^(local|online)$")
    paper_ids: Optional[list[int]] = None
    # 是否把工作区内已存在的联网扩展论文（level>=2）纳入本次图谱。
    # 关闭后只用工作区内部论文（level==1），不使用之前下载到本地的扩展论文。
    include_expanded: bool = True
    deep: int = Field(1, ge=1, le=5)
    expand_top_k: int = Field(5, ge=1, le=50)
    expand_total_limit: int = Field(50, ge=1, le=500)
    rebuild: bool = False
    similarity_threshold: float = Field(0.78, ge=0, le=1)
    enable_shared_method: bool = False


class ResearchRunResponse(BaseModel):
    """深度研究任务的响应"""
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
    """工作区图谱快照响应"""
    workspace_id: int
    run_id: int
    nodes: list[dict]
    edges: list[dict]
    stats: dict
    updated_at: datetime


class RagQueryRequest(BaseModel):
    """RAG 跨论文问答请求"""
    question: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(5, ge=1, le=20)
    paper_ids: Optional[list[int]] = None


class RagCitation(BaseModel):
    """RAG 回答中的引用来源"""
    paper_id: int
    paper_title: str
    section_name: str
    snippet: str
    score: float


class RagQueryResponse(BaseModel):
    """RAG 问答响应"""
    answer: str
    citations: list[RagCitation]
