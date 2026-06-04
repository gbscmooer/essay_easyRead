"""
数据库服务

SQLite + SQLModel 的数据库初始化和 CRUD 操作。
"""

from sqlmodel import Session, create_engine, SQLModel, select, col
from sqlalchemy import func

from ..models.paper import Paper
from ..models.analysis import AnalysisResult


def _migrate_schema(engine) -> None:
    """简易 schema 迁移：为已有表添加缺失的列"""
    import logging
    logger = logging.getLogger(__name__)

    with engine.connect() as conn:
        # 检查 analysis_results 表的列
        try:
            result = conn.exec_driver_sql("PRAGMA table_info(analysis_results)")
            existing_cols = {row[1] for row in result.fetchall()}
        except Exception:
            return

        # 添加缺失的列
        migrations = [
            ("outline", "JSON DEFAULT '[]'"),
        ]
        for col_name, col_def in migrations:
            if col_name not in existing_cols:
                try:
                    conn.exec_driver_sql(f"ALTER TABLE analysis_results ADD COLUMN {col_name} {col_def}")
                    conn.commit()
                    logger.info(f"Migration: added column {col_name} to analysis_results")
                except Exception as e:
                    logger.warning(f"Migration failed for {col_name}: {e}")


def init_db(database_url: str) -> None:
    """
    初始化数据库。

    创建所有表（如果不存在）。
    在应用启动时调用一次。

    Args:
        database_url: 数据库连接字符串，如 "sqlite:///./data/essay_read.db"
    """
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    # 简易迁移：为已有表添加新列
    _migrate_schema(engine)


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
    paper = Paper(filename=filename, pdf_path=pdf_path, file_size=file_size)
    session.add(paper)
    session.commit()
    session.refresh(paper)
    return paper.id


def update_paper_status(session: Session, paper_id: int, status: str, error_message: str = None) -> None:
    """
    更新论文解析状态。

    Args:
        session: 数据库会话
        paper_id: 论文 ID
        status: 新状态（pending | extracting | analyzing | done | failed）
        error_message: 失败时的错误信息
    """
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise ValueError(f"Paper {paper_id} not found")
    paper.status = status
    paper.error_message = error_message
    session.add(paper)
    session.commit()


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
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise ValueError(f"Paper {paper_id} not found")
    paper.title = title
    paper.venue = venue
    paper.year = year
    session.add(paper)
    session.commit()


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
    # 查找已有的分析结果
    statement = select(AnalysisResult).where(AnalysisResult.paper_id == paper_id)
    existing = session.exec(statement).first()

    method_data = result.get("method", {})
    # 从 method 中提取 mermaid_chart，因为它需要单独存储
    mermaid = method_data.pop("mermaid_chart", None)

    if existing:
        existing.title = result.get("title", "")
        existing.venue = result.get("venue", "")
        existing.year = result.get("year", 0)
        existing.one_sentence = result.get("one_sentence", "")
        existing.problem = result.get("problem", "")
        existing.key_contributions = result.get("key_contributions", [])
        existing.outline = result.get("outline", [])
        existing.method = method_data
        existing.experiments = result.get("experiments", {})
        existing.code_mapping = result.get("code_mapping", [])
        existing.interview_qa = result.get("interview_qa", [])
        existing.limitations = result.get("limitations", "")
        existing.related_papers = result.get("related_papers", [])
        existing.mermaid_chart = mermaid
        session.add(existing)
    else:
        analysis = AnalysisResult(
            paper_id=paper_id,
            title=result.get("title", ""),
            venue=result.get("venue", ""),
            year=result.get("year", 0),
            one_sentence=result.get("one_sentence", ""),
            problem=result.get("problem", ""),
            key_contributions=result.get("key_contributions", []),
            outline=result.get("outline", []),
            method=method_data,
            experiments=result.get("experiments", {}),
            code_mapping=result.get("code_mapping", []),
            interview_qa=result.get("interview_qa", []),
            limitations=result.get("limitations", ""),
            related_papers=result.get("related_papers", []),
            mermaid_chart=mermaid,
        )
        session.add(analysis)

    session.commit()


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
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise ValueError(f"Paper {paper_id} not found")
    return {
        "id": paper.id,
        "filename": paper.filename,
        "title": paper.title,
        "venue": paper.venue,
        "year": paper.year,
        "status": paper.status,
        "error_message": paper.error_message,
        "created_at": paper.created_at,
        "updated_at": paper.updated_at,
    }


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
    statement = select(AnalysisResult).where(AnalysisResult.paper_id == paper_id)
    analysis = session.exec(statement).first()
    if analysis is None:
        raise ValueError(f"Analysis result for paper {paper_id} not found")
    return {
        "paper_id": analysis.paper_id,
        "title": analysis.title,
        "venue": analysis.venue,
        "year": analysis.year,
        "one_sentence": analysis.one_sentence,
        "problem": analysis.problem,
        "key_contributions": analysis.key_contributions,
        "outline": analysis.outline if analysis.outline else [],
        "method": analysis.method,
        "experiments": analysis.experiments,
        "code_mapping": analysis.code_mapping,
        "interview_qa": analysis.interview_qa,
        "limitations": analysis.limitations,
        "related_papers": analysis.related_papers,
        "mermaid_chart": analysis.mermaid_chart,
    }


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
    statement = select(Paper)

    # 应用筛选条件
    if search:
        statement = statement.where(col(Paper.title).contains(search))
    if venue:
        statement = statement.where(Paper.venue == venue)
    if year is not None:
        statement = statement.where(Paper.year == year)

    # 获取总数
    count_statement = select(func.count()).select_from(statement.subquery())
    total = session.exec(count_statement).one()

    # 应用排序和分页
    statement = statement.order_by(Paper.created_at.desc())
    statement = statement.offset((page - 1) * per_page).limit(per_page)

    papers = session.exec(statement).all()
    result = [
        {
            "id": p.id,
            "filename": p.filename,
            "title": p.title,
            "venue": p.venue,
            "year": p.year,
            "status": p.status,
            "error_message": p.error_message,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        }
        for p in papers
    ]

    return result, total


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
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise ValueError(f"Paper {paper_id} not found")

    pdf_path = paper.pdf_path

    # AnalysisResult 通过 CASCADE 会自动删除，但显式删除更安全
    statement = select(AnalysisResult).where(AnalysisResult.paper_id == paper_id)
    analysis = session.exec(statement).first()
    if analysis:
        session.delete(analysis)

    session.delete(paper)
    session.commit()

    return pdf_path
