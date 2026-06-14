"""
Unified 相似度 Provider —— 生产侧到 graphkit 的桥（新默认）

这是"最强图谱算法"的生产入口。它做且只做"耦合层"的事：从 DB / ChromaDB 把数据
抽成纯净的 `PaperFeatures`，然后**委托 graphkit** 算向量与相似度——与 test_g 沙盒
跑的是同一套算法，保证"沙盒验证 = 生产行为"。

与旧 provider 的本质差异：
- bge_centroid：只连有 chunk 向量的论文，且靠脆弱的全局中心化拉分布。
- facet_weighted：只在 3 篇已解读论文间连边，叶子全是孤立引用节点。
- **unified**：给**每篇**论文挑最佳主题向量（ChromaDB 质心 > title/abstract embedding），
  全量连接成研究地图；已解读论文对再挂 facet 分解 detail（解释"为什么相似"）。
  去噪（互近邻）由 graph_builder 统一负责。

返回 (a,b,cosine) 给 graph_builder 去噪；facet 分解放在 self.details 供其挂到 edge.detail。
"""

import logging

from sqlmodel import Session, create_engine, select

from ....config import settings
from ....models.analysis import AnalysisResult
from ..embedder import embed_texts
from ..vector_store import get_or_create
from ..graphkit import PaperFeatures, FacetEngine, build_topic_vectors, cosine_pairs

logger = logging.getLogger(__name__)

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})
    return _engine


# 主题代表性强的章节关键词（命中则加权，让质心更偏向"研究主题/目的"）
_TOPIC_SECTIONS = ("abstract", "intro", "conclusion", "overview")


def _chunk_weight(meta: dict) -> float:
    """单个 chunk 在论文质心里的权重。

    动机：简单平均会让到处雷同的实现细节（backbone/优化器/超参）稀释主题信号，
    使本不相关的论文也"看起来相似"。据章节/位置加权可让质心更贴近研究方向：
    - 摘要/引言/结论：最能代表主题 → 2.0
    - 正文滑窗（section_name=="Body"）：按位置衰减，开头(题录/摘要/引言)更代表主题
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


class UnifiedProvider:
    name = "unified"

    def __init__(self, weights: dict | None = None, **_):
        # facet 权重透传给 FacetEngine（用于已解读论文对的可解释分解 detail）
        self.weights = weights or None
        self.details: dict[tuple[int, int], dict] = {}

    def _doc_centroids(self, ws_id: int, paper_ids: list[int]) -> dict[int, list[float]]:
        """ChromaDB 里每篇论文 chunk embedding 的质心（覆盖所有下载过 PDF 的论文）。"""
        import numpy as np
        out: dict[int, list[float]] = {}
        try:
            col = get_or_create(ws_id)
        except Exception as ex:
            logger.warning(f"unified: 向量库不可用，doc_vector 全空: {ex}")
            return out
        for pid in paper_ids:
            try:
                res = col.get(where={"paper_id": pid}, include=["embeddings", "metadatas"])
                embs = res["embeddings"] if isinstance(res, dict) else None
                arr = np.asarray(embs) if embs is not None else None
                if arr is None or arr.size == 0 or arr.ndim < 2:
                    continue
                metas = (res.get("metadatas") if isinstance(res, dict) else None) or []
                norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9
                unit = arr / norms                              # 每个 chunk 向量单位化
                if len(metas) == len(unit):
                    w = np.array([_chunk_weight(m or {}) for m in metas], dtype=float).reshape(-1, 1)
                else:
                    w = np.ones((len(unit), 1))                 # 元数据对不齐 → 退回等权平均
                mean_vec = (unit * w).sum(axis=0) / (float(w.sum()) + 1e-9)
                out[pid] = (mean_vec / (np.linalg.norm(mean_vec) + 1e-9)).tolist()
            except Exception as ex:
                logger.debug(f"unified: skip centroid for paper {pid}: {ex}")
        return out

    def _build_features(self, papers: list, ws_id: int) -> list[PaperFeatures]:
        pids = [p.id for p in papers]
        with Session(_get_engine()) as s:
            analyses = {a.paper_id: a for a in s.exec(
                select(AnalysisResult).where(AnalysisResult.paper_id.in_(pids))
            ).all()}
        centroids = self._doc_centroids(ws_id, pids)

        feats: list[PaperFeatures] = []
        for p in papers:
            a = analyses.get(p.id)
            digest = getattr(p, "graph_digest", None)
            abstract = (getattr(p, "full_text", None) or "")[:1200].strip()
            if a is not None:
                # 全量精读：facet 取自 AnalysisResult（最完整）
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
                # 图谱速读：没有精读时用轻量 digest 的 facet（同样参与可解释关系）
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

    async def score_pairs(self, papers: list, *, ws_id: int) -> list[tuple[int, int, float]]:
        self.details = {}
        feats = self._build_features(papers, ws_id)

        vectors = await build_topic_vectors(feats, embed_fn=embed_texts)
        if len(vectors) < 2:
            logger.warning(f"unified: 只有 {len(vectors)} 篇论文有可用主题向量，无法连边")
            return []
        pairs = cosine_pairs(vectors)

        # 已解读论文对的可解释 facet 分解（挂到 edge.detail，不改 headline 权重）
        try:
            engine = FacetEngine(weights=self.weights)
            _, self.details = await engine.score_pairs(feats, embed_fn=embed_texts)
        except Exception as ex:
            logger.warning(f"unified: facet 分解失败（不影响连边）: {ex}")
            self.details = {}

        n_vec = len(vectors)
        n_analyzed = sum(1 for f in feats if f.is_analyzed)
        logger.info(f"unified: {n_vec}/{len(feats)} 篇有主题向量, {n_analyzed} 篇已解读, {len(pairs)} 对待去噪")
        return pairs
