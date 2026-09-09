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


def _backfill_paper_identity(conn, logger) -> None:
    """回填论文身份字段；canonical 冲突只记日志，不 merge/删除。"""
    import json
    from pathlib import Path
    from .paper_identity import (
        build_canonical_id,
        build_source_ids,
        is_strong_canonical_id,
        normalize_arxiv_id,
        normalize_doi,
        normalize_openalex_id,
        normalize_s2_id,
        normalize_title,
        parse_legacy_external_id,
    )

    rows = conn.exec_driver_sql(
        """
        SELECT id, external_id, title, pdf_path,
               doi, arxiv_id, openalex_id, s2_id,
               canonical_id, title_normalized, download_status, source_ids
        FROM papers
        """
    ).fetchall()

    canonical_owners: dict[str, int] = {}
    for row in rows:
        (_pid, _ext, _title, _pdf, _doi, _arxiv, _oa, _s2, cid, *_rest) = row
        if cid and is_strong_canonical_id(cid):
            if cid in canonical_owners:
                logger.warning(
                    "Migration: pre-existing canonical conflict %s papers %s vs %s",
                    cid, canonical_owners[cid], _pid,
                )
            else:
                canonical_owners[cid] = _pid

    conflict_queue: list[dict] = []
    updated = 0

    for row in rows:
        (paper_id, external_id, title, pdf_path,
         doi, arxiv_id, openalex_id, s2_id,
         canonical_id, title_normalized, download_status, source_ids_raw) = row

        parsed = parse_legacy_external_id(external_id)
        new_doi = doi or parsed.get("doi")
        new_arxiv = arxiv_id or parsed.get("arxiv_id")
        new_oa = openalex_id or parsed.get("openalex_id")
        new_s2 = s2_id or parsed.get("s2_id")

        new_doi = normalize_doi(new_doi) if new_doi else None
        new_arxiv = normalize_arxiv_id(new_arxiv) if new_arxiv else None
        new_oa = normalize_openalex_id(new_oa) if new_oa else None
        new_s2 = normalize_s2_id(new_s2) if new_s2 else None
        new_title_norm = normalize_title(title)

        new_canonical = canonical_id
        if not new_canonical:
            new_canonical = build_canonical_id(
                doi=new_doi,
                arxiv_id=new_arxiv,
                openalex_id=new_oa,
                s2_id=new_s2,
                title=title,
            )

        new_download = download_status or "none"
        if new_download == "none" and pdf_path and Path(pdf_path).is_file():
            new_download = "available"

        new_source_ids = source_ids_raw
        if not new_source_ids and (new_doi or new_arxiv or new_oa or new_s2 or external_id):
            built = build_source_ids(
                doi=new_doi,
                arxiv_id=new_arxiv,
                openalex_id=new_oa,
                s2_id=new_s2,
                legacy_external_id=external_id,
            )
            new_source_ids = json.dumps(built) if built else None

        # 冲突检测：强 ID canonical 已被其他 paper 占用
        if (
            new_canonical
            and is_strong_canonical_id(new_canonical)
            and new_canonical in canonical_owners
            and canonical_owners[new_canonical] != paper_id
        ):
            conflict_queue.append({
                "paper_id": paper_id,
                "canonical_id": new_canonical,
                "owner_id": canonical_owners[new_canonical],
            })
            logger.warning(
                "Migration: canonical conflict paper %s -> %s owned by %s; skip canonical write",
                paper_id, new_canonical, canonical_owners[new_canonical],
            )
            new_canonical = canonical_id  # 保留原值（通常为 NULL）

        sets: list[str] = []
        params: list = []

        def _add(col: str, val):
            sets.append(f"{col} = ?")
            params.append(val)

        if not doi and new_doi:
            _add("doi", new_doi)
        if not arxiv_id and new_arxiv:
            _add("arxiv_id", new_arxiv)
        if not openalex_id and new_oa:
            _add("openalex_id", new_oa)
        if not s2_id and new_s2:
            _add("s2_id", new_s2)
        if not title_normalized and new_title_norm:
            _add("title_normalized", new_title_norm)
        if not canonical_id and new_canonical:
            _add("canonical_id", new_canonical)
            if is_strong_canonical_id(new_canonical):
                canonical_owners[new_canonical] = paper_id
        if (not download_status or download_status == "none") and new_download == "available":
            _add("download_status", new_download)
        if not source_ids_raw and new_source_ids:
            _add("source_ids", new_source_ids)

        if not sets:
            continue

        params.append(paper_id)
        conn.exec_driver_sql(
            f"UPDATE papers SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        updated += 1

    if updated:
        conn.commit()
        logger.info("Migration: backfilled paper identity for %s row(s)", updated)
    if conflict_queue:
        report_path = None
        database_path = getattr(getattr(conn, "engine", None), "url", None)
        database_path = getattr(database_path, "database", None)
        if database_path and database_path != ":memory:":
            report_path = Path(database_path).parent / "migration_identity_conflicts.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "conflicts": conflict_queue,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        logger.warning(
            "Migration: %s canonical conflict(s) queued for manual review%s: %s",
            len(conflict_queue),
            f" at {report_path}" if report_path else "",
            conflict_queue[:10],
        )


