from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import ForeignKey, Integer, DateTime, String


class Tag(SQLModel, table=True):
    __tablename__ = "tags"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(sa_column=Column(String(100), unique=True, nullable=False))
    color: str = "#3b82f6"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PaperTag(SQLModel, table=True):
    __tablename__ = "paper_tags"

    id: Optional[int] = Field(default=None, primary_key=True)
    paper_id: int = Field(
        sa_column=Column(Integer, ForeignKey("papers.id", ondelete="CASCADE"), index=True),
    )
    tag_id: int = Field(
        sa_column=Column(Integer, ForeignKey("tags.id", ondelete="CASCADE"), index=True),
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
