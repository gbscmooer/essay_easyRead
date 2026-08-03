"""
Stage 5 — graph

调用 graph_builder 构建知识图谱。
"""

from ..graph_builder import DEFAULT_BACKBONE_ALPHA, DEFAULT_MAP_DENSITY, build_graph


async def stage_graph(ws_id: int, papers, *, enable_shared_method: bool = False,
                      map_density: float = DEFAULT_MAP_DENSITY,
                      backbone_alpha: float = DEFAULT_BACKBONE_ALPHA,
                      citation_contexts: dict[int, list[dict]] | None = None):
    return await build_graph(ws_id, papers,
                             enable_shared_method=enable_shared_method,
                             map_density=map_density,
                             backbone_alpha=backbone_alpha,
                             citation_contexts=citation_contexts or {})
