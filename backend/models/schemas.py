"""
API 请求/响应 Schema

Pydantic 模型，用于 FastAPI 的请求验证和响应序列化。
与数据库模型分离，API 层面的字段可以与 DB 不同。
"""

from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field
from .analysis import MethodModel, ExperimentModel, CodeMapping, InterviewQA, OutlineItem, RelatedPaper, FigureAnalysis


# ============================================================
# Paper 相关 Schema
# ============================================================


class ExplorationMessageCreate(BaseModel):
    question: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    search_more: bool = False
    selected_keys: list[str] = Field(default_factory=list, max_length=12)


class ExplorationMessageResponse(BaseModel):
    id: int
    run_id: int
    question: str
    answer: str
    status: str
    search_more: bool
    selected_keys: list[str]
    papers: list[dict]
    evidence_keys: list[str]
    context: dict
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime


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
    subjects: list[str] = Field(default_factory=list)
    publication_type: str = "unknown"
    publisher: Optional[str] = None
    providers: list[str] = Field(default_factory=list)
    metadata_sources: dict[str, str] = Field(default_factory=dict)
    metadata_updated_at: Optional[str] = None
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    status: str  # pending | extracting | analyzing | done | failed | indexed
    index_status: str = "none"          # none | indexing | indexed | failed（自动索引）
    error_message: Optional[str] = None
    code_url: Optional[str] = None
    code_path: Optional[str] = None
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


class PaperMetadataUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=1000, pattern=r"\S")
    authors: str = Field(default="", max_length=4000)
    venue: str = Field(default="", max_length=500)
    year: Optional[int] = Field(default=None, ge=1900, le=2100)
    publication_type: Literal["journal", "conference", "preprint", "book", "other", "unknown"] = "unknown"
    subjects: list[str] = Field(default_factory=list, max_length=30)
    publisher: str = Field(default="", max_length=500)


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
# Workspace 研究洞察 Schema
# ============================================================


class WorkspaceInsightPaper(BaseModel):
    """工作区洞察中的单篇论文。"""

    paper_id: int
    title: str
    venue: Optional[str] = None
    year: Optional[int] = None
    source: str = "uploaded"
    read_level: str = "none"
    importance: float = 0.0
    bridge_score: float = 0.0
    cluster: int = -1
    cluster_label: Optional[str] = None
    reason: str = ""


class WorkspaceClusterSummary(BaseModel):
    """工作区图谱中的子方向汇总。"""

    cluster: int
    label: str
    paper_count: int
    unread_count: int
    digest_count: int
    full_count: int
    top_paper_id: Optional[int] = None
    top_paper_title: Optional[str] = None
    average_importance: float = 0.0


class WorkspaceInsightResponse(BaseModel):
    """工作区洞察响应。"""

    workspace_id: int
    graph_available: bool
    paper_count: int
    read_counts: dict[str, int]
    source_counts: dict[str, int]
    modularity: Optional[float] = None
    cluster_count: int = 0
    clusters: list[WorkspaceClusterSummary] = []
    top_papers: list[WorkspaceInsightPaper] = []
    bridge_papers: list[WorkspaceInsightPaper] = []
    next_reads: list[WorkspaceInsightPaper] = []
    updated_at: datetime


class WorkspaceReadingPlanPaper(BaseModel):
    """阅读路径中的论文节点。"""

    paper_id: int
    title: str
    venue: Optional[str] = None
    year: Optional[int] = None
    source: str = "uploaded"
    read_level: str = "none"
    detail_level: str = "metadata"
    cluster_label: Optional[str] = None
    stage: int
    score: float = 0.0
    prerequisites: list[int] = []
    reason: str = ""


class WorkspaceReadingPlanStage(BaseModel):
    """阅读路径阶段。"""

    stage: int
    label: str
    objective: str
    papers: list[WorkspaceReadingPlanPaper] = []


class WorkspaceReadingPlanEdge(BaseModel):
    """阅读路径依赖边。"""

    source_paper_id: int
    target_paper_id: int
    kind: str
    weight: float = 1.0
    reason: str = ""


