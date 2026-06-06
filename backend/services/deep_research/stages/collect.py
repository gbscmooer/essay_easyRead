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
    可用 status：done / pending / indexed。

    返回策略：
    - 用户在 picker 中勾选的 L1 论文（`paper_ids`）
    - **加上**工作区内已存在的所有 L2+ 论文（之前研究下载的）
      这些论文不会再被 expand 重复下载，但要进入图谱和向量检索。
    - 如果 `paper_ids` 为空，回退为全部 L1 + 全部 L2+。
    """
    pids = params.get("paper_ids")
    with Session(_get_engine(), expire_on_commit=False) as s:
        base = (select(Paper).join(WorkspacePaper, WorkspacePaper.paper_id == Paper.id)
                .where(WorkspacePaper.workspace_id == ws_id)
                .where(Paper.status.in_(["done", "indexed", "pending"])))

        if pids:
            # L1 按 picker 勾选过滤；L2+ 全要
            picked_l1 = s.exec(base.where(Paper.id.in_(pids)).where(Paper.level == 1)).all()
            l2_plus = s.exec(base.where(Paper.level >= 2)).all()
            return list(picked_l1) + list(l2_plus)
        else:
            return list(s.exec(base).all())
