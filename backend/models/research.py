"""
深度研究数据模型

ResearchRun: 一次深度研究任务的执行记录
ResearchGraph: 工作区最新图谱快照
"""

from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON, ForeignKey, Index, Integer


# ── 领域认知综合（stage_synthesize）的结构化产物 ───────────────────────────
# 这些是纯 Pydantic 校验模型：持久化时整体塞进 ResearchGraph.synthesis（JSON 列）。

NODE_ROLES = ("landmark", "rising_star", "benchmark", "tool_sota", "survey", "regular")
EDGE_RELATIONS = ("improves", "replaces", "evaluates_on", "inspires", "cites")


class NodeSemantic(SQLModel):
    """节点学术角色研判（Task 1）：这篇论文在领域里是什么身份。"""

    role: str = "regular"            # NODE_ROLES 之一
    short_takeaway: str = ""         # 一句话核心创新（≤25 字）
    key_techniques: list[str] = []   # 2~3 个核心方法标签


class EdgeSemantic(SQLModel):
    """连边因果研判（Task 2）：仅 cites 边（有向引用 + 上下文），related 边不猜因果。"""

    relation_type: str = "cites"     # EDGE_RELATIONS 之一
    relation_summary: str = ""       # 一句话因果说明


class ParadigmInfo(SQLModel):
    """技术流派（Task 3）：Louvain 簇的学术具象化。"""

    cluster_id: int = 0
    name: str = ""
    core_philosophy: str = ""
    pros: list[str] = []
    cons: list[str] = []
    representative_node_ids: list[str] = []   # 图谱节点 id（"paper:{id}"）


class BriefingMilestone(SQLModel):
    year: str = ""                   # 年份或年份区间（字符串以兼容 "2020-2021"）
    event: str = ""


class ExecutiveBriefing(SQLModel):
    """领域全景简报（Task 4）：免读单篇的领域认知。"""

    domain_problem: str = ""                 # 领域本质与核心矛盾
    milestone_timeline: list[BriefingMilestone] = []
    benchmark_standards: list[str] = []      # 公认评测体系
    recent_hotspots: list[str] = []          # 近期爆发热点
    open_challenges: list[str] = []          # 未解决的公开挑战
    markdown_report: str = ""                # 完整 Markdown 综述


class GraphSynthesis(SQLModel):
    """ResearchGraph.synthesis 列的完整结构。"""

    briefing: Optional[ExecutiveBriefing] = None
    paradigms: list[ParadigmInfo] = []
    enriched_nodes: dict[str, NodeSemantic] = {}   # node_id → 语义
    enriched_edges: dict[str, EdgeSemantic] = {}   # "source>target" → 语义
    stats: dict = {}                               # 打标统计 / 模型 / 时间
    partial: bool = False                          # 某任务失败时仍保留部分产物


class ResearchRun(SQLModel, table=True):
    """一次深度研究任务的执行记录（工作区维度）"""
    __tablename__ = "research_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(
        sa_column=Column(Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True),
    )

    mode: str = "local"        # local | online
    status: str = "pending"    # pending | running | done | failed
    current_stage: Optional[str] = None  # collect | expand | expand_arxiv | chunk | embed | digest | graph | synthesize | persist
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

    nodes: list = Field(default=[], sa_column=Column(JSON))   # List[GraphNode]
    edges: list = Field(default=[], sa_column=Column(JSON))   # List[GraphEdge]
    stats: dict = Field(default={}, sa_column=Column(JSON))   # {node_count, edge_count, by_kind, ...}
    # 领域认知综合产物（stage_synthesize）：GraphSynthesis 结构；旧图谱 / 未开启时为 NULL
    synthesis: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PaperReference(SQLModel, table=True):
    """真实引用关系：citing → cited（与 parent_paper_id 发现谱系分离）。"""
    __tablename__ = "paper_references"
    __table_args__ = (
        Index("ix_paper_refs_citing", "citing_paper_id"),
        Index("ix_paper_refs_cited", "cited_paper_id"),
        Index("ix_paper_refs_cited_doi", "cited_doi"),
        Index("ix_paper_refs_cited_arxiv", "cited_arxiv_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)

    citing_paper_id: int = Field(
        sa_column=Column(Integer, ForeignKey("papers.id", ondelete="CASCADE"), nullable=False),
    )
    cited_paper_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("papers.id", ondelete="SET NULL"), nullable=True),
    )

    cited_doi: Optional[str] = None
    cited_arxiv_id: Optional[str] = None
    cited_title: Optional[str] = None

    frequency: int = 1
    contexts: list = Field(default=[], sa_column=Column(JSON))
    intents: list = Field(default=[], sa_column=Column(JSON))

    source: str = "grobid"       # grobid | openalex | s2 | regex
    confidence: float = 1.0

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
