"""
graphkit — 论文图谱算法的纯净核心（decoupled）

设计目标：
- **零系统依赖**：本包不 import DB / ChromaDB / FastAPI / config.settings / embedder。
  所有外部能力（如向量化）通过参数注入（embed_fn）。
- **纯数据输入**：算法只吃 `list[PaperFeatures]`（见 features.py），可用 fixtures +
  假 embedder 离线复现、单测、调参。
- **单一算法**：论文关联度只有 `relatedness.compute_relations` 一套实现（v3 起不再
  有可切换的 similarity provider）。它把语义向量、术语、共享实体、引用结构合成一个
  可解释的分数，同时决定连边、社区、重要性与图检索。

模块分工：
    features.py     纯数据边界 PaperFeatures
    vectors.py      主题向量（doc_vector 优先，回退 topic_text embedding）
    lexical.py      IDF 加权术语重叠
    structural.py   文献耦合 / 共引（Salton）
    relatedness.py  ★ 唯一的关联度算法：多通道分位数归一 + 加权融合 + 可解释分解
    calibration.py  ★ 通道权重自监督校准：用工作区自身引用结构当弱标签定权重
    backbone.py     多尺度骨架提取（Disparity Filter + 目标密度反解），决定"留哪些边"
"""

from .backbone import extract_backbone, extract_backbone_with_bridges
from .calibration import (
    CalibrationReport,
    build_supervision,
    calibrate_weights,
    rank_auc,
)
from .features import PaperFeatures
from .lexical import build_idf, extract_terms, idf_cosine
from .relatedness import (
    DEFAULT_WEIGHTS,
    EVIDENCE,
    SEMANTIC,
    RelationResult,
    channel_catalog,
    compute_relations,
    normalize_weights,
)
from .structural import (
    build_cited_by_map,
    build_cites_map,
    co_citation_from_cites_edges,
    compute_bibliographic_coupling_pairs,
    compute_co_citation_pairs,
    pair_map,
)
from .vectors import build_topic_vectors, cosine_pairs

__all__ = [
    "PaperFeatures",
    "compute_relations", "RelationResult", "channel_catalog",
    "DEFAULT_WEIGHTS", "SEMANTIC", "EVIDENCE", "normalize_weights",
    "build_topic_vectors", "cosine_pairs",
    "build_idf", "extract_terms", "idf_cosine",
    "build_cites_map", "compute_bibliographic_coupling_pairs",
    "build_cited_by_map", "compute_co_citation_pairs",
    "co_citation_from_cites_edges", "pair_map",
    "extract_backbone", "extract_backbone_with_bridges",
    "CalibrationReport", "build_supervision", "calibrate_weights", "rank_auc",
]
