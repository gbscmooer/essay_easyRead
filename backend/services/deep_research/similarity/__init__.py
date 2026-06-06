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

REGISTRY: dict[str, type] = {
    "bge_centroid": BgeCentroidProvider,
    # TODO: 未来扩展示例
    # "specter2":          SpecterProvider,       # AllenAI 科研 embedding
    # "bibcoupling":       BibCouplingProvider,   # 共享引用 Jaccard
    # "hybrid":            HybridProvider,        # specter2 + bibcoupling 融合
    # "cross_encoder":     CrossEncoderProvider,  # 复用 BAAI/bge-reranker-v2-m3
}


def get_similarity_provider(config: dict) -> SimilarityProvider:
    """根据配置实例化 Provider。

    Args:
        config: 形如 {"provider": "bge_centroid", "<name>": {<kwargs>}}
    """
    name = (config or {}).get("provider", "bge_centroid")
    cls = REGISTRY.get(name)
    if cls is None:
        cls = BgeCentroidProvider
    kwargs = (config or {}).get(name, {}) or {}
    return cls(**kwargs)


def list_providers() -> list[str]:
    return list(REGISTRY.keys())
