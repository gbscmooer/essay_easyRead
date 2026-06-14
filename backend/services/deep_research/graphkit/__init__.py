"""
graphkit — 论文图谱算法的纯净核心（decoupled）

设计目标：
- **零系统依赖**：本包不 import DB / ChromaDB / FastAPI / config.settings / embedder。
  所有外部能力（如向量化）通过参数注入（embed_fn）。
- **纯数据输入**：算法只吃 `list[PaperFeatures]`（见 features.py），便于在 test_g/
  沙盒里用 fixtures + 假 embedder 离线复现、单测、调参，不受其他系统干扰。
- **模块化多字段**：论文间的每一种"关系维度"是一个可插拔的 Facet（见 facets/），
  加新维度 = 新写一个 Facet 子类 + 注册，引擎自动组合。

与生产的关系：
- 生产侧（耦合层）负责把 DB/ChromaDB 的数据抽取成 `PaperFeatures`，再调用本包；
- 本包产出的 pairs/details/边，由生产侧落库、推 SSE。
- 因此 `similarity/facet_weighted.py` 后续可改为"抽取 features → 委托 FacetEngine"，
  与 test_g 共用同一套算法。
"""

from .features import PaperFeatures
from .engine import FacetEngine
from .facets import build_default_facets, FACET_REGISTRY
from .vectors import build_topic_vectors, cosine_pairs
from .knn import denoise_knn, local_scaling_affinity
from .backbone import extract_backbone
from .graph import build_unified_graph

__all__ = [
    "PaperFeatures", "FacetEngine", "build_default_facets", "FACET_REGISTRY",
    "build_topic_vectors", "cosine_pairs", "denoise_knn", "local_scaling_affinity",
    "extract_backbone", "build_unified_graph",
]
