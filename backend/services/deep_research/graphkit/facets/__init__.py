"""
Facet 注册中心

加一个新关系维度：
1. 新建 facets/{your_facet}.py，继承 EmbeddingFacet 或 SetFacet（见 base.py）
2. 在下方 FACET_REGISTRY 注册
3. 需要默认启用就加进 build_default_facets()

引擎、前端、生产 provider 都不用改——这就是"可扩展"的含义。
"""

from .base import Facet, FacetScore, EmbeddingFacet, SetFacet
from .purpose import PurposeFacet
from .method import MethodFacet
from .dataset import DatasetFacet
from .baseline import BaselineFacet

FACET_REGISTRY: dict[str, type[Facet]] = {
    PurposeFacet.name: PurposeFacet,
    MethodFacet.name: MethodFacet,
    DatasetFacet.name: DatasetFacet,
    BaselineFacet.name: BaselineFacet,
}


def build_default_facets(names: list[str] | None = None, *, center: bool = True) -> list[Facet]:
    """实例化默认 facets。names=None 时用全部已注册的。"""
    names = names or list(FACET_REGISTRY.keys())
    out: list[Facet] = []
    for n in names:
        cls = FACET_REGISTRY.get(n)
        if cls is None:
            continue
        # EmbeddingFacet 接受 center；SetFacet 忽略多余 kwargs
        try:
            out.append(cls(center=center))  # type: ignore[call-arg]
        except TypeError:
            out.append(cls())
    return out


__all__ = [
    "Facet", "FacetScore", "EmbeddingFacet", "SetFacet",
    "PurposeFacet", "MethodFacet", "DatasetFacet", "BaselineFacet",
    "FACET_REGISTRY", "build_default_facets",
]
