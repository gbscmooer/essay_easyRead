"""
CodeAnalysis 数据模型

存储代码分析结果：文件树、数据流、依赖图、论文-代码映射。
独立于 AnalysisResult，代码分析可单独触发和重新运行。
"""

from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON, ForeignKey, Integer


class CodeAnalysis(SQLModel, table=True):
    """
    代码分析结果表

    与 Paper 表一对一关联。
    存储代码仓库的分析结果，包括文件树、数据流、依赖图和论文-代码映射。
    """

    __tablename__ = "code_analyses"

    id: Optional[int] = Field(default=None, primary_key=True)
    paper_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("papers.id", ondelete="CASCADE"),
            unique=True,
        ),
    )

    # 文件树（JSON）— 缓存的目录结构
    file_tree: dict = Field(default={}, sa_column=Column(JSON))

    # AI：数据流描述
    data_flow: Optional[str] = None

    # 静态分析：模块依赖图（JSON）
    dependency_graph: list[dict] = Field(default=[], sa_column=Column(JSON))
    # 每个元素: {"source": "model.py", "target": "utils.py", "imports": ["func1"]}

    # AI：论文概念 → 代码文件映射（JSON）
    paper_code_mapping: list[dict] = Field(default=[], sa_column=Column(JSON))
    # 每个元素: {"concept": "...", "file": "...", "function": "...", "explanation": "..."}

    # 节点指标（JSON）— fan_in/fan_out 耦合度
    node_metrics: list[dict] = Field(default=[], sa_column=Column(JSON))

    # 状态
    status: str = "pending"  # pending | analyzing | done | failed
    error_message: Optional[str] = None

    # 时间戳
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