def _validate_column_name(name: str) -> None:
    """Validate column name to prevent SQL injection via f-string."""
    if not _SAFE_COLUMN_RE.match(name):
        raise ValueError(f"Invalid column name: {name}")

from ..models.paper import Paper
from ..models.analysis import AnalysisResult
from ..models.code_analysis import CodeAnalysis
from ..models.research import ResearchRun, ResearchGraph, PaperReference
from ..models.exploration import ExplorationRun, ExplorationMessage
from ..models.research_core import (
    ResearchAsset,
    ResearchBudgetEvent,
    ResearchClaim,
    ResearchEvent,
    ResearchEvidence,
    ResearchIdentityDecision,
    ResearchRelation,
    ResearchReportRevision,
    ResearchTask,
    ResearchTaskAttempt,
    WORKFLOW_ACADEMIC_SURVEY_V2,
)
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

        exploration_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(exploration_runs)")}
        if exploration_cols and "options" not in exploration_cols:
            conn.exec_driver_sql("ALTER TABLE exploration_runs ADD COLUMN options JSON DEFAULT '{}'")
            conn.commit()

        # exploration_runs 表迁移：academic_survey_v2 工作流列（S0，只增不删）
        # 旧数据自动落默认值 legacy_v1 / 0 / NULL；回滚 v1 代码不读这些列，数据仍可读
        exploration_v2_migrations = [
            ("workflow_version", "TEXT NOT NULL DEFAULT 'legacy_v1'"),
            ("state_version", "INTEGER NOT NULL DEFAULT 0"),
            ("protocol_asset_id", "TEXT"),
        ]
        for col_name, col_def in exploration_v2_migrations:
            _validate_column_name(col_name)
            if col_name not in exploration_cols:
                try:
                    conn.exec_driver_sql(f"ALTER TABLE exploration_runs ADD COLUMN {col_name} {col_def}")
                    conn.commit()
                    logger.info(f"Migration: added column {col_name} to exploration_runs")
                except Exception as e:
                    logger.warning(f"Migration failed for exploration_runs.{col_name}: {e}")

        # academic_survey_v2 新表（S0 冻结 schema；正常路径由 create_all 建表，
        # 此处兜底保证旧库升级/部分导入路径也能拿到完整表集，checkfirst 幂等）
        from sqlmodel import SQLModel as _SQLModel
        _SQLModel.metadata.create_all(engine, tables=[
            ResearchTask.__table__,
            ResearchTaskAttempt.__table__,
            ResearchAsset.__table__,
            ResearchEvidence.__table__,
            ResearchRelation.__table__,
            ResearchClaim.__table__,
            ResearchReportRevision.__table__,
            ResearchBudgetEvent.__table__,
            ResearchEvent.__table__,
            ResearchIdentityDecision.__table__,
        ])

        # research_tasks 幂等键唯一索引（S0 复审修正 F2）：旧全局唯一索引会阻断
        # 同协议的另一个 run 建立自己的账本；改为 (run_id, idempotency_key) 唯一。
        if conn.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_tasks'"
        ).first():
            try:
                conn.exec_driver_sql("DROP INDEX IF EXISTS uq_research_tasks_idempotency")
                conn.exec_driver_sql(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_research_tasks_run_idempotency
                    ON research_tasks(run_id, idempotency_key)
                    WHERE idempotency_key IS NOT NULL
                    """
                )
                conn.commit()
            except Exception as e:
                logger.warning(f"Migration failed for research_tasks idempotency index: {e}")


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
            ("bibliography", "JSON"),
            ("graph_digest", "JSON"),
            # 论文身份字段（P1）
            ("canonical_id", "TEXT"),
            ("doi", "TEXT"),
            ("arxiv_id", "TEXT"),
            ("openalex_id", "TEXT"),
            ("s2_id", "TEXT"),
            ("title_normalized", "TEXT"),
            ("download_status", "TEXT DEFAULT 'none'"),
            ("match_confidence", "REAL"),
            ("source_ids", "JSON"),
            # 领域认知铺垫：召回时的摘要与被引数（synthesize 阶段的 LLM 输入）
            ("abstract", "TEXT"),
            ("citation_count", "INTEGER"),
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

        # papers 身份索引：强 ID canonical 唯一；titlefp 不做唯一约束
        try:
            conn.exec_driver_sql(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_papers_canonical_strong
                ON papers(canonical_id)
                WHERE canonical_id IS NOT NULL
                  AND canonical_id NOT LIKE 'titlefp:%'
                """
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_papers_doi ON papers(doi) WHERE doi IS NOT NULL"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_papers_arxiv_id ON papers(arxiv_id) WHERE arxiv_id IS NOT NULL"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_papers_openalex_id ON papers(openalex_id) WHERE openalex_id IS NOT NULL"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_papers_s2_id ON papers(s2_id) WHERE s2_id IS NOT NULL"
            )
            conn.commit()
            logger.info("Migration: ensured paper identity indexes")
        except Exception as e:
            logger.warning(f"Migration failed for paper identity indexes: {e}")

        # papers 身份回填：解析 legacy external_id，构建 canonical_id
        try:
            _backfill_paper_identity(conn, logger)
        except Exception as e:
            logger.warning(f"Migration backfill paper identity failed: {e}")

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

        # 活跃研究任务唯一索引：若已有重复 pending/running，先收敛再建索引
        try:
            dup_rows = conn.exec_driver_sql(
                """
                SELECT workspace_id, COUNT(*) AS cnt
                FROM research_runs
                WHERE status IN ('pending', 'running')
                GROUP BY workspace_id
                HAVING cnt > 1
                """
            ).fetchall()
            for workspace_id, _cnt in dup_rows:
                keep_id = conn.exec_driver_sql(
                    """
                    SELECT id FROM research_runs
                    WHERE workspace_id = ? AND status IN ('pending', 'running')
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (workspace_id,),
                ).fetchone()
                if not keep_id:
                    continue
                conn.exec_driver_sql(
                    """
                    UPDATE research_runs
                    SET status = 'failed',
                        error_message = COALESCE(error_message, 'superseded by newer active run')
                    WHERE workspace_id = ?
                      AND status IN ('pending', 'running')
                      AND id != ?
                    """,
                    (workspace_id, keep_id[0]),
                )
            if dup_rows:
                conn.commit()
                logger.info("Migration: deduped %s workspace(s) with duplicate active research runs", len(dup_rows))

            conn.exec_driver_sql(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_research_runs_workspace_active
                ON research_runs(workspace_id)
                WHERE status IN ('pending', 'running')
                """
            )
            conn.commit()
            logger.info("Migration: ensured active research_runs unique index")
        except Exception as e:
            logger.warning(f"Migration failed for active research_runs unique index: {e}")

        # create_all does not add new indexes to an already existing table.
        if conn.exec_driver_sql("SELECT 1 FROM sqlite_master WHERE type='table' AND name='exploration_messages'").first():
            conn.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_exploration_active_message "
                "ON exploration_messages(run_id) WHERE status IN ('pending', 'running')"
            )
            conn.commit()

        # paper_references 表索引（create_all 已建表，此处确保索引存在）
        try:
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_paper_refs_citing ON paper_references(citing_paper_id)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_paper_refs_cited ON paper_references(cited_paper_id)"
            )
            conn.exec_driver_sql(
                """
                CREATE INDEX IF NOT EXISTS ix_paper_refs_cited_doi
                ON paper_references(cited_doi) WHERE cited_doi IS NOT NULL
                """
            )
            conn.exec_driver_sql(
                """
                CREATE INDEX IF NOT EXISTS ix_paper_refs_cited_arxiv
                ON paper_references(cited_arxiv_id) WHERE cited_arxiv_id IS NOT NULL
                """
            )
            conn.commit()
            logger.info("Migration: ensured paper_references indexes")
        except Exception as e:
            logger.warning(f"Migration failed for paper_references indexes: {e}")

        # research_graphs 表迁移：领域认知 synthesize 结果（简报/流派/语义标注）
        try:
            result = conn.exec_driver_sql("PRAGMA table_info(research_graphs)")
            existing_rg_cols = {row[1] for row in result.fetchall()}
        except Exception:
            existing_rg_cols = set()
        if "synthesis" not in existing_rg_cols:
            try:
                conn.exec_driver_sql("ALTER TABLE research_graphs ADD COLUMN synthesis JSON")
                conn.commit()
                logger.info("Migration: added column synthesis to research_graphs")
            except Exception as e:
                logger.warning(f"Migration failed for research_graphs.synthesis: {e}")


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
        count = len(ids)
        # academic_survey_v2 豁免（计划 §7.1）：v2 运行由 worker 租约与任务账本决定恢复，
        # 不能被 API 进程启动误置 failed；v1 行为保持不变。
        for model in (ExplorationRun, ExplorationMessage):
            statement = select(model).where(model.status.in_(orphan_statuses))
            if model is ExplorationRun:
                statement = statement.where(ExplorationRun.workflow_version != WORKFLOW_ACADEMIC_SURVEY_V2)
            rows = session.exec(statement).all()
            for row in rows:
                row.status = "failed"
                row.error_message = "后端重启导致任务中断，请重试"
                row.updated_at = now
                session.add(row)
            count += len(rows)
        session.commit()
        return count


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


