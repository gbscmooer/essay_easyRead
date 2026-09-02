"""
Stage 6 — persist

将图谱快照写入 ResearchGraph 表（upsert）。
synthesis（领域认知综合产物）随图谱一起写入；None 表示本次未产出（未配置 Key / 全部失败）。
"""

from datetime import datetime, timezone
from sqlmodel import Session, create_engine, select
from ....config import settings
from ....models.research import ResearchGraph

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})
    return _engine


async def stage_persist(ws_id: int, run_id: int, graph: dict, synthesis: dict | None = None):
    """将图谱持久化到数据库（upsert）"""
    with Session(_get_engine()) as s:
        existing = s.exec(select(ResearchGraph).where(ResearchGraph.workspace_id == ws_id)).first()
        if existing:
            existing.nodes = graph["nodes"]
            existing.edges = graph["edges"]
            existing.stats = graph["stats"]
            existing.synthesis = synthesis
            existing.run_id = run_id
            existing.updated_at = datetime.now(timezone.utc)
            s.add(existing)
        else:
            s.add(ResearchGraph(workspace_id=ws_id, run_id=run_id,
                                nodes=graph["nodes"], edges=graph["edges"],
                                stats=graph["stats"], synthesis=synthesis))
        s.commit()
