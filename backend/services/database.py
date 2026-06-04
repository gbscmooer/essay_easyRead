"""
数据库服务

SQLite + SQLModel 的数据库初始化和 CRUD 操作。
"""

from sqlmodel import Session


def init_db(database_url: str) -> None:
    """
    初始化数据库。

    创建所有表（如果不存在）。
    在应用启动时调用一次。

    Args:
        database_url: 数据库连接字符串，如 "sqlite:///./data/essay_read.db"
    """
    ...


def create_paper(session: Session, filename: str, pdf_path: str, file_size: int) -> int:
    """
    创建论文记录。

    Args:
        session: 数据库会话
        filename: 原始文件名
        pdf_path: 服务器存储路径
        file_size: 文件大小（字节）

    Returns:
        int: 新创建的论文 ID
    """
    ...


def update_paper_status(session: Session, paper_id: int, status: str, error_message: str = None) -> None:
    """
    更新论文解析状态。

    Args:
        session: 数据库会话
        paper_id: 论文 ID
        status: 新状态（pending | extracting | analyzing | done | failed）
        error_message: 失败时的错误信息
    """
    ...


def update_paper_metadata(session: Session, paper_id: int, title: str, venue: str, year: int) -> None:
    """
    更新论文元信息（解析完成后）。

    Args:
        session: 数据库会话
        paper_id: 论文 ID
        title: 论文标题
        venue: 会议/期刊
        year: 发表年份
    """
    ...


def save_analysis_result(session: Session, paper_id: int, result: dict) -> None:
    """
    保存解析结果。

    将 AI 返回的完整 JSON 存入 AnalysisResult 表。
    如果已存在则更新（upsert）。

    Args:
        session: 数据库会话
        paper_id: 论文 ID
        result: AI 返回的完整解析结果 dict
    """
    ...


def get_paper(session: Session, paper_id: int) -> dict:
    """
    获取单篇论文信息。

    Args:
        session: 数据库会话
        paper_id: 论文 ID

    Returns:
        dict: 论文信息字典

    Raises:
        ValueError: 论文不存在
    """
    ...


def get_analysis(session: Session, paper_id: int) -> dict:
    """
    获取论文的解析结果。

    Args:
        session: 数据库会话
        paper_id: 论文 ID

    Returns:
        dict: 完整的解析结果

    Raises:
        ValueError: 解析结果不存在
    """
    ...


def list_papers(
    session: Session,
    search: str = None,
    venue: str = None,
    year: int = None,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[dict], int]:
    """
    列出论文，支持筛选和分页。

    Args:
        session: 数据库会话
        search: 搜索关键词（匹配标题）
        venue: 会议筛选
        year: 年份筛选
        page: 页码（从 1 开始）
        per_page: 每页数量

    Returns:
        tuple[list[dict], int]: (论文列表, 总数)
    """
    ...


def delete_paper(session: Session, paper_id: int) -> str:
    """
    删除论文及其解析结果。

    Args:
        session: 数据库会话
        paper_id: 论文 ID

    Returns:
        str: 被删除论文的 PDF 文件路径（供调用方删除文件）

    Raises:
        ValueError: 论文不存在
    """
    ...
