"""
paper_relatedness — 生产侧到 graphkit 的唯一桥（v3）

职责只有"取数 + 转换"：把 DB（Paper / AnalysisResult / graph_digest）与 ChromaDB
（chunk 向量质心）里的东西抽成纯净的 `PaperFeatures`，再交给
`graphkit.relatedness.compute_relations` 算分。算法本身一行都不在这里，
沙盒 / 单测跑的与生产是同一套代码。

v3 与旧 similarity/* 的区别：
- 旧版有 6 个可切换的 provider（unified / bge_centroid / facet_weighted / hybrid /
  cross_encoder / bibcoupling），各算各的，配置切换一次行为全变，且只有 unified 的
  分数真正决定拓扑；facet 分解、文献耦合都被丢在展示层。
- v3 只有一套：所有信号（主题向量、目的、方法、术语、数据集、baseline、文献耦合、
  共引）作为通道进入同一个融合分，同一个分数决定连边 / 社区 / 重要性 / 图检索。
"""

from __future__ import annotations

import logging

from sqlmodel import Session, create_engine, select

from ...config import settings
from ...models.analysis import AnalysisResult
from ..ai_config import get_relations_config
from .embedder import embed_texts
from .graphkit import (
    PaperFeatures,
    RelationResult,
    build_cited_by_map,
    build_cites_map,
    compute_bibliographic_coupling_pairs,
    compute_co_citation_pairs,
    compute_relations,
    pair_map,
)
from .vector_store import get_or_create

logger = logging.getLogger(__name__)

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.resolved_database_url, connect_args={"check_same_thread": False}
        )
    return _engine


# 主题代表性强的章节关键词（命中则加权，让质心更偏向"研究主题/目的"）
_TOPIC_SECTIONS = ("abstract", "intro", "conclusion", "overview")


def _chunk_weight(meta: dict) -> float:
    """单个 chunk 在论文质心里的权重。

    全文平均会让到处雷同的实现细节（backbone/优化器/超参）稀释主题信号。按章节/位置
    加权可让质心更贴近研究方向：
    - 摘要/引言/结论：最能代表主题 → 2.0
    - 正文滑窗（section_name=="Body"）：按位置衰减，开头（题录/摘要/引言）更代表主题
      —— 这对**没有章节信息的叶子论文**也有效（它们全是 Body，只能靠位置区分）
    - 引用上下文：讲的是别人的工作 → 0.5 弱化
    - 其他具名章节（method/experiment…）：1.0 正常
    """
    name = (meta.get("section_name") or "").strip().lower()
    idx = int(meta.get("chunk_index") or 0)
    if any(k in name for k in _TOPIC_SECTIONS):
        return 2.0
    if "citation context" in name:
        return 0.5
    if name in ("body", ""):
        return max(0.7, 1.5 - 0.1 * idx)
    return 1.0


def _doc_centroids(ws_id: int, paper_ids: list[int]) -> dict[int, list[float]]:
    """ChromaDB 里每篇论文 chunk embedding 的加权质心。"""
    import numpy as np

    out: dict[int, list[float]] = {}
    try:
        col = get_or_create(ws_id)
    except Exception as ex:
        logger.warning(f"relatedness: 向量库不可用，doc_vector 全空: {ex}")
        return out
    for pid in paper_ids:
        try:
            res = col.get(where={"paper_id": pid}, include=["embeddings", "metadatas"])
            embs = res["embeddings"] if isinstance(res, dict) else None
            arr = np.asarray(embs) if embs is not None else None
            if arr is None or arr.size == 0 or arr.ndim < 2:
                continue
            metas = (res.get("metadatas") if isinstance(res, dict) else None) or []
            unit = arr / (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9)
            if len(metas) == len(unit):
                w = np.array([_chunk_weight(m or {}) for m in metas], dtype=float).reshape(-1, 1)
            else:
                w = np.ones((len(unit), 1))                 # 元数据对不齐 → 退回等权平均
            mean_vec = (unit * w).sum(axis=0) / (float(w.sum()) + 1e-9)
            out[pid] = (mean_vec / (np.linalg.norm(mean_vec) + 1e-9)).tolist()
        except Exception as ex:
            logger.debug(f"relatedness: skip centroid for paper {pid}: {ex}")
    return out