def update_paper_metadata(
    session: Session,
    paper_id: int,
    title: str,
    venue: str,
    year: int,
    authors: Optional[str] = None,
) -> None:
    """
    更新论文元信息（解析完成后）。

    Args:
        session: 数据库会话
        paper_id: 论文 ID
        title: 论文标题
        venue: 会议/期刊
        year: 发表年份
        authors: 作者列表
    """
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise ValueError(f"Paper {paper_id} not found")
    paper.title = title
    if authors is not None:
        paper.authors = authors
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
        existing.authors = result.get("authors", "")
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
            authors=result.get("authors", ""),
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


#: 论文库排序方式 → 说明（前端下拉的唯一真源）
PAPER_SORTS: dict[str, str] = {
    "recent": "最近添加",
    "oldest": "最早添加",
    "year_desc": "发表年份新→旧",
    "year_asc": "发表年份旧→新",
    "title": "标题 A→Z",
}


def _paper_order_by(sort: str) -> list:
    """把排序名翻译成 order_by 子句。

    年份可能为空（未索引的论文），两个方向都先把空值排到末尾 —— 否则「按年份
    排序」的第一页会是一整屏没有年份的论文，等于没排。
    """
    year_is_null = col(Paper.year).is_(None)
    title_key = func.lower(func.coalesce(col(Paper.title), col(Paper.filename)))

    if sort == "oldest":
        return [Paper.created_at.asc()]
    if sort == "year_desc":
        return [year_is_null.asc(), Paper.year.desc(), Paper.created_at.desc()]
    if sort == "year_asc":
        return [year_is_null.asc(), Paper.year.asc(), Paper.created_at.desc()]
    if sort == "title":
        return [title_key.asc()]
    return [Paper.created_at.desc()]


