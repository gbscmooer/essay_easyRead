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
    """创建领域探索任务的请求体（问句驱动，见 GOAL.md）

    academic_survey_v2 字段（S0 冻结）：全部可选，默认走 legacy_v1 行为；
    只有显式 workflow_version=academic_survey_v2 使用新链路。
    """
    question: str = Field(..., min_length=2, max_length=4000)
    depth: str = Field("standard", pattern=r"^(quick|standard|deep)$")
    workspace_id: Optional[int] = Field(None, ge=1)
    seed_paper_ids: Optional[list[int]] = Field(None, max_length=240)
    search_online: bool = True
    # 盲测口径：把已知论文（如充当标准答案的综述）从语料中整链路排除——
    # 召回、引文扩展、计量、综合与报告全文下载都不允许接触这些标识符
    exclude_dois: Optional[list[str]] = Field(None, max_length=50)
    exclude_arxiv_ids: Optional[list[str]] = Field(None, max_length=50)
    exclude_titles: Optional[list[str]] = Field(None, max_length=50)
    # ── academic_survey_v2 输入（随 options["workflow"] 快照落盘）──
    workflow_version: Optional[str] = Field(None, pattern=r"^(legacy_v1|academic_survey_v2)$")
    protocol_options: Optional[dict] = None   # 子问题/纳排/来源策略覆盖（S5 冻结语义）
    budget: Optional[dict] = None             # resource -> limit 覆盖；不得由 agent 自行放宽
    content_cutoff: Optional[datetime] = None  # 内容时间门禁（含当日；历史轨道）
    policy_id: Optional[str] = None           # 排除策略引用（S1）


class SourceHealth(BaseModel):
    """单个数据源的预检结果（带权限的真实最小请求探测，不是 ping 可达）"""
    name: str
    role: str = Field("primary", pattern=r"^(primary|fallback)$")
    # ok | auth_error | rate_limited | network_error | error | unconfigured
    status: str = Field(..., pattern=r"^(ok|auth_error|rate_limited|network_error|error|unconfigured)$")
    detail: str = ""
    latency_ms: Optional[int] = None
    http_status: Optional[int] = None


class PreflightResponse(BaseModel):
    """数据源预检集合：调查启动前展示各源权限状态"""
    checked_at: datetime
    sources: list[SourceHealth]


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
    # ── academic_survey_v2（S0 冻结；legacy_v1 运行为默认值/None）──
    workflow_version: str = "legacy_v1"
    state_version: int = 0
    protocol: Optional["ProtocolSummaryResponse"] = None
    current_revision: Optional["ReportRevisionEntry"] = None
    # 断点续传检查点摘要（legacy v1；不含逐篇提取正文）
    checkpoint_summary: Optional[dict] = None
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
    workflow_version: str = "legacy_v1"
    state_version: int = 0
    created_at: datetime


# ============================================================
# academic_survey_v2 契约 Schema（S0 冻结；执行器由 S3/S5 接入）
# ============================================================


class ProtocolSubquestionSchema(BaseModel):
    """协议子问题（必答子问题是停止条件的一部分）"""
    subquestion_id: str
    question: str
    facet: str = ""
    required: bool = False
    status: str = "open"


class ProtocolSummaryResponse(BaseModel):
    """POST /runs 响应中的协议摘要（完整协议见 GET /runs/{id}/protocol）"""
    protocol_id: Optional[str] = None
    version: int = 1
    hash: Optional[str] = None
    question: str
    subquestion_count: int = 0


class ProtocolChangeEntry(BaseModel):
    """协议变更记录（每次修改产生新冻结版本，不丢历史）"""
    version: int
    protocol_id: Optional[str] = None
    change_reason: Optional[str] = None
    created_at: datetime


class RunProtocolResponse(BaseModel):
    """GET /runs/{id}/protocol：冻结协议及变更记录；不返回隐藏金标。

    v1 运行返回 legacy 投影（从 options 快照推导，legacy=true，不含子问题）。
    """
    run_id: int
    workflow_version: str
    legacy: bool = True
    protocol: Optional[dict] = None          # ResearchProtocol 结构；v1 为 None
    legacy_options: Optional[dict] = None    # v1 输入快照（options）
    changes: list[ProtocolChangeEntry] = []


class ResearchTaskItem(BaseModel):
    """GET /runs/{id}/tasks 的任务条目（账本行投影）"""
    task_id: str
    run_id: int
    kind: str
    subquestion_id: Optional[str] = None
    state: str
    dependencies: list[str] = []
    idempotency_key: Optional[str] = None
    error_code: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ResearchTaskListResponse(BaseModel):
    """分页任务列表；v1 运行固定为空列表（legacy=true）"""
    run_id: int
    workflow_version: str
    legacy: bool = True
    items: list[ResearchTaskItem] = []
    next_cursor: Optional[str] = None
    total: int = 0


class CoverageSubquestionEntry(BaseModel):
    """覆盖矩阵的子问题条目（S5 覆盖快照投影）"""
    subquestion_id: str
    question: str = ""
    status: str = "open"
    screened: int = 0
    included_families: int = 0
    fulltexts: int = 0
    evidence_kinds: list[str] = []
    open_gaps: int = 0


