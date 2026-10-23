"""
Workspace 数据模型

工作区用于组织论文，支持多对多关系。
"""

from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import ForeignKey, Integer, DateTime
from sqlalchemy import func


class Workspace(SQLModel, table=True):
    """工作区表"""

    __tablename__ = "workspaces"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = "未命名工作区"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class WorkspacePaper(SQLModel, table=True):
    """工作区-论文关联表（多对多）"""

    __tablename__ = "workspace_papers"

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(
        sa_column=Column(Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True),
    )
    paper_id: int = Field(
        sa_column=Column(Integer, ForeignKey("papers.id", ondelete="CASCADE"), index=True),
    )
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
