"""
SimilarityProvider — 论文级相似度计算的抽象接口

为什么要抽象：
- 当前默认实现（bge_centroid）用 BGE chunk 向量取平均做论文级向量，再算余弦。
  这种"平均"对长文会向"通用 ML 论文中心"塌陷，难以区分细分方向。
- BGE 本身是 bi-encoder 检索语义模型，优化目标是"句子检索文档"，不是
  "两篇论文是否研究同一问题"。
- 未来可以无缝替换为：
  * specter2 / scincl  —— 科研论文专用 embedding（用引用对训练）
  * bibcoupling        —— 共享引用 Jaccard
  * hybrid             —— 多信号融合
  * cross_encoder      —— 候选对先语义召回，再用 reranker 精排
- 通过本接口，任何新方法只需实现 `score_pairs`，注册到 `__init__.py` 即可启用。
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class SimilarityProvider(Protocol):
    """计算工作区内论文两两相似度的 Provider 协议。"""

    name: str

    async def score_pairs(self, papers: list, *, ws_id: int) -> list[tuple[int, int, float]]:
        """对论文集合计算两两相似度。

        Args:
            papers: list[Paper]（SQLModel），保证至少含 id / title。
            ws_id:  工作区 ID，便于 provider 取 ChromaDB collection 或外部缓存。

        Returns:
            list[(paper_id_a, paper_id_b, score)]：
            - 对称对（a, b）仅返回一次（约定 a < b），由 graph_builder 决定是否生成双向边。
            - score ∈ [0, 1]，由 graph_builder 用配置的 threshold 过滤。
            - 无法计算的对（如缺向量、缺数据）直接省略即可。
        """
        ...
