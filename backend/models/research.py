"""
深度研究数据模型

ResearchRun: 一次深度研究任务的执行记录
ResearchGraph: 工作区最新图谱快照
"""

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

    nodes: list = Field(default=[], sa_column=Column(JSON))   # List[GraphNode]
    edges: list = Field(default=[], sa_column=Column(JSON))   # List[GraphEdge]
    stats: dict = Field(default={}, sa_column=Column(JSON))   # {node_count, edge_count, by_kind, ...}

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
