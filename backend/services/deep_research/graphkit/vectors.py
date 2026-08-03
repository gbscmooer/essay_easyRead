"""
vectors — 为每篇论文挑选"最佳可用的主题向量"（纯函数，无 DB）

这是把图谱从"3 星引用树"升级为"研究地图"的关键一步：**让每篇论文都拿到一个
语义向量**，而不只是 3 篇已解读的。优先级（信息量从高到低）：

1. doc_vector（生产里 = ChromaDB chunk 质心，覆盖所有下载过 PDF 的论文）
2. topic_text() 的 embedding（兜底：已解读用 purpose+method，叶子用 title+abstract）

维度一致性守卫：doc_vector 与 embed_fn 必须同维才能算余弦。生产中两者同源（同一
embedding 模型）天然一致；但沙盒用 fake_embed（低维）而 fixture 的 doc_vector 可能是
真实 bge（高维），混用会算出垃圾余弦。检测到维度冲突时，统一改用 embed_fn 重嵌入
**所有**论文的 topic_text，保证落在同一向量空间。
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

import numpy as np

from .features import PaperFeatures

logger = logging.getLogger(__name__)

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]


def _norm(v) -> np.ndarray:
    a = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(a))
    return a / n if n > 0 else a


async def build_topic_vectors(
    papers: list[PaperFeatures], *, embed_fn: EmbedFn
) -> dict[int, np.ndarray]:
    """返回 {paper_id: 单位向量}。无任何可用文本/向量的论文不在返回里。"""
    have_vec: dict[int, np.ndarray] = {
        p.id: _norm(p.doc_vector) for p in papers if p.doc_vector
    }
    need = [p for p in papers if p.id not in have_vec and p.topic_text()]

    emb: dict[int, np.ndarray] = {}
    if need:
        try:
            vecs = await embed_fn([p.topic_text() for p in need])
            emb = {p.id: _norm(v) for p, v in zip(need, vecs)}
        except Exception as ex:
            logger.warning(f"build_topic_vectors: embed_fn failed: {ex}")

    dims = {v.shape[0] for v in have_vec.values()} | {v.shape[0] for v in emb.values()}
    if len(dims) > 1:
        # 维度冲突（典型：沙盒 fake_embed vs 真实 bge 质心）→ 统一重嵌入所有 topic_text
        logger.info(f"build_topic_vectors: 维度不一致 {dims}，统一改用 embed_fn 重嵌入")
        allp = [p for p in papers if p.topic_text()]
        if not allp:
            return {}
        vecs = await embed_fn([p.topic_text() for p in allp])
        return {p.id: _norm(v) for p, v in zip(allp, vecs)}

    return {**have_vec, **emb}


def cosine_pairs(vectors: dict[int, np.ndarray]) -> list[tuple[int, int, float]]:
    """所有两两余弦（单位向量点积）。返回 (a, b, sim)，a<b，sim 截到 [0,1]。"""
    ids = sorted(vectors)
    if len(ids) < 2:
        return []
    mat = np.stack([vectors[i] for i in ids])           # (n, d)，已单位化
    sims = mat @ mat.T                                   # (n, n) 余弦矩阵
    out: list[tuple[int, int, float]] = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            out.append((ids[i], ids[j], round(float(max(0.0, sims[i, j])), 4)))
    return out
