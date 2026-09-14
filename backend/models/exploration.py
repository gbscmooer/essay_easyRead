"""
领域探索数据模型（问句驱动的免读领域认知，见 GOAL.md）

ExplorationRun: 一次「领域探索」任务的执行记录与完整产物。
与 ResearchRun（工作区种子扩展）互补：探索从自然语言问句冷启动，不需要任何种子论文。

result 列的完整结构（explorer.py 产出）：
{
  "decompose":  {"intent", "en_queries", "focus_terms", "fallback": bool},
  "papers":     [{key, title, year, citation_count, citation_velocity, surge,
                  cluster, cluster_label, doi, arxiv_id, authors, abstract, source_query}],
  "metrics":    {"total", "by_year", "recent_share", "year_span", "top_cited", "rising",
                  "clusters", "modularity"},
  "graph":      {"nodes", "edges", "stats"}   # ResearchGraph 形状，前端总览视图直接复用
  "synthesis":  ExplorationSynthesis | None   # LLM 跨文献综合（无 Key 为 None）
  "survey_report": {                          # 报告 v2；旧 v1 JSON 仍可读取
      "tables": {"frameworks", "metrics", "tasks", "taxonomy",
                 "experimental_comparisons"},
      "evidence_store": {"evidence_version", "records"},
      "quality_status", "method": {"final_audit", "diagnostics", "budgets"}
  }
}
"""

from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON, Index, text

from .research import ParadigmInfo


# 查新结论的枚举口径：结论只允许落在这些档位上，防止 LLM 自由发挥
NOVELTY_STATUSES = (
    "done_extensively",  # 已被充分实现：多个成熟系统/方法，SOTA 明确
    "done_partial",      # 部分实现：有相关工作但未完全覆盖问题的全部条件
    "emerging",          # 新兴：近 1~2 年开始出现，尚无公认范式
    "gap",               # 空白：检索未发现直接实现（注意与「检索失败」区分）
    "unclear",           # 证据不足以判定
)


class ExplorationVerdict(SQLModel):
    """查新结论：这个 idea 有没有人做。evidence_keys 指向 result.papers 的 key。"""

    status: str = "unclear"          # NOVELTY_STATUSES 之一
    confidence: float = 0.0          # 0~1，证据充分度
    explanation: str = ""            # 2~4 句判定依据（必须引用材料）
    evidence_keys: list[str] = []


class ExplorationSynthesis(SQLModel):
    """LLM 跨文献综合产物（只基于召回材料，铁律见 GOAL.md）。"""

    verdict: Optional[ExplorationVerdict] = None
    maturity: str = ""               # 实现到什么程度：代表系统/SOTA 水平/基准一句话
    landscape_summary: str = ""      # 领域现状一段话
    hotspots: list[str] = []         # 近期热点与突破
    gaps: list[str] = []             # 尚未解决的空白
    follow_ups: list[str] = []       # 建议的后续检索式/相邻问题
    # 子方向流派（cluster_id 与 graph 节点 meta.cluster 对齐，前端范式卡片复用）
    paradigms: list[ParadigmInfo] = []
    markdown_report: str = ""        # 完整 Markdown 报告
    partial: bool = False
    stats: dict = {}


class ExplorationRun(SQLModel, table=True):
    """一次领域探索任务的执行记录（问句维度，不挂工作区）

    academic_survey_v2 扩展（S0 冻结，见 docs/ACADEMIC_SURVEY_AGENT_PLAN_2026-09-08.md §7.1）：
    - workflow_version: legacy_v1（默认，含迁移前旧数据）| academic_survey_v2
    - state_version: 单调递增；v2 状态操作在事务内 compare-and-swap（陈旧版本 409）
    - protocol_asset_id: 指向 research_assets 中冻结的协议版本（v1 为 NULL）
    v2 运行状态在 v1 的 pending/running/done/failed 之外增加
    paused/completed/partial/cancelled（见 research_core.RUN_STATES_V2），与阶段分离。
    """

    __tablename__ = "exploration_runs"

    id: Optional[int] = Field(default=None, primary_key=True)

    question: str                    # 学者的自然语言问句
    depth: str = "standard"          # quick | standard | deep
    status: str = "pending"          # pending | running | done | failed（v2 另见 RUN_STATES_V2）
    current_stage: Optional[str] = None  # decompose | recall | enrich | metrics | synthesize | persist
    error_message: Optional[str] = None

    # 实际使用的检索源（scopus/openalex/…，透明度用）
    provider: str = ""
    options: dict = Field(default_factory=dict, sa_column=Column(JSON))

    decompose: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    result: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    # 断点续传检查点（legacy v1）：已完成阶段游标 + 报告逐篇提取/共享证据缓存。
    # {"completed_stages": [...], "report": {"selected_keys": [...],
    #  "extractions": {paper_key: 提取结果}, "evidence_store": {...}}, "updated_at": iso}
    # run 到达 done 后清空；paused/failed 的 run 由此支持从断点续跑。
    checkpoint: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    workflow_version: str = "legacy_v1"
    state_version: int = 0           # v2 创建时置 1，此后每次 CAS 状态变更 +1
    protocol_asset_id: Optional[str] = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ExplorationMessage(SQLModel, table=True):
    """一次追问及其回答；证据快照独立保存，不覆盖最初探索结果。"""

    __tablename__ = "exploration_messages"
    __table_args__ = (Index("uq_exploration_active_message", "run_id", unique=True,
                           sqlite_where=text("status IN ('pending', 'running')")),)

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="exploration_runs.id", index=True)
    question: str
    answer: str = ""
    status: str = "pending"
    search_more: bool = False
    selected_keys: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    papers: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    evidence_keys: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    context: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
