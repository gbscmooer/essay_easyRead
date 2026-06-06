"""
Paper 数据库模型

SQLModel 模型，映射到 SQLite 的 papers 表。
存储论文的元信息和解析状态，解析结果存储在 AnalysisResult 中。
"""

from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field


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
    # pending | extracting | analyzing | done | failed
    # indexed: 深度研究扩展下载的论文，只跑了 PDF 提取 + chunk + embed，没跑 LLM 分析（level=2 专用）
    status: str = "pending"
    error_message: Optional[str] = None  # 失败时的错误信息

    # 代码仓库
    code_url: Optional[str] = None  # AI 提取的 GitHub/GitLab URL
    code_status: str = "none"  # none | cloning | ready | failed
    code_path: Optional[str] = None  # 本地 clone 路径
    code_summary: Optional[str] = None  # AI 生成的 README 摘要

    # 深度研究扩展字段
    source: str = "uploaded"               # uploaded | expanded
    level: int = 1                         # 1 = 用户手动加入工作区; 2 = 联网扩展下载; 3+ = 多跳扩展
    parent_paper_id: Optional[int] = None  # 仅 expanded：seed 论文 id
    external_id: Optional[str] = None      # S2 paperId 或 arXivId，用于去重

    # 时间戳 — 在 database.py 的 CRUD 中手动设置
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
