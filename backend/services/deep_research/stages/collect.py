"""
Stage 1 — collect

收集工作区中已完成解析的论文。
"""

from sqlmodel import Session, create_engine, select
from ....config import settings
from ....models.paper import Paper
from ....models.workspace import WorkspacePaper

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})
    return _engine


async def stage_collect(ws_id: int, params: dict) -> list[Paper]:
    """收集工作区中可参与研究的论文。

    深度研究只用 bge 做 chunk + embed，**不依赖 LLM 分析的 status**。
    所以只过滤掉真正不能用的：
    - extracting / analyzing：正在跑别的 pipeline，避免文件占用冲突
    - failed：跑过但出错，再走可能还是错

    可用的：
    - pending → 仅上传过、没解析过 LLM —— 直接用 PDF 全文切片
    - done    → LLM 解析完成，chunk 优先用 outline 章节
    - indexed → 联网扩展下来的，跟 pending 一样只走 PDF 全文

    paper_ids 非空时仅取这些；否则取工作区全部 level=1 论文。
    """
    pids = params.get("paper_ids")
    with Session(_get_engine(), expire_on_commit=False) as s:
        stmt = (select(Paper).join(WorkspacePaper, WorkspacePaper.paper_id == Paper.id)
                .where(WorkspacePaper.workspace_id == ws_id)
                .where(Paper.status.in_(["done", "indexed", "pending"])))
        if pids:
            stmt = stmt.where(Paper.id.in_(pids))
        else:
            stmt = stmt.where(Paper.level == 1)
        return s.exec(stmt).all()
