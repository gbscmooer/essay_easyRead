"""
数据库服务

SQLite + SQLModel 的数据库初始化和 CRUD 操作。
"""

import re
from datetime import datetime, timezone
from typing import Optional
from sqlmodel import Session, create_engine, SQLModel, select, col
from sqlalchemy import func, event

# Column name validation pattern: only safe identifiers
_SAFE_COLUMN_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_column_name(name: str) -> None:
    """Validate column name to prevent SQL injection via f-string."""
    if not _SAFE_COLUMN_RE.match(name):
        raise ValueError(f"Invalid column name: {name}")

from ..models.paper import Paper
from ..models.analysis import AnalysisResult
from ..models.code_analysis import CodeAnalysis
from ..models.research import ResearchRun, ResearchGraph
from ..models.tag import Tag, PaperTag
from ..models.note import Note
from ..models.annotation import Annotation

# ============================================================
# Engine 单例 — 所有路由统一从此处获取引擎
# ============================================================
_shared_engine = None


def _set_sqlite_pragma(dbapi_conn, connection_record):
    """Set WAL journal mode and busy_timeout on every new connection."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def get_engine():
    """获取共享的数据库引擎单例。所有 router / service 统一使用此函数。"""
    global _shared_engine
    if _shared_engine is None:
        from ..config import settings
        _shared_engine = create_engine(
            settings.resolved_database_url,
            connect_args={"check_same_thread": False},
        )
        event.listen(_shared_engine, "connect", _set_sqlite_pragma)
    return _shared_engine


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
            existing_cols = set()

        # analysis_results 表迁移
        analysis_migrations = [
            ("outline", "JSON DEFAULT '[]'"),
            ("authors", "TEXT DEFAULT ''"),
        ]
        for col_name, col_def in analysis_migrations:
            _validate_column_name(col_name)
            if col_name not in existing_cols:
                try:
                    conn.exec_driver_sql(f"ALTER TABLE analysis_results ADD COLUMN {col_name} {col_def}")
                    conn.commit()
                    logger.info(f"Migration: added column {col_name} to analysis_results")
                except Exception as e:
                    logger.warning(f"Migration failed for {col_name}: {e}")

        # papers 表迁移：代码仓库相关字段
        try:
            result = conn.exec_driver_sql("PRAGMA table_info(papers)")
            existing_paper_cols = {row[1] for row in result.fetchall()}
        except Exception:
            existing_paper_cols = set()

        paper_migrations = [
            ("code_url", "TEXT"),
            ("code_status", "TEXT DEFAULT 'none'"),
            ("code_path", "TEXT"),
            ("code_summary", "TEXT"),
            ("source", "TEXT DEFAULT 'uploaded'"),
            ("level", "INTEGER DEFAULT 1"),
            ("parent_paper_id", "INTEGER"),
            ("external_id", "TEXT"),
            ("authors", "TEXT"),
            ("full_text", "TEXT"),
            ("index_status", "TEXT DEFAULT 'none'"),
            ("category", "TEXT"),
            ("graph_digest", "JSON"),
        ]
        for col_name, col_def in paper_migrations:
            _validate_column_name(col_name)
            if col_name not in existing_paper_cols:
                try:
                    conn.exec_driver_sql(f"ALTER TABLE papers ADD COLUMN {col_name} {col_def}")
                    conn.commit()
                    logger.info(f"Migration: added column {col_name} to papers")
                except Exception as e:
                    logger.warning(f"Migration failed for {col_name}: {e}")

        # code_analyses 表迁移
        try:
            result = conn.exec_driver_sql("PRAGMA table_info(code_analyses)")
            existing_code_cols = {row[1] for row in result.fetchall()}
        except Exception:
            existing_code_cols = set()

        code_migrations = [
            ("node_metrics", "JSON DEFAULT '[]'"),
        ]
        for col_name, col_def in code_migrations:
            _validate_column_name(col_name)
            if col_name not in existing_code_cols:
                try:
                    conn.exec_driver_sql(f"ALTER TABLE code_analyses ADD COLUMN {col_name} {col_def}")
                    conn.commit()
                    logger.info(f"Migration: added column {col_name} to code_analyses")
                except Exception as e:
                    logger.warning(f"Migration failed for {col_name}: {e}")

        # 数据回填：早期 expand 阶段创建的 paper 没有正确写 level
        # source='expanded' 的论文按定义就是 L2+，level=1 是历史脏数据
        try:
            cur = conn.exec_driver_sql(
                "UPDATE papers SET level = 2 WHERE source = 'expanded' AND (level IS NULL OR level = 1)"
            )
            conn.commit()
            if cur.rowcount:
                logger.info(f"Migration: backfilled level=2 for {cur.rowcount} expanded papers")
        except Exception as e:
            logger.warning(f"Migration backfill expanded.level failed: {e}")

        # annotations 表迁移：group_id 字段
        try:
            result = conn.exec_driver_sql("PRAGMA table_info(annotations)")
            existing_ann_cols = {row[1] for row in result.fetchall()}
        except Exception:
            existing_ann_cols = set()

        ann_migrations = [
            ("group_id", "TEXT"),
        ]
        for col_name, col_def in ann_migrations:
            _validate_column_name(col_name)
            if col_name not in existing_ann_cols:
                try:
                    conn.exec_driver_sql(f"ALTER TABLE annotations ADD COLUMN {col_name} {col_def}")
                    conn.commit()
                    logger.info(f"Migration: added column {col_name} to annotations")
                except Exception as e:
                    logger.warning(f"Migration failed for {col_name}: {e}")


def init_db(database_url: str) -> None:
    """
    初始化数据库。

    创建所有表（如果不存在）。
    在应用启动时调用一次。

    Args:
        database_url: 数据库连接字符串（应为绝对路径）
    """
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    # 简易迁移：为已有表添加新列
    _migrate_schema(engine)


def reconcile_interrupted_runs(engine=None) -> int:
    """启动自愈：把上次进程被杀时残留为 running/pending 的研究任务标记为 failed。

    进程刚启动，内存里不可能有真正在跑的 pipeline 任务（后台任务随进程一起死了）；
    任何仍是 running 或 pending 的 ResearchRun 都是上次异常退出留下的孤儿。
    不重置的话：①前端 getLatestRun 会一直把 UI 锁成「运行中」且无进度；
    ②create_run 会因「已有运行中任务」永远返回 409，连新任务都发不起。

    Returns:
        int: 被重置的孤儿 run 数量
    """
    if engine is None:
        engine = get_engine()
    now = datetime.now(timezone.utc)
    orphan_statuses = ("running", "pending")
    # 用 Core 批量 UPDATE（而非逐个 ORM flush）：单条 SQL，且不触发 FK 依赖排序，
    # 即使在「未 import 全部模型」的孤立调用下也能安全执行。
    from sqlalchemy import update as _sa_update
    with Session(engine) as session:
        ids = session.exec(
            select(ResearchRun.id).where(ResearchRun.status.in_(orphan_statuses))
        ).all()
        if ids:
            session.exec(
                _sa_update(ResearchRun)
                .where(ResearchRun.status.in_(orphan_statuses))
                .values(
                    status="failed",
                    error_message="后端重启导致任务中断（启动时自动标记为失败）",
                    updated_at=now,
                )
            )
            session.commit()
        return len(ids)


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


def update_paper_status(session: Session, paper_id: int, status: str, error_message: Optional[str] = None) -> None:
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

    method_data = dict(result.get("method", {}))
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


def get_paper(session: Session, paper_id: int) -> Paper:
    """
    获取单篇论文信息。

    Args:
        session: 数据库会话
        paper_id: 论文 ID

    Returns:
        Paper: 论文模型实例

    Raises:
        ValueError: 论文不存在
    """
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise ValueError(f"Paper {paper_id} not found")
    return paper


def get_analysis(session: Session, paper_id: int) -> AnalysisResult:
    """
    获取论文的解析结果。

    Args:
        session: 数据库会话
        paper_id: 论文 ID

    Returns:
        AnalysisResult: 解析结果模型实例

    Raises:
        ValueError: 解析结果不存在
    """
    statement = select(AnalysisResult).where(AnalysisResult.paper_id == paper_id)
    analysis = session.exec(statement).first()
    if analysis is None:
        raise ValueError(f"Analysis result for paper {paper_id} not found")
    return analysis


def update_paper_code_url(session: Session, paper_id: int, code_url: str) -> None:
    """更新论文的代码仓库 URL"""
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise ValueError(f"Paper {paper_id} not found")
    paper.code_url = code_url or None
    paper.updated_at = datetime.now(timezone.utc)
    session.add(paper)
    session.commit()


def update_related_papers(session: Session, paper_id: int, related_papers: list[dict]) -> None:
    """仅更新 related_papers 字段"""
    from sqlmodel import select
    statement = select(AnalysisResult).where(AnalysisResult.paper_id == paper_id)
    analysis = session.exec(statement).first()
    if analysis is None:
        raise ValueError(f"Analysis result for paper {paper_id} not found")
    analysis.related_papers = related_papers
    analysis.updated_at = datetime.now(timezone.utc)
    session.add(analysis)
    session.commit()


def list_papers(
    session: Session,
    search: str = None,
    category: str = None,
    year: int = None,
    date_added: str = None,
    page: int = 1,
    per_page: int = 20,
    source: str = "uploaded",
) -> tuple[list[dict], int]:
    """
    列出论文，支持筛选和分页。

    Args:
        category:   按 arXiv 学科方向（如 cs.LG）筛选
        year:       按论文发表年份筛选
        date_added: 按加入论文库的日期筛选（"YYYY-MM-DD"）
        source: "uploaded"（默认，仅用户上传）| "expanded"（深度研究扩展）| "all"（全部）
    """
    # 显式选择列，排除 full_text（~300K/行）以减少内存和传输开销
    statement = select(
        Paper.id, Paper.filename, Paper.title, Paper.authors, Paper.venue,
        Paper.year, Paper.category, Paper.status, Paper.index_status,
        Paper.error_message, Paper.code_url, Paper.code_status, Paper.source,
        Paper.level, Paper.created_at, Paper.updated_at, Paper.graph_digest,
    )

    # source 过滤
    if source == "uploaded":
        statement = statement.where(Paper.source == "uploaded")
    elif source == "expanded":
        statement = statement.where(Paper.source == "expanded")
    # source == "all" 不过滤

    if search:
        # 大小写不敏感：统一 lower 后子串匹配
        statement = statement.where(func.lower(col(Paper.title)).contains(search.lower()))
    if category:
        statement = statement.where(Paper.category == category)
    if year is not None:
        statement = statement.where(Paper.year == year)
    if date_added:
        statement = statement.where(func.date(Paper.created_at) == date_added)

    count_statement = select(func.count()).select_from(statement.subquery())
    total = session.exec(count_statement).one()

    statement = statement.order_by(Paper.created_at.desc())
    statement = statement.offset((page - 1) * per_page).limit(per_page)

    rows = session.exec(statement).all()
    result = [
        {
            "id": row.id,
            "filename": row.filename,
            "title": row.title,
            "authors": row.authors,
            "venue": row.venue,
            "year": row.year,
            "category": row.category,
            "status": row.status,
            "index_status": row.index_status,
            "error_message": row.error_message,
            "code_url": row.code_url,
            "code_status": row.code_status,
            "source": row.source,
            "level": row.level,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "graph_digest": row.graph_digest,
        }
        for row in rows
    ]

    return result, total


def delete_paper(session: Session, paper_id: int) -> tuple[str, str | None]:
    """
    删除论文及其解析结果。

    Args:
        session: 数据库会话
        paper_id: 论文 ID

    Returns:
        tuple[str, str]: (pdf_path, code_path) 被删除论文的文件路径（供调用方删除文件）

    Raises:
        ValueError: 论文不存在
    """
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise ValueError(f"Paper {paper_id} not found")

    pdf_path = paper.pdf_path
    code_path = paper.code_path

    # AnalysisResult 通过 CASCADE 会自动删除，但显式删除更安全
    statement = select(AnalysisResult).where(AnalysisResult.paper_id == paper_id)
    analysis = session.exec(statement).first()
    if analysis:
        session.delete(analysis)

    session.delete(paper)
    session.commit()

    return pdf_path, code_path
