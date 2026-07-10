"""
深度研究数据模型

ResearchRun: 一次深度研究任务的执行记录
ResearchGraph: 工作区最新图谱快照
"""

from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON, ForeignKey, Index, Integer


class ResearchRun(SQLModel, table=True):
    """一次深度研究任务的执行记录（工作区维度）"""
    __tablename__ = "research_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(
        sa_column=Column(Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True),
    )

    mode: str = "local"        # local | online
    status: str = "pending"    # pending | running | done | failed
    current_stage: Optional[str] = None  # collect | expand | expand_arxiv | chunk | embed | digest | graph | persist
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
