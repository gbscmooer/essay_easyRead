"""
Bibliographic Coupling 文献耦合

若两篇论文引用相同参考文献，它们可能研究相似问题。
权重采用 Salton 系数，共享引用越多权重单调不减（分母固定时）。

外部未解析引用用 doi/arxiv/title 键匹配，不要求 cited 在工作区内。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Iterable

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


def ref_target_key(ref) -> str | int | None:
    """引用目标的规范键。"""
    if getattr(ref, "cited_paper_id", None):
        return int(ref.cited_paper_id)
    if getattr(ref, "cited_doi", None):
        return f"doi:{ref.cited_doi}"
    if getattr(ref, "cited_arxiv_id", None):
        return f"arxiv:{ref.cited_arxiv_id}"
    title = (getattr(ref, "cited_title", None) or "").strip().lower()
    return f"title:{title}" if title else None


def build_cites_map(
    references: Iterable,
    paper_ids: set[int],
) -> dict[int, set[str | int]]:
    """citing_paper_id → 其引用的目标键集合。"""
    cites_map: dict[int, set[str | int]] = {pid: set() for pid in paper_ids}
    for ref in references:
        citing_id = int(ref.citing_paper_id)
        if citing_id not in paper_ids:
            continue
        key = ref_target_key(ref)
        if key is not None:
            cites_map.setdefault(citing_id, set()).add(key)
    return cites_map


def compute_bibliographic_coupling_pairs(
    cites_map: dict[int, set[str | int]],
    *,
    min_shared: int = 1,
) -> list[tuple[int, int, float, int]]:
    """
    计算文献耦合对。

    Returns:
        [(paper_a, paper_b, salton_weight, shared_ref_count)]，a < b。
    """
    # ref_key -> papers citing it
    ref_to_papers: dict[str | int, set[int]] = defaultdict(set)
    for pid, refs in cites_map.items():
        for rk in refs:
            ref_to_papers[rk].add(pid)

    pair_shared: dict[tuple[int, int], int] = defaultdict(int)
    for _rk, papers in ref_to_papers.items():
        plist = sorted(papers)
        for i in range(len(plist)):
            for j in range(i + 1, len(plist)):
                a, b = plist[i], plist[j]
                pair_shared[(a, b)] += 1

    out: list[tuple[int, int, float, int]] = []
    for (a, b), shared in pair_shared.items():
        if shared < min_shared:
            continue
        refs_a = len(cites_map.get(a, set()))
        refs_b = len(cites_map.get(b, set()))
        if refs_a == 0 or refs_b == 0:
            continue
        salton = shared / ((refs_a * refs_b) ** 0.5)
        out.append((a, b, round(float(salton), 6), shared))
    return out


class BibCouplingProvider:
    """SimilarityProvider：从已持久化图谱 cites 边计算 Jaccard（兼容旧接口）。"""

    name = "bibcoupling"

    def __init__(self, **_):
        pass

    def _load_graph_edges(self, ws_id: int) -> list[dict]:
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
        edges = self._load_graph_edges(ws_id)
        if not edges:
            logger.warning(f"bibcoupling: no graph edges for ws={ws_id}")
            return []

        paper_ids = {p.id for p in papers}
        cites_map: dict[int, set[str | int]] = {pid: set() for pid in paper_ids}
        for e in edges:
            if e.get("kind") != "cites":
                continue
            try:
                src_id = int(str(e["source"]).split(":")[1])
                tgt_id = int(str(e["target"]).split(":")[1])
            except (IndexError, ValueError, AttributeError):
                continue
            if src_id in paper_ids and tgt_id in paper_ids:
                cites_map[src_id].add(tgt_id)

        pairs = compute_bibliographic_coupling_pairs(cites_map)
        # Provider 协议返回 Jaccard 以兼容旧行为
        out: list[tuple[int, int, float]] = []
        for a, b, salton, shared in pairs:
            refs_a = cites_map.get(a, set())
            refs_b = cites_map.get(b, set())
            union = len(refs_a | refs_b)
            jaccard = shared / union if union > 0 else 0.0
            out.append((a, b, round(jaccard, 4)))
        return out