def build_paper_features(papers: list, ws_id: int) -> list[PaperFeatures]:
    """Paper 行 → PaperFeatures（facet 优先取精读结果，回退图谱速读 digest）。"""
    pids = [p.id for p in papers]
    with Session(_get_engine()) as s:
        analyses = {
            a.paper_id: a
            for a in s.exec(select(AnalysisResult).where(AnalysisResult.paper_id.in_(pids))).all()
        }
    centroids = _doc_centroids(ws_id, pids)

    feats: list[PaperFeatures] = []
    for p in papers:
        a = analyses.get(p.id)
        digest = getattr(p, "graph_digest", None) or None
        # 摘要代理：速读 summary 比"全文前 1200 字"（常含页眉/作者/致谢）干净得多
        abstract = (digest or {}).get("summary") or (getattr(p, "full_text", None) or "")[:1200]
        abstract = (abstract or "").strip()

        if a is not None:
            method = a.method or {}
            exp = a.experiments or {}
            feats.append(PaperFeatures(
                id=p.id, title=a.title or p.title or "", year=a.year, is_analyzed=True,
                purpose_text=" ".join(filter(None, [a.problem, a.one_sentence, *(a.key_contributions or [])])),
                method_text=" ".join(filter(None, [method.get("overview", ""), method.get("pipeline", "")])),
                datasets=[d for d in (exp.get("datasets") or []) if str(d).strip()],
                baselines=[b for b in (exp.get("baselines") or []) if str(b).strip()],
                abstract=abstract, doc_vector=centroids.get(p.id),
            ))
        elif digest:
            feats.append(PaperFeatures(
                id=p.id, title=p.title or "", year=getattr(p, "year", None), is_analyzed=True,
                purpose_text=" ".join(filter(None, [
                    digest.get("problem", ""), digest.get("one_sentence", ""),
                    *(digest.get("key_contributions") or []),
                ])),
                method_text=digest.get("method", "") or "",
                datasets=[d for d in (digest.get("datasets") or []) if str(d).strip()],
                baselines=[b for b in (digest.get("baselines") or []) if str(b).strip()],
                abstract=abstract, doc_vector=centroids.get(p.id),
            ))
        else:
            feats.append(PaperFeatures(
                id=p.id, title=p.title or "", year=getattr(p, "year", None),
                is_analyzed=False, abstract=abstract, doc_vector=centroids.get(p.id),
            ))
    return feats


def structural_channels(
    references: list | None, paper_ids: set[int]
) -> tuple[dict[tuple[int, int], tuple[float, int]], dict[tuple[int, int], tuple[float, int]]]:
    """由引用记录算出 (bibcoupling, cocitation) 两个通道的输入。"""
    if not references:
        return {}, {}
    cites_map = build_cites_map(references, paper_ids)
    cited_by_map = build_cited_by_map(references, paper_ids)
    bib = pair_map(compute_bibliographic_coupling_pairs(cites_map))
    cocite = pair_map(compute_co_citation_pairs(cited_by_map, paper_ids))
    return bib, cocite


async def compute_paper_relations(
    papers: list,
    *,
    ws_id: int,
    references: list | None = None,
    weights: dict[str, float] | None = None,
) -> RelationResult:
    """工作区论文两两关联度（全系统唯一入口）。"""
    feats = build_paper_features(papers, ws_id)
    bib, cocite = structural_channels(references, {p.id for p in papers})
    if weights is None:
        weights = (get_relations_config() or {}).get("weights") or None
    return await compute_relations(
        feats, embed_fn=embed_texts, weights=weights,
        bibcoupling=bib, cocitation=cocite,
    )
