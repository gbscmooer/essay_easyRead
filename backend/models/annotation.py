from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import ForeignKey, Integer, Float, Text


class Annotation(SQLModel, table=True):
    __tablename__ = "annotations"

    id: Optional[int] = Field(default=None, primary_key=True)
    paper_id: int = Field(
        sa_column=Column(Integer, ForeignKey("papers.id", ondelete="CASCADE"), index=True),
    )
    page_number: int = 1
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    annotation_type: str = "highlight"
    color: str = "#fde047"
    text: str = Field(sa_column=Column(Text, default=""))
    group_id: Optional[str] = Field(default=None, max_length=64, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