class WorkspaceReadingPlanResponse(BaseModel):
    """工作区自适应阅读路径响应。"""

    workspace_id: int
    target_paper_id: Optional[int] = None
    graph_available: bool
    strategy: str
    coverage: dict
    stages: list[WorkspaceReadingPlanStage] = []
    edges: list[WorkspaceReadingPlanEdge] = []
    updated_at: datetime


class WorkspaceSurveyMatrixRow(BaseModel):
    """工作区综述矩阵中的单篇论文。"""

    paper_id: int
    title: str
    venue: Optional[str] = None
    year: Optional[int] = None
    detail_level: str = "metadata"
    task: str = ""
    method: str = ""
    datasets: list[str] = []
    baselines: list[str] = []
    contributions: list[str] = []
    limitations: str = ""
    code_concepts: list[str] = []


class WorkspaceSurveyCluster(BaseModel):
    """工作区综述中的子方向聚合。"""

    label: str
    paper_count: int
    paper_ids: list[int] = []
    years: list[int] = []
    common_methods: list[str] = []
    datasets: list[str] = []
    summary: str = ""


class WorkspaceSurveyDraftSection(BaseModel):
    """自动综述草稿片段。"""

    key: str
    title: str
    content: str


class WorkspaceSurveyResponse(BaseModel):
    """工作区综述与对比矩阵响应。"""

    workspace_id: int
    paper_count: int
    detail_counts: dict[str, int]
    topics: list[str] = []
    datasets: list[str] = []
    baselines: list[str] = []
    matrix: list[WorkspaceSurveyMatrixRow] = []
    clusters: list[WorkspaceSurveyCluster] = []
    draft_sections: list[WorkspaceSurveyDraftSection] = []
    updated_at: datetime


class WorkspaceComparisonPaper(BaseModel):
    """工作区论文对比中的单篇论文摘要。"""

    paper_id: int
    title: str
    venue: Optional[str] = None
    year: Optional[int] = None
    source: str = "uploaded"
    read_level: str = "none"
    detail_level: str = "metadata"


class WorkspaceComparisonSection(BaseModel):
    """论文对比中的单个分析维度。"""

    key: str
    label: str
    score: float = 0.0
    summary: str = ""
    shared: list[str] = []
    unique_a: list[str] = []
    unique_b: list[str] = []


class WorkspaceComparisonOrder(BaseModel):
    """论文对比推荐阅读顺序。"""

    first_paper_id: int
    first_title: str
    second_paper_id: int
    second_title: str
    reason: str = ""


class WorkspaceComparisonResponse(BaseModel):
    """工作区内两篇论文的结构化对比结果。"""

    workspace_id: int
    paper_a: WorkspaceComparisonPaper
    paper_b: WorkspaceComparisonPaper
    overall_score: float = 0.0
    relation: str = ""
    relation_reason: str = ""
    shared_topics: list[str] = []
    same_cluster: bool = False
    cluster_label: Optional[str] = None
    direct_edge_kinds: list[str] = []
    reading_order: WorkspaceComparisonOrder
    sections: list[WorkspaceComparisonSection] = []
    updated_at: datetime


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
    # Compatibility only; no longer changes the statistical backbone.
    map_density: float = Field(8.0, ge=1.0, le=40.0)
    # Original disparity filter; unrelated to a target edge count.
    backbone_alpha: float = Field(0.05, ge=0.01, le=0.9)
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
    # 领域认知综合产物（GraphSynthesis 结构）；旧图谱 / 未开启时为 None
    synthesis: Optional[dict] = None
    updated_at: datetime


class ExplorationRunCreate(BaseModel):
    """创建领域探索任务的请求体（问句驱动，见 GOAL.md）"""
    question: str = Field(..., min_length=2, max_length=4000)
    depth: str = Field("standard", pattern=r"^(quick|standard|deep)$")
    workspace_id: Optional[int] = Field(None, ge=1)
    seed_paper_ids: Optional[list[int]] = Field(None, max_length=240)
    search_online: bool = True


