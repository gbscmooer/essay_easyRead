"""
Hybrid 相似度：多信号加权融合

策略：
- 同时运行多个 Provider（如 bge_centroid + bibcoupling）
- 对每对论文的 score 做加权平均
- 权重可通过配置调整，默认 0.7 语义 + 0.3 引用结构

优点：
- 语义相似度擅长"主题相近但引用不同"的论文对
- 引用耦合擅长"引用结构重叠但语义表述差异大"的论文对
- 互补覆盖

配置示例：
{
    "provider": "hybrid",
    "hybrid": {
        "providers": {
            "bge_centroid": {"weight": 0.7},
            "bibcoupling": {"weight": 0.3}
        }
    }
}
"""

import logging
from .base import SimilarityProvider

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS = {
    "bge_centroid": {"weight": 0.7},
    "bibcoupling": {"weight": 0.3},
}


def _get_provider_class(name: str):
    """延迟导入避免循环引用"""
    if name == "bge_centroid":
        from .bge_centroid import BgeCentroidProvider
        return BgeCentroidProvider
    elif name == "bibcoupling":
        from .bibcoupling import BibCouplingProvider
        return BibCouplingProvider
    return None


class HybridProvider:
    name = "hybrid"

    def __init__(self, providers: dict | None = None, **_):
        self._providers: list[tuple[SimilarityProvider, float]] = []
        cfg = providers or DEFAULT_WEIGHTS
        for pname, pcfg in cfg.items():
            weight = pcfg.get("weight", 0.5) if isinstance(pcfg, dict) else 0.5
            cls = _get_provider_class(pname)
            if cls is None:
                logger.warning(f"hybrid: unknown provider '{pname}', skip")
                continue
            kwargs = pcfg if isinstance(pcfg, dict) else {}
            self._providers.append((cls(**kwargs), weight))

        if not self._providers:
            logger.warning("hybrid: no valid sub-providers, fallback to bge_centroid")
            from .bge_centroid import BgeCentroidProvider
            self._providers = [(BgeCentroidProvider(), 1.0)]

    async def score_pairs(self, papers: list, *, ws_id: int) -> list[tuple[int, int, float]]:
        # 收集所有子 provider 的结果
        pair_scores: dict[tuple[int, int], list[tuple[float, float]]] = {}

        for provider, weight in self._providers:
            try:
                results = await provider.score_pairs(papers, ws_id=ws_id)
                for a, b, score in results:
                    key = (min(a, b), max(a, b))
                    pair_scores.setdefault(key, []).append((score, weight))
            except Exception as ex:
                logger.warning(f"hybrid: provider {provider.name} failed: {ex}")

        # 加权融合
        out: list[tuple[int, int, float]] = []
        for (a, b), scores in pair_scores.items():
            total_weight = sum(w for _, w in scores)
            if total_weight <= 0:
                continue
            fused = sum(s * w for s, w in scores) / total_weight
            out.append((a, b, round(min(fused, 1.0), 4)))

        return out