def list_papers(
    session: Session,
    search: str = None,
    category: str = None,
    year: int = None,
    date_added: str = None,
    page: int = 1,
    per_page: int = 20,
    source: str = "uploaded",
    sort: str = "recent",
    subject: str = None,
    venue: str = None,
    publication_type: str = None,
    provider: str = None,
) -> tuple[list[dict], int]:
    """
    列出论文，支持筛选、排序和分页。

    Args:
        category:   按 arXiv 学科方向（如 cs.LG）筛选
        year:       按论文发表年份筛选
        date_added: 按加入论文库的日期筛选（"YYYY-MM-DD"）
        source: "uploaded"（默认，仅用户上传）| "expanded"（深度研究扩展）| "all"（全部）
        sort:   PAPER_SORTS 中的排序名，未知值回落到 "recent"
    """
    # 显式选择列，排除 full_text（~300K/行）以减少内存和传输开销
    statement = select(
        Paper.id, Paper.filename, Paper.title, Paper.authors, Paper.venue,
        Paper.year, Paper.category, Paper.status, Paper.index_status,
        Paper.error_message, Paper.code_url, Paper.code_path, Paper.code_status, Paper.source,
        Paper.level, Paper.created_at, Paper.updated_at, Paper.graph_digest,
        Paper.bibliography, Paper.source_ids, Paper.doi, Paper.arxiv_id, Paper.openalex_id, Paper.s2_id,
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

    from .paper_metadata import filter_query, view
    statement = filter_query(statement, session, subject=subject, venue=venue, publication_type=publication_type, provider=provider)

    count_statement = select(func.count()).select_from(statement.subquery())
    total = session.exec(count_statement).one()

    statement = statement.order_by(*_paper_order_by(sort))
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
            "code_path": row.code_path,
            "code_status": row.code_status,
            "source": row.source,
            "level": row.level,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "graph_digest": row.graph_digest,
            **view(dict(row._mapping)),
        }
        for row in rows
    ]

    return result, total


