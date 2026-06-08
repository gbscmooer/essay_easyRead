"""
Stage 5 — graph

调用 graph_builder 构建知识图谱。
"""

from ..graph_builder import build_graph


async def stage_graph(ws_id: int, papers, *, similarity_threshold: float, enable_shared_method: bool,
                      knn_k: int = 8):
    return await build_graph(ws_id, papers,
                             similarity_threshold=similarity_threshold,
                             enable_shared_method=enable_shared_method,
                             knn_k=knn_k)
