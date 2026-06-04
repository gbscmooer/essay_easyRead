"""
Paper 数据库模型

SQLModel 模型，映射到 SQLite 的 papers 表。
存储论文的元信息和解析状态，解析结果存储在 AnalysisResult 中。
"""

from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import func


class Paper(SQLModel, table=True):
    """
    论文记录表

    一条记录对应一篇上传过的论文。
    pdf_path 存储上传后的本地文件路径。
    status 跟踪解析进度：pending → extracting → analyzing → done / failed。
    """

    __tablename__ = "papers"

    id: Optional[int] = Field(default=None, primary_key=True)

    # 文件信息
    filename: str  # 原始文件名，如 "attention_is_all_you_need.pdf"
    pdf_path: str  # 服务器存储路径，如 "uploads/abc123.pdf"
    file_size: int  # 文件大小（字节）

    # 元信息（解析后填充）
    title: Optional[str] = None  # AI 提取的论文标题
    venue: Optional[str] = None  # 会议/期刊，如 "CVPR", "NeurIPS"
    year: Optional[int] = None  # 发表年份

    # 状态
    status: str = "pending"  # pending | extracting | analyzing | done | failed
    error_message: Optional[str] = None  # 失败时的错误信息

    # 时间戳
    # created_at: 仅在 INSERT 时设置
    # updated_at: INSERT 和 UPDATE 时都自动设置（通过 SQLAlchemy onupdate）
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(server_default=func.now(), onupdate=func.now()),
    )