class CoverageGapEntry(BaseModel):
    """缺口条目（不包含评分器缺失名单）"""
    gap_id: str
    subquestion_id: Optional[str] = None
    severity: str = "medium"
    missing_evidence_kind: str = ""
    next_action: str = ""
    status: str = "open"


class RunCoverageResponse(BaseModel):
    """GET /runs/{id}/coverage：按子问题覆盖、缺口、最近补充动作"""
    run_id: int
    workflow_version: str
    legacy: bool = True
    updated_at: Optional[datetime] = None
    subquestions: list[CoverageSubquestionEntry] = []
    gaps: list[CoverageGapEntry] = []
    recent_actions: list[dict] = []          # 最近补搜/补读任务摘要
    legacy_summary: Optional[dict] = None    # v1：result.survey_report.coverage 原样投影


class BudgetResourceEntry(BaseModel):
    """单资源账本聚合（S0 冻结聚合口径见 research_core.ResearchBudgetEvent）"""
    resource: str
    limit: Optional[int] = None              # 协议冻结的上限；未定义为 None
    used: int = 0
    reserved: int = 0
    unit: str = "count"
    usage_known: bool = True                 # 存在 usage_unknown 窗口时为 False


class RunBudgetResponse(BaseModel):
    """GET /runs/{id}/budget：各资源 limit/used/reserved 与停止原因；不返回凭证"""
    run_id: int
    workflow_version: str
    legacy: bool = True
    resources: list[BudgetResourceEntry] = []
    stop_reason: Optional[str] = None


class RunControlRequest(BaseModel):
    """pause/resume/cancel 的条件状态（事务内 compare-and-swap；陈旧版本 409）"""
    expected_state: Optional[str] = None
    expected_state_version: Optional[int] = Field(None, ge=0)


class RunControlResponse(BaseModel):
    """控制端点结果；重复相同幂等请求返回原结果（idempotent_replay=true）"""
    run_id: int
    status: str
    state_version: int
    idempotent_replay: bool = False
    detail: str = ""


class RunRetryRequest(BaseModel):
    """POST /runs/{id}/retry：只重做失败/失效任务；不隐式提高预算"""
    failed_only: bool = True
    task_ids: Optional[list[str]] = Field(None, max_length=200)
    idempotency_key: Optional[str] = Field(None, max_length=200)


class RunRetryResponse(BaseModel):
    run_id: int
    status: str
    state_version: int
    retried_tasks: int = 0
    idempotent_replay: bool = False


class ReportRevisionEntry(BaseModel):
    """报告不可变版本条目；v1 运行有报告时投影一个 revision_id="legacy" 的伪版本"""
    revision_id: str
    parent_revision: Optional[str] = None
    protocol_hash: Optional[str] = None
    quality_status: str = "unchecked"
    stop_reason: Optional[str] = None
    is_selected: bool = False
    legacy: bool = False
    created_at: datetime


class ReportRevisionListResponse(BaseModel):
    run_id: int
    workflow_version: str
    legacy: bool = True
    items: list[ReportRevisionEntry] = []
    next_cursor: Optional[str] = None


class EvidenceSpanEntry(BaseModel):
    """证据定位（页/节/表/坐标或字符位置 + 原文片段）"""
    page: Optional[int] = None
    section: Optional[str] = None
    table: Optional[str] = None
    bbox: Optional[list[float]] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    quote: str = ""
    location_id: Optional[str] = None
    location_status: Optional[str] = None


class EvidenceDetailResponse(BaseModel):
    """GET /runs/{id}/evidence/{eid}：许可范围内的摘录、文档版本与定位"""
    evidence_id: str
    run_id: int
    asset_id: Optional[str] = None
    spans: list[EvidenceSpanEntry] = []
    relation: Optional[str] = None
    evidence_type: Optional[str] = None
    fact: dict = Field(default_factory=dict)
    subquestion_ids: list[str] = []
    missing_fields: list[str] = []
    comparability: dict = Field(default_factory=dict)
    verification: dict = Field(default_factory=dict)
    document: dict = Field(default_factory=dict)   # content_hash/format/public_available_at/parse_status
    created_at: Optional[datetime] = None


class EvidenceIndexItem(BaseModel):
    evidence_id: str
    evidence_type: Optional[str] = None
    subquestion_ids: list[str] = []
    paper_key: Optional[str] = None
    fact_summary: str = ""
    missing_fields: list[str] = []
    comparability_status: Optional[str] = None
    verification_status: Optional[str] = None
    location_status: Optional[str] = None


class EvidenceIndexResponse(BaseModel):
    run_id: int
    workflow_version: str
    evidence_version: str = ""
    subquestion_id: Optional[str] = None
    cursor: int = 0
    limit: int = 20
    total: int = 0
    next_cursor: Optional[int] = None
    items: list[EvidenceIndexItem] = []


ExplorationRunResponse.model_rebuild()



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
