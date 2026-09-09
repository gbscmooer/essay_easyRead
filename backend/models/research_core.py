"""
academic_survey_v2 研究核心契约（S0 冻结，见 docs/ACADEMIC_SURVEY_AGENT_PLAN_2026-09-08.md §7）

本模块只冻结数据形状，不含业务编排（S1–S8 负责）：
- 契约常量：工作流版本、任务/运行/质量/身份/核验状态、预算资源口径。
- Core 交换契约：ResearchProtocol / SearchRecord / IdentityDecision / ResolvedLocation /
  EvidenceSpan / CoverageGap 等（Pydantic 形状，跨模块传递用）。
- SQLModel 持久化表：research_tasks / research_task_attempts / research_assets /
  research_evidence / research_relations / research_claims / research_report_revisions /
  research_budget_events / research_events / research_identity_decisions。

冻结约定：
- 所有表用 int 自增主键 + 对外稳定字符串 ID（uuid hex），外部引用一律用字符串 ID。
- 身份四层（WorkFamily/WorkVersion/SourceRecord/DocumentAsset）复用现有 Paper 身份数据，
  本模块用 family_key/version_key 引用（"doi:10.x/x" / "arxiv:2312.10997" / "paper:{id}" 等），
  桥接与传递归并由 S1 实现；同标题不同作者不得自动合并。
- 表行由各业务 sub 写入；schema 变更必须经 S0（共享文件所有权）。
- 外部字符串 ID 保留值 "legacy"（v1 兼容投影的伪 revision），业务侧不得占用。
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import JSON, Index, text
from sqlmodel import SQLModel, Field, Column


# ============================================================
# 契约常量（值即数据库存储值；变更需 S0 评审）
# ============================================================

WORKFLOW_LEGACY_V1 = "legacy_v1"
WORKFLOW_ACADEMIC_SURVEY_V2 = "academic_survey_v2"
WORKFLOW_VERSIONS = (WORKFLOW_LEGACY_V1, WORKFLOW_ACADEMIC_SURVEY_V2)

# 任务状态（计划 §7.1）
TASK_STATES = ("pending", "ready", "running", "succeeded", "failed", "skipped", "cancelled")
# v2 运行状态（在 v1 pending/running/done/failed 之外增加；与阶段分离）
RUN_STATES_V2 = ("pending", "running", "paused", "completed", "partial", "failed", "cancelled")
# 活动态：可能出现 worker 租约、需要豁免启动自愈
RUN_ACTIVE_STATES_V2 = ("pending", "running", "paused")
# 可安全重试的运行终态
RUN_RETRYABLE_STATES_V2 = ("failed", "partial", "cancelled")

# 质量状态独立于运行状态；不能自动把内部模型检查提升为 human_reviewed
QUALITY_STATUSES = ("unchecked", "internally_checked", "needs_review", "human_reviewed")

# 身份判定（S1）：allow/deny/unresolved
IDENTITY_DECISIONS = ("allow", "deny", "unresolved")

# 证据/关系核验状态（计划 §6.5）：摘要只能支撑摘要级陈述；
# 实验细节、性能比较、机制归因需要对应正文/表格证据
VERIFICATION_STATES = (
    "abstract_supported", "fulltext_supported", "unverified", "contradicted", "insufficient",
)

# claim 类别
CLAIM_KINDS = ("fact", "inference", "hypothesis")

# 预算资源口径（guideline，provider_scope 细分如 "http_requests:scopus" 由 S3 记账）
BUDGET_RESOURCES = ("llm_tokens", "llm_calls", "http_requests", "download_bytes", "wall_clock_minutes")

# 搜索记录状态（计划 §6.1）：请求失败不得写成"该方向未发现研究"
SEARCH_STATUSES = ("ok_empty", "partial", "auth_error", "rate_limited", "quota_exhausted", "timeout", "error")

# research_assets.content_kind 口径（资产均为不可变；重写=新行+旧行 superseded）
ASSET_KINDS = (
    "document",        # S2：下载/解析的文献文档（PDF/XML/HTML）
    "protocol",        # S5：冻结协议版本（ResearchProtocol JSON）
    "search_record",   # S5：检索审计记录（SearchRecord JSON）
    "coverage_snapshot",  # S5：覆盖矩阵快照（subquestions + CoverageGap 列表）
    "report",          # S6：报告 revision 内容
    "manifest",        # S3/S6：资产清单（hash 汇总）
)
ASSET_STATUSES = ("active", "quarantined", "superseded", "orphaned")

# 事件类型注册（research_events.type；扩展需登记，payload 保持小型）
EVENT_TYPES = (
    "run_created", "run_started", "run_paused", "run_resumed", "run_cancelled",
    "run_retry", "run_state_changed", "task_state_changed",
    "stage_started", "stage_finished", "report_revision_created",
    "budget_stop", "identity_decision", "note",
)


def _new_id() -> str:
    return uuid4().hex


# ============================================================
# Core 交换契约（Pydantic 形状）
# ============================================================


class ProtocolSubquestion(SQLModel):
    """研究协议子问题：5–8 个初始子问题为实验默认值，不预置标杆答案。"""

    subquestion_id: str
    question: str
    facet: str = ""            # 语义 facet：mechanism/comparison/evaluation/condition/failure 等
    required: bool = False     # 必答子问题（停止条件：必答满足最低证据要求才进入写作）
    status: str = "open"       # open | in_progress | sufficient | insufficient_at_stop


class ResearchProtocol(SQLModel):
    """冻结协议（计划 §7.1）。每次修改产生新版本（新 protocol 资产行），不丢历史。"""

    protocol_id: Optional[str] = None
    version: int = 1
    question: str
    subquestions: list[ProtocolSubquestion] = []
    eligibility: dict = {}     # 纳入/排除标准、语言、文献类型
    content_cutoff: Optional[datetime] = None   # 内容时间门禁（含当日；历史轨道用）
    search_time: Optional[datetime] = None      # 检索日期
    source_policy: dict = {}   # 允许来源、原始证据优先级、扩展跳数/seed 上限
    budget: dict = {}          # resource -> limit；写作/核验预留单独键（如 reserved_for_writing）
    stop_rules: list[dict] = []
    hash: Optional[str] = None            # 内容 hash 由写入方计算并冻结
    change_reason: Optional[str] = None   # 协议变更理由（首个版本为初始冻结）


class SearchRecord(SQLModel):
    """单次检索的审计记录（计划 §7.1/§6.1）。持久化为 content_kind=search_record 资产。"""

    search_id: str
    subquestion_id: Optional[str] = None
    query_raw: str             # 原始检索式
    query_compiled: str = ""   # 源语法编译后表达式
    provider: str              # scopus / openalex / arxiv / ...
    capability: str = "search"  # search / abstract / references / citations
    facet: str = ""
    date_filter: Optional[dict] = None
    cursor: Optional[str] = None
    total_if_known: Optional[int] = None
    fetched_count: int = 0
    status: str = "ok_empty"   # SEARCH_STATUSES 之一
    cost: Optional[dict] = None    # 请求数/字节等（不含密钥）
    result_ids: list[str] = []     # 命中候选的 family/source 标识


class IdentityDecision(SQLModel):
    """身份判定（S1）：所有模型输入入口前校验，deny 只回通用状态给执行器。"""

    decision_id: str
    family_key: Optional[str] = None
    version_key: Optional[str] = None
    source_keys: list[str] = []
    decision: str              # IDENTITY_DECISIONS 之一
    policy_hash: Optional[str] = None
    reason: str = ""
    attestation: Optional[dict] = None   # 依据（解析出的别名、桥接证据）
    expires_at: Optional[datetime] = None


class ResolvedLocation(SQLModel):
    """内容位置解析结果（S2）。持久化于 DocumentAsset.source_record["locations"]。"""

    family_key: Optional[str] = None
    version_key: Optional[str] = None
    url: str
    provider: str
    content_kind: str = "pdf"       # pdf | xml | html
    access_kind: str = "unknown"    # open | subscription | unknown
    identity_attestation: Optional[dict] = None
    license_if_known: Optional[str] = None
    resolution_status: str = "resolved"  # resolved | unavailable | identity_unresolved | denied


class EvidenceSpan(SQLModel):
    """证据定位单元：原文片段 + 页/节/表/坐标或字符位置 + 归一化映射。"""

    page: Optional[int] = None
    section: Optional[str] = None
    table: Optional[str] = None            # 表标识（如 "Table III"）
    bbox: Optional[list[float]] = None     # PDF 坐标 [x0, y0, x1, y1]
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    quote: str = ""                        # 原文片段
    quote_normalized: Optional[str] = None  # NFKC/空白/跨行断词归一后（程序匹配用）


class CoverageGap(SQLModel):
    """覆盖缺口（S5 Critic 输出；持久化于 coverage_snapshot 资产，不包含评分器缺失名单）。"""

    gap_id: str
    subquestion_id: Optional[str] = None
    severity: str = "medium"      # high | medium | low
    missing_evidence_kind: str = ""
    current_evidence: list[str] = []   # 已有 evidence/relation ID
    next_action: str = ""
    status: str = "open"          # open | dispatched | resolved | wont_resolve
    resolved_by: Optional[str] = None   # task_id


# ============================================================
# SQLModel 持久化表（写入方：S1–S6；schema 变更经 S0）
# ============================================================


class ResearchTask(SQLModel, table=True):
    """任务账本行（计划 §5.2：任务账本是执行真源，checkpoint 只存调度位置）。

    幂等键唯一性以 (run_id, idempotency_key) 生效：同一 run 内防止重复调度；
    两个 run 对相同冻结输入（任务类型+协议hash+文档hash+子问题+模型/prompt版本）
    各建独立账本，互不冲突。跨运行的内容复用由资产层 content_hash 承担，
    不用执行身份承担。
    """

    __tablename__ = "research_tasks"
    __table_args__ = (
        Index("ix_research_tasks_run_state", "run_id", "state"),
        Index("uq_research_tasks_run_idempotency", "run_id", "idempotency_key", unique=True,
              sqlite_where=text("idempotency_key IS NOT NULL")),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: str = Field(default_factory=_new_id, unique=True, index=True)
    run_id: int = Field(foreign_key="exploration_runs.id")
    kind: str                                  # search/screen/identity/resolve/download/parse/
                                               # extract/verify/coverage/synthesize/audit/...
    subquestion_id: Optional[str] = None
    state: str = "pending"                     # TASK_STATES 之一
    input_asset_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    dependencies: list[str] = Field(default_factory=list, sa_column=Column(JSON))  # task_id 列表
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))           # 冻结的任务输入
    idempotency_key: Optional[str] = None      # run 内唯一；见类 docstring 的构成说明
    result_asset_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    lease_owner: Optional[str] = None          # worker 标识（S3）
    lease_expires_at: Optional[datetime] = None
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResearchTaskAttempt(SQLModel, table=True):
    """单次执行尝试（不保存密钥；超时/截断/解析失败与语义不足分开）。"""

    __tablename__ = "research_task_attempts"
    __table_args__ = (Index("ix_research_task_attempts_task", "task_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    attempt_id: str = Field(default_factory=_new_id, unique=True)
    task_id: str = Field(foreign_key="research_tasks.task_id")
    run_id: int = Field(foreign_key="exploration_runs.id", index=True)
    stage: str = ""
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    model_name: Optional[str] = None      # 模型名（不含凭证）
    prompt_version: Optional[str] = None
    input_hash: Optional[str] = None
    usage: dict = Field(default_factory=dict, sa_column=Column(JSON))  # token/请求/字节用量
    error_code: Optional[str] = None
    retryable: Optional[bool] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResearchAsset(SQLModel, table=True):
    """不可变资产（document/protocol/search_record/coverage_snapshot/report/manifest）。

    大文件先以 hash 写不可变临时资产再登记有效引用；重写内容 = 新行，
    旧行置 superseded，由异步清理回收孤立文件（S3）。
    """

    __tablename__ = "research_assets"
    __table_args__ = (Index("ix_research_assets_run_kind", "run_id", "content_kind"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    asset_id: str = Field(default_factory=_new_id, unique=True, index=True)
    run_id: int = Field(foreign_key="exploration_runs.id")
    content_kind: str                          # ASSET_KINDS 之一
    format: str = "json"                       # pdf | xml | html | json | markdown | text
    family_key: Optional[str] = Field(default=None, index=True)
    version_key: Optional[str] = None
    content_hash: Optional[str] = Field(default=None, index=True)
    source_record: dict = Field(default_factory=dict, sa_column=Column(JSON))
    storage_path: Optional[str] = None         # 大文件落盘路径
    inline_payload: Optional[dict] = Field(default=None, sa_column=Column(JSON))  # 小型 JSON 资产
    size_bytes: Optional[int] = None
    retrieved_at: Optional[datetime] = None
    public_available_at: Optional[datetime] = None   # 内容资产版本日期（时间门禁；未知为 NULL）
    parser: Optional[str] = None
    parse_status: str = "pending"              # pending | parsed | degraded | failed
    structure_index: Optional[dict] = Field(default=None, sa_column=Column(JSON))  # 页/节/表区块索引
    status: str = "active"                     # ASSET_STATUSES 之一
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResearchEvidence(SQLModel, table=True):
    """EvidenceBundle：同一结论允许引用多个原文位置（表头+单元格+脚注）。"""

    __tablename__ = "research_evidence"
    __table_args__ = (Index("ix_research_evidence_run", "run_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    evidence_id: str = Field(default_factory=_new_id, unique=True, index=True)
    run_id: int = Field(foreign_key="exploration_runs.id")
    asset_id: str = Field(foreign_key="research_assets.asset_id")
    doc_content_hash: Optional[str] = None     # 冗余 asset.content_hash，防资产被替换后引用漂移
    spans: list[dict] = Field(default_factory=list, sa_column=Column(JSON))  # EvidenceSpan 字典列表
    relation: Optional[str] = None             # bundle 内多 span 的语义关系说明
    context_note: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResearchRelation(SQLModel, table=True):
    """ExtractedRelation：类型化实体关系（方法/任务/数据集/指标/benchmark…，S4）。"""

    __tablename__ = "research_relations"
    __table_args__ = (Index("ix_research_relations_run_state", "run_id", "verification_state"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    relation_id: str = Field(default_factory=_new_id, unique=True, index=True)
    run_id: int = Field(foreign_key="exploration_runs.id")
    subquestion_id: Optional[str] = None
    subject_type: str = ""
    subject: str = ""
    predicate: str = ""
    object_type: str = ""
    object: str = ""
    scope: Optional[str] = None                # 适用条件/范围
    conditions: dict = Field(default_factory=dict, sa_column=Column(JSON))  # 实验设置（模型/语料/top-k…）
    evidence_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    verification_state: str = "unverified"     # VERIFICATION_STATES 之一
    verification_note: Optional[str] = None
    reviewer_version: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResearchClaim(SQLModel, table=True):
    """报告事实论断（S6）：验证绑定 text_hash + evidence 版本，正文改写即失效重跑。"""

    __tablename__ = "research_claims"
    __table_args__ = (Index("ix_research_claims_run", "run_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    claim_id: str = Field(default_factory=_new_id, unique=True, index=True)
    run_id: int = Field(foreign_key="exploration_runs.id")
    revision_id: Optional[str] = Field(default=None, index=True)  # research_report_revisions.revision_id
    text: str = ""
    text_hash: str = ""
    kind: str = "fact"                         # CLAIM_KINDS 之一
    scope: Optional[str] = None                # 适用范围/时间
    support_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    counterevidence_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    verification: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResearchReportRevision(SQLModel, table=True):
    """报告不可变版本（S6）。旧 GET /runs/{id}/report 返回当前选定 revision。"""

    __tablename__ = "research_report_revisions"
    __table_args__ = (Index("ix_research_report_revisions_run", "run_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    revision_id: str = Field(default_factory=_new_id, unique=True, index=True)
    run_id: int = Field(foreign_key="exploration_runs.id")
    parent_revision: Optional[str] = None
    report_asset_id: str = Field(foreign_key="research_assets.asset_id")
    protocol_hash: Optional[str] = None
    asset_manifest_hash: Optional[str] = None
    workflow_version: str = WORKFLOW_ACADEMIC_SURVEY_V2
    model_version: Optional[str] = None
    prompt_version: Optional[str] = None
    is_selected: bool = False                  # 同一 run 至多一个 selected（切换不改内容）
    quality_status: str = "unchecked"          # QUALITY_STATUSES 之一
    stop_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResearchBudgetEvent(SQLModel, table=True):
    """预算账本事件（S3）：先原子 reserve、完成后 settle；失败/取消释放未用预留。

    聚合口径（S0 冻结）：
      used                 = Σ delta_used
      reserved_outstanding = Σ delta_reserved − Σ delta_used − Σ delta_refunded
    usage_unknown 窗口（响应丢失）按上界计入 delta_used 且 usage_known=false，不得归零。
    """

    __tablename__ = "research_budget_events"
    __table_args__ = (Index("ix_research_budget_events_run", "run_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)   # 自增即账本顺序
    run_id: int = Field(foreign_key="exploration_runs.id")
    resource: str                              # BUDGET_RESOURCES 或 provider_scope 细分
    task_id: Optional[str] = None
    attempt_id: Optional[str] = None
    provider_scope: Optional[str] = None       # 如 "http_requests:scopus"
    delta_reserved: int = 0
    delta_used: int = 0
    delta_refunded: int = 0
    unit: str = "count"                        # count | token | byte | minute
    price_version: Optional[str] = None
    usage_known: bool = True
    note: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResearchEvent(SQLModel, table=True):
    """持久化事件流（S0 冻结）：SSE 只是订阅持久化事件，不作为任务运行的唯一状态来源。

    断点续订按 event_id / 自增 id；payload 保持小型（文档、证据、报告放资产）。
    """

    __tablename__ = "research_events"
    __table_args__ = (Index("ix_research_events_run_id_", "run_id", "id"),)

    id: Optional[int] = Field(default=None, primary_key=True)   # 自增即流顺序
    event_id: str = Field(default_factory=_new_id, unique=True)
    run_id: int = Field(foreign_key="exploration_runs.id")
    task_id: Optional[str] = None
    type: str                                  # EVENT_TYPES 之一
    payload_version: int = 1
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResearchIdentityDecision(SQLModel, table=True):
    """身份判定持久层（S1）：每个模型/下载入口前的门禁审计；deny 细节不回传执行器。"""

    __tablename__ = "research_identity_decisions"
    __table_args__ = (Index("ix_research_identity_run_family", "run_id", "family_key"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    decision_id: str = Field(default_factory=_new_id, unique=True)
    run_id: Optional[int] = Field(default=None, index=True)   # 全局策略判定可无 run
    family_key: Optional[str] = None
    version_key: Optional[str] = None
    source_keys: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    decision: str                              # IDENTITY_DECISIONS 之一
    policy_hash: Optional[str] = None
    reason: str = ""
    attestation: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    expires_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
