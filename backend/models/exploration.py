"""
研究任务数据模型（单循环：检索 → 判定 → 展开 → 作答，见 services/deep_research/loop）

ExplorationRun.result（runner.snapshot 产出）：
{
  "question", "queries": [...],
  "papers":  [选中论文，按 score 降序；字段见 loop/papers.py：key/title/abstract/year/…/origin/parent/via/depth/label/score],
  "crawl":   {"config", "phase", "layer", "stop_reason", "candidates", "selected", "expanded",
              "trace": [每个动作一条], "selector", "crawler", "sources", "by_origin", "by_depth"},
  "metrics", "graph",                       # landscape：计量、聚类、图谱（只覆盖选中论文）
  "answer":  {"status", "text", "references", "check", "agent"},
  "sources": {"preflight", "warnings", "stage_timeline", "token_usage", "elapsed_s", …输入选项},
}
checkpoint = LoopState.dump()（断点续跑；完成后清空）。
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import JSON, Index, text
from sqlmodel import Column, Field, SQLModel


class ExplorationRun(SQLModel, table=True):
    __tablename__ = "exploration_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    question: str
    depth: str = "standard"          # lite | quick | standard | deep
    status: str = "pending"          # pending | running | paused | done | failed | cancelled
    current_stage: Optional[str] = None  # search | expand | landscape | answer
    error_message: Optional[str] = None
    provider: str = ""
    options: dict = Field(default_factory=dict, sa_column=Column(JSON))
    decompose: Optional[dict] = Field(default=None, sa_column=Column(JSON))   # {"queries": [...]}
    result: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    checkpoint: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    # 运行实例身份：创建时落库的不可复用 UUID（数字 id 删除后可被 SQLite 复用，用量归因以本字段为准）
    run_instance_id: str = Field(default_factory=lambda: uuid4().hex, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ExplorationMessage(SQLModel, table=True):
    """一次追问及其回答；证据快照独立保存，不覆盖研究结果。"""

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
