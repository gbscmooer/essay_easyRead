"""
Bibliographic Coupling 相似度

策略：
- 如果两篇论文引用了相同的参考文献，它们很可能研究相似的问题
- Jaccard(paper_a.cites, paper_b.cites) = |交集| / |并集|
- 优点：不需要 embedding，纯引用结构，计算快
- 局限：只对有引用数据的论文有效；新论文无引用则 score=0
"""

import logging
from sqlmodel import Session, create_engine, select
from ....models.research import ResearchGraph
from ....config import settings

logger = logging.getLogger(__name__)

_engine = None

def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})
    return _engine


class BibCouplingProvider:
    name = "bibcoupling"

    def __init__(self, **_):
        pass

    def _load_graph_edges(self, ws_id: int) -> list[dict]:
        """从数据库加载工作区图谱的 edges"""
        try:
            engine = _get_engine()
            with Session(engine) as s:
                g = s.exec(select(ResearchGraph).where(ResearchGraph.workspace_id == ws_id)).first()
                if g and g.edges:
                    return g.edges
        except Exception as ex:
            logger.warning(f"bibcoupling: failed to load graph for ws={ws_id}: {ex}")
        return []

    async def score_pairs(self, papers: list, *, ws_id: int) -> list[tuple[int, int, float]]:
        # 1. 加载图谱，获取 cites 边
        edges = self._load_graph_edges(ws_id)
        if not edges:
            logger.warning(f"bibcoupling: no graph edges for ws={ws_id}")
            return []

        paper_ids = {p.id for p in papers}

        # 2. 构建引用映射：paper_id -> set of cited paper_ids
        cites_map: dict[int, set[int]] = {pid: set() for pid in paper_ids}
        for e in edges:
            if e.get("kind") != "cites":
                continue
            src, tgt = e.get("source"), e.get("target")
            # source/target 格式为 "paper:{id}"
            try:
                src_id = int(src.split(":")[1]) if isinstance(src, str) else src
                tgt_id = int(tgt.split(":")[1]) if isinstance(tgt, str) else tgt
            except (IndexError, ValueError, AttributeError):
                continue
            if src_id in paper_ids and tgt_id in paper_ids:
                cites_map[src_id].add(tgt_id)

        # 3. 计算 Jaccard 相似度
        ids = sorted(paper_ids)
        out: list[tuple[int, int, float]] = []
        for i in range(len(ids)):
            a_refs = cites_map.get(ids[i], set())
            if not a_refs:
                continue
            for j in range(i + 1, len(ids)):
                b_refs = cites_map.get(ids[j], set())
                if not b_refs:
                    continue
                intersection = len(a_refs & b_refs)
                if intersection == 0:
                    continue
                union = len(a_refs | b_refs)
                score = intersection / union if union > 0 else 0.0
                out.append((ids[i], ids[j], round(score, 4)))
        return out
