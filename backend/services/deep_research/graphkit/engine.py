"""
FacetEngine — 组合多个 Facet，算出论文两两的加权相似度 + 可解释分解

这是 facet_weighted 的纯净核心：
- 输入：list[PaperFeatures] + 注入的 embed_fn
- 输出：pairs = [(a, b, score)]，details = {(min,max): {weight, components:[...]}}
  其中 components 就是前端 EdgeDetail 要的每维度 sim×权重→贡献+理由+共享项。

它不碰 DB / settings / ChromaDB，因此 test_g 与生产可共用同一份算法。

未来要"每维独立成边"（多关系图谱）时，components 已经天然带 facet 维度，
只需在 graph 层按 facet 拆边即可，无需改本引擎。
"""

from __future__ import annotations

import logging

from .features import PaperFeatures
from .facets import Facet, build_default_facets
from .facets.base import EmbedFn

logger = logging.getLogger(__name__)


class FacetEngine:
    def __init__(self, facets: list[Facet] | None = None, weights: dict[str, float] | None = None):
        self.facets = facets if facets is not None else build_default_facets()
        # 权重：未指定的 facet 用其 default_weight；整体归一化到 1
        raw = {f.name: float((weights or {}).get(f.name, f.default_weight)) for f in self.facets}
        total = sum(max(0.0, v) for v in raw.values()) or 1.0
        self.weights = {k: max(0.0, v) / total for k, v in raw.items()}

    async def score_pairs(
        self, papers: list[PaperFeatures], *, embed_fn: EmbedFn
    ) -> tuple[list[tuple[int, int, float]], dict[tuple[int, int], dict]]:
        # 只在"已解读"论文间算语义关系（叶子论文交给引用关系，见 graph 层）
        usable = [p for p in papers if p.is_analyzed]
        if len(usable) < 2:
            logger.warning("FacetEngine: 已解读论文不足 2 篇，无法计算 facet 关系")
            return [], {}

        for f in self.facets:
            await f.prepare(usable, embed_fn=embed_fn)

        pairs: list[tuple[int, int, float]] = []
        details: dict[tuple[int, int], dict] = {}
        for i in range(len(usable)):
            for j in range(i + 1, len(usable)):
                a, b = usable[i], usable[j]
                components, score = [], 0.0
                for f in self.facets:
                    fs = f.score(a, b)
                    w = self.weights.get(f.name, 0.0)
                    contrib = round(fs.sim * w, 4)
                    score += contrib
                    components.append({
                        "facet": f.name,
                        "label": f.label,
                        "sim": fs.sim,
                        "weight": round(w, 4),
                        "contribution": contrib,
                        "reason": fs.reason,
                        "shared": fs.shared,
                    })
                score = round(min(score, 1.0), 4)
                key = (min(a.id, b.id), max(a.id, b.id))
                pairs.append((a.id, b.id, score))
                details[key] = {"weight": score, "components": components}
        return pairs, details
