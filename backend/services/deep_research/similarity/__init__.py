"""
相似度 Provider 注册中心

添加新方法时：
1. 新建 backend/services/deep_research/similarity/{your_provider}.py，实现 SimilarityProvider 协议
2. 在下方 REGISTRY 注册
3. ai_config DEFAULT_CONFIG["similarity"]["provider"] 可以默认指向新方法
4. SettingsPage 的下拉选项中追加（前端可直接读 /api/research/similarity/providers）
"""

from .base import SimilarityProvider
from .bge_centroid import BgeCentroidProvider
from .bibcoupling import BibCouplingProvider
from .hybrid import HybridProvider
from .facet_weighted import FacetWeightedProvider
from .unified import UnifiedProvider

REGISTRY: dict[str, type] = {
    "unified": UnifiedProvider,          # 新默认：全量主题向量 + 可解释 facet detail
    "bge_centroid": BgeCentroidProvider,
    "bibcoupling": BibCouplingProvider,
    "hybrid": HybridProvider,
    "facet_weighted": FacetWeightedProvider,
}


def get_similarity_provider(config: dict) -> SimilarityProvider:
    """根据配置实例化 Provider。

    Args:
        config: 形如 {"provider": "bge_centroid", "<name>": {<kwargs>}}
    """
    name = (config or {}).get("provider", "unified")
    cls = REGISTRY.get(name)
    if cls is None:
        cls = UnifiedProvider
    kwargs = (config or {}).get(name, {}) or {}
    return cls(**kwargs)


def list_providers() -> list[str]:
    return list(REGISTRY.keys())
