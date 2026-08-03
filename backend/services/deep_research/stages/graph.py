"""
Stage 5 — graph

调用 graph_builder 构建知识图谱。
"""

from ..graph_builder import build_graph


async def stage_graph(ws_id: int, papers, *, enable_shared_method: bool = False,
                      backbone_alpha: float = 0.25,
                      citation_contexts: dict[int, list[dict]] | None = None):
    return await build_graph(ws_id, papers,
                             enable_shared_method=enable_shared_method,
                             backbone_alpha=backbone_alpha,
                             citation_contexts=citation_contexts or {})