class ExplorationRunResponse(BaseModel):
    """领域探索任务的完整响应（含产物）"""
    id: int
    question: str
    depth: str
    status: str
    current_stage: Optional[str] = None
    error_message: Optional[str] = None
    provider: str = ""
    options: dict = Field(default_factory=dict)
    decompose: Optional[dict] = None
    result: Optional[dict] = None
    created_at: datetime
    updated_at: datetime


class ExplorationRunSummary(BaseModel):
    """领域探索历史列表的轻量条目（不带 result 大字段）"""
    id: int
    question: str
    depth: str
    status: str
    provider: str = ""
    options: dict = Field(default_factory=dict)
    created_at: datetime


class RagQueryRequest(BaseModel):
    """RAG 跨论文问答请求"""
    question: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(5, ge=1, le=20)
    paper_ids: Optional[list[int]] = None
    mode: str = Field("vector", pattern=r"^(vector|hybrid|graph)$")



class RagCitationDebug(BaseModel):
    """RAG 检索调试信息（可选，前端默认折叠展示）。"""
    source_hits: dict[str, float] = Field(default_factory=dict)
    fused_score: Optional[float] = None
    rerank_score: Optional[float] = None
    graph_score: Optional[float] = None
    chunk_id: Optional[str] = None
    channel_ranks: dict[str, int] = Field(default_factory=dict)


class RagCitation(BaseModel):
    """RAG 回答中的引用来源"""
    paper_id: int
    paper_title: str
    section_name: str
    snippet: str
    score: float
    download_status: Optional[str] = None   # none | available | failed | unavailable | pending
    metadata_only: Optional[bool] = None    # True = 无 PDF 正文，仅元数据
    debug: Optional[RagCitationDebug] = None


class RecallChannelStats(BaseModel):
    """expand 阶段单路召回统计（SSE recall_stats）。"""
    channel: str
    hit_count: int = 0
    failed: int = 0
    latency_ms: float = 0.0
    errors: list[str] = Field(default_factory=list)
    stage: Optional[str] = None
    error: Optional[str] = None


class RagQueryResponse(BaseModel):
    """RAG 问答响应"""
    answer: str
    citations: list[RagCitation]
    path_evidence: list[dict] = Field(default_factory=list)
    debug: Optional[list[RagCitationDebug]] = None


# ============================================================
# Agent Tool / MCP 相关 Schema
# ============================================================


class AgentToolDescriptor(BaseModel):
    """可供 MCP 或外部 Agent 调用的工具描述。"""

    name: str
    description: str
    input_schema: dict


class AgentToolManifestResponse(BaseModel):
    """Agent 工具清单。"""

    tools: list[AgentToolDescriptor]


class PaperSearchRequest(BaseModel):
    """Agent 论文检索请求。"""

    query: str = Field(..., min_length=1, max_length=1000)
    workspace_id: Optional[int] = None
    limit: int = Field(10, ge=1, le=50)
    include_expanded: bool = True


class EvidenceRequest(BaseModel):
    """Agent 证据片段检索请求。"""

    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(5, ge=1, le=20)
    paper_ids: Optional[list[int]] = None


class GraphNeighborsRequest(BaseModel):
    """Agent 图谱邻域请求。"""

    paper_id: int
    depth: int = Field(1, ge=1, le=2)
    limit: int = Field(50, ge=1, le=200)
    edge_kinds: Optional[list[str]] = None


class DevBriefRequest(BaseModel):
    """基于论文证据生成开发任务 brief。"""

    goal: str = Field(..., min_length=1, max_length=4000)
    top_k: int = Field(6, ge=1, le=20)
    paper_ids: Optional[list[int]] = None


class ResearchAgentRunRequest(BaseModel):
    """研究 Agent Harness 运行请求（同 goal 产品级入口）。"""

    workflow: str = Field(..., min_length=1, max_length=64, description="literature_brief | survey_path")
    goal: str = Field(..., min_length=1, max_length=4000)
    workspace_id: int = Field(..., ge=1)


class ResearchAgentRunResponse(BaseModel):
    """研究 Agent Harness 运行结果。"""

    run_id: str
    workflow: str
    goal: str
    workspace_id: int
    status: str
    started_at: str
    finished_at: str
    journal: list[dict] = []
    artifacts: dict = {}
    error: Optional[dict] = None
    harness: dict = {}
