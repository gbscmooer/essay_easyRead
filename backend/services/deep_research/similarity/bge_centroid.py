"""
默认实现：bge_centroid

策略：
- 读取该工作区 ChromaDB collection 里每篇论文的所有 chunk embedding
- 每个 chunk 先 L2 归一化（避免长 chunk 模长干扰），再取平均，再归一化得到论文级向量
- 余弦相似度

已知局限（写给未来想替换它的你）：
- 平均会向"通用 ML 论文中心"塌陷 —— 长文尤其严重
- BGE 是 bi-encoder 检索语义模型，不擅长"两篇论文是否研究同一问题"
- 替换思路在 base.py 的注释里
"""

import logging
import numpy as np
from ..vector_store import get_or_create

logger = logging.getLogger(__name__)


class BgeCentroidProvider:
    name = "bge_centroid"

    def __init__(self, center: bool = True, **_):
        # center: 计算余弦前先减去全局均值向量再归一化。
        # BGE 向量不是零中心的（都挤在一个窄锥里），不中心化时任意两篇论文余弦
        # 普遍落在 0.75~0.95，绝对阈值几乎失效（实测 45 篇里 0.78 保留 99% 边）。
        # 中心化把分布拉开到可区分的范围，配合 graph_builder 的 kNN 稀疏化效果最佳。
        self.center = center

    async def score_pairs(self, papers: list, *, ws_id: int) -> list[tuple[int, int, float]]:
        col = get_or_create(ws_id)
        paper_vecs: dict[int, np.ndarray] = {}
        for p in papers:
            try:
                res = col.get(where={"paper_id": p.id}, include=["embeddings"])
                embs = res["embeddings"] if isinstance(res, dict) else None
                arr = np.asarray(embs) if embs is not None else None
                if arr is None or arr.size == 0 or arr.ndim < 2:
                    continue
                # 每个 chunk 先归一化，再平均，再归一化
                norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9
                normalized = arr / norms
                mean_vec = normalized.mean(axis=0)
                final = mean_vec / (np.linalg.norm(mean_vec) + 1e-9)
                paper_vecs[p.id] = final
            except Exception as ex:
                logger.warning(f"bge_centroid: skip paper {p.id}: {ex}")

        ids = sorted(paper_vecs.keys())
        if not ids:
            return []

        mat = np.stack([paper_vecs[i] for i in ids])  # (n, d)
        # 中心化：减全局均值后再 L2 归一化，消除 BGE 余弦整体偏高的问题
        if self.center and len(ids) >= 3:
            mat = mat - mat.mean(axis=0)
            mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)

        out: list[tuple[int, int, float]] = []
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                sim = float(np.dot(mat[i], mat[j]))  # 单位向量点积 = 余弦
                # 中心化后余弦可能为负（低于平均相似度），截断到 0 视作"不相关"
                sim = max(0.0, sim)
                out.append((ids[i], ids[j], round(sim, 4)))
        return out