def list_paper_facets(session: Session, source: str = "uploaded") -> dict:
    """
    列出整个论文库里可用的筛选值（方向 / 年份 / 加入日期）。

    筛选下拉此前是从「当前这一页的 20 条」里推出来的：翻页时选项会跳变，
    跨页存在的方向和年份根本选不到。筛选器必须以全库为定义域，不是以当前页。
    """
    from .paper_metadata import view
    statement = select(Paper.category, Paper.year, Paper.created_at, Paper.venue, Paper.bibliography,
                       Paper.source_ids, Paper.doi, Paper.arxiv_id, Paper.openalex_id, Paper.s2_id)
    if source in ("uploaded", "expanded"):
        statement = statement.where(Paper.source == source)

    categories: set[str] = set()
    years: set[int] = set()
    dates: set[str] = set()
    subjects, venues, kinds, providers = set(), set(), set(), set()

    for row in session.exec(statement).all():
        meta = view(dict(row._mapping))
        subjects.update(meta["subjects"])
        providers.update(meta["providers"])
        kinds.add(meta["publication_type"])
        if meta["venue"]:
            venues.add(meta["venue"])
        if row.category:
            categories.add(row.category)
        # 过滤脏年份（GROBID 偶尔会解析出 20 / 2 这类残值）
        if row.year and row.year >= 1900:
            years.add(row.year)
        if row.created_at:
            dates.add(row.created_at.isoformat()[:10] if hasattr(row.created_at, "isoformat") else str(row.created_at)[:10])

    return {
        "categories": sorted(categories),
        "years": sorted(years, reverse=True),
        "dates": sorted(dates, reverse=True),
        "subjects": sorted(subjects), "venues": sorted(venues),
        "publication_types": sorted(kinds), "providers": sorted(providers),
    }


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
