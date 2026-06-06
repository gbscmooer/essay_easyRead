"""
Graph Builder — NetworkX 知识图谱构建

节点类型：paper（本期仅实现）
边类型：cites（基于 parent_paper_id）、similar（基于向量余弦相似度）
"""

import networkx as nx
import numpy as np
from .vector_store import get_or_create


def _paper_node(p) -> dict:
    return {
        "id": f"paper:{p.id}",
        "kind": "paper",
        "label": (p.title or p.filename)[:80],
        "meta": {
            "paper_id": p.id,
            "year": p.year,
            "venue": p.venue,
            "source": p.source,
            "status": p.status,
            "external_id": p.external_id,
        },
    }


def _cosine(a, b):
    a = np.array(a)
    b = np.array(b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


async def build_graph(ws_id: int, papers, *, similarity_threshold: float, enable_shared_method: bool) -> dict:
    g = nx.DiGraph()

    # 1) 节点
    for p in papers:
        g.add_node(f"paper:{p.id}", **_paper_node(p))

    # 2) cites 边：基于 expanded 关系（parent_paper_id → child）
    by_id = {p.id: p for p in papers}
    for p in papers:
        if p.parent_paper_id and p.parent_paper_id in by_id:
            g.add_edge(f"paper:{p.parent_paper_id}", f"paper:{p.id}",
                       kind="cites", weight=1.0)

    # 3) similar 边：基于每篇论文摘要级向量
    col = get_or_create(ws_id)
    # 取每篇论文的全部 chunk 的平均向量
    paper_vecs: dict[int, list[float]] = {}
    for p in papers:
        try:
            res = col.get(where={"paper_id": p.id}, include=["embeddings"])
            embs = res["embeddings"] if isinstance(res, dict) else None
            arr = np.asarray(embs) if embs is not None else None
            if arr is not None and arr.size > 0 and arr.ndim >= 2:
                vec = np.mean(arr, axis=0).tolist()
                paper_vecs[p.id] = vec
        except Exception as ex:
            __import__("logging").getLogger(__name__).warning(
                f"skip paper {p.id} in similarity step: {ex}"
            )

    ids = list(paper_vecs)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            sim = _cosine(paper_vecs[ids[i]], paper_vecs[ids[j]])
            if sim >= similarity_threshold:
                g.add_edge(f"paper:{ids[i]}", f"paper:{ids[j]}",
                           kind="similar", weight=sim)
                # 无向：再加反向
                g.add_edge(f"paper:{ids[j]}", f"paper:{ids[i]}",
                           kind="similar", weight=sim)

    # 4) （可选）shared-method 边：对相似度高的 pair 调一次 LLM
    # 当前为占位实现，enable_shared_method=True 时仅做日志提示
    if enable_shared_method:
        logger = __import__("logging").getLogger(__name__)
        logger.warning("shared-method 边生成暂未实现（method_extractor 模块待开发），跳过")

    # 5) 序列化为 dict
    nodes = [g.nodes[n] for n in g.nodes]
    edges = [{**g.edges[s, t], "source": s, "target": t} for s, t in g.edges]
    stats = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "by_kind": {k: sum(1 for e in edges if e["kind"] == k) for k in ("cites", "similar", "shared-method")},
    }
    return {"nodes": nodes, "edges": edges, "stats": stats}
