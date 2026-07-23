"""
Graph Retriever — 查询相关 Personalized PageRank 与结构邻居扩展

供 P7 RAG 路由调用；本模块不直接拼 prompt。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

import networkx as nx

from ...models.recall import RecallCandidate, RecallSource, build_recall_candidate

logger = logging.getLogger(__name__)

SOURCE_GRAPH_PPR = "graph_ppr"
SOURCE_GRAPH_COCITE = "graph_cocite"
SOURCE_GRAPH_BIBCOUPLING = "graph_bibcoupling"

# 参与 PPR 的结构边；similar 中的连通性兜底桥接边权重会被压低
DEFAULT_PPR_EDGE_KINDS = frozenset({"cites", "bibcoupling", "cocitation", "similar"})


@dataclass
class GraphRetrieverConfig:
    """图谱召回配置；各图源可单独关闭。"""

    enable_ppr: bool = True
    enable_cocite_neighbors: bool = True
    enable_bibcoupling_neighbors: bool = True
    ppr_alpha: float = 0.85
    top_k: int = 50
    edge_kinds: frozenset[str] = DEFAULT_PPR_EDGE_KINDS
    bridge_weight_factor: float = 0.0  # 弱桥接边在转移矩阵中的权重倍率（0=不参与结构 boost）
    min_ppr_score: float = 1e-9
    # cites 边补低权反向边（cited→citing）。引文图中扩展论文往往只有入边（dangling），
    # PPR 质量到达后只能被 teleport 抽回 seed，无法流向"共同引用它的其他论文"。
    # 反向边让经典论文成为导流枢纽：seed→经典→(反向)→其他引用者 = 协同过滤信号。
    # 实测 ws1 图 65% 节点 dangling，是图通道退化的主因之一。0=关闭。
    cites_reverse_factor: float = 0.25
    # score_papers_ppr 结果剔除 seed 论文本身。seed 来自 dense 命中，dense/bm25 通道
    # 已为其投票；图通道再投是重复信息（实测 PPR 质量平均 54.6% 落在 seed 上）。
    # 剔除后图通道只贡献增量论文。retrieve_from_graph（发现路径）本就剔除 seed，此处对齐。
    exclude_seeds: bool = True


@dataclass
class GraphRecallHit:
    """图谱召回命中（可转 RecallCandidate）。"""

    paper_id: int
    rank: int
    raw_score: float
    source: str
    seed_paper_id: str | None = None
    seed_relation: str | None = None
    provenance: list[str] = field(default_factory=list)


def parse_paper_node_id(node_id: str | None) -> int | None:
    if not node_id or not str(node_id).startswith("paper:"):
        return None
    try:
        return int(str(node_id).split(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _edge_weight(edge: dict, *, bridge_factor: float) -> float:
    w = float(edge.get("weight", 1.0) or 1.0)
    if edge.get("bridge") and bridge_factor <= 0.0:
        return 0.0
    if edge.get("bridge"):
        return w * bridge_factor
    return w


def build_ppr_graph(
    nodes: list[dict],
    edges: list[dict],
    *,
    edge_kinds: frozenset[str] = DEFAULT_PPR_EDGE_KINDS,
    bridge_weight_factor: float = 0.0,
    cites_reverse_factor: float = 0.0,
) -> nx.DiGraph:
    """构建 PPR 用的有向加权图。

    语义边（similar/bibcoupling/cocitation）在 graph_builder 中已双向存储；
    cites 边只有 citing→cited 方向。``cites_reverse_factor`` > 0 时为每条
    cites 边补一条 cited→citing 的低权反向边（权重 = 原权 × 因子，与已有
    边取 max 合并），让 PPR 质量能穿过只有入边的 dangling 引用节点。
    """

    def _merge_edge(s: str, t: str, w: float) -> None:
        if g.has_edge(s, t):
            g[s][t]["weight"] = max(g[s][t]["weight"], w)
        else:
            g.add_edge(s, t, weight=w)

    g = nx.DiGraph()
    for n in nodes:
        nid = n.get("id")
        if nid:
            g.add_node(nid)
    for e in edges:
        kind = e.get("kind", "")
        if kind not in edge_kinds:
            continue
        s, t = e.get("source"), e.get("target")
        if not s or not t or s == t:
            continue
        w = _edge_weight(e, bridge_factor=bridge_weight_factor)
        if w <= 0:
            continue
        _merge_edge(s, t, w)
        if kind == "cites" and cites_reverse_factor > 0:
            _merge_edge(t, s, w * cites_reverse_factor)
    return g


def personalized_pagerank(
    graph: nx.DiGraph,
    seed_ids: Iterable[int],
    *,
    alpha: float = 0.85,
) -> dict[int, float]:
    """
    以 seed 论文为 personalization 向量的 PageRank。

    Returns:
        {paper_id: score}
    """
    seeds = [int(s) for s in seed_ids]
    if not seeds or graph.number_of_nodes() == 0:
        return {}

    personalization: dict[str, float] = {}
    for sid in seeds:
        nid = f"paper:{sid}"
        if graph.has_node(nid):
            personalization[nid] = personalization.get(nid, 0.0) + 1.0
    if not personalization:
        return {}

    total = sum(personalization.values())
    personalization = {k: v / total for k, v in personalization.items()}

    try:
        pr = nx.pagerank(
            graph,
            alpha=alpha,
            personalization=personalization,
            weight="weight",
            max_iter=200,
            tol=1e-6,
        )
    except Exception as ex:
        logger.warning("personalized_pagerank failed: %s", ex)
        return {}

    out: dict[int, float] = {}
    for nid, score in pr.items():
        pid = parse_paper_node_id(nid)
        if pid is not None:
            out[pid] = float(score)
    return out


def _neighbor_map(edges: list[dict], kind: str) -> dict[int, list[tuple[int, float]]]:
    """paper_id -> [(neighbor_id, weight)]"""
    neighbors: dict[int, dict[int, float]] = {}
    for e in edges:
        if e.get("kind") != kind:
            continue
        a = parse_paper_node_id(e.get("source"))
        b = parse_paper_node_id(e.get("target"))
        if a is None or b is None:
            continue
        w = float(e.get("weight", 1.0) or 1.0)
        neighbors.setdefault(a, {})
        neighbors[a][b] = max(neighbors[a].get(b, 0.0), w)
    return {k: sorted(v.items(), key=lambda x: x[1], reverse=True) for k, v in neighbors.items()}


def expand_structural_neighbors(
    seed_ids: Iterable[int],
    edges: list[dict],
    *,
    enable_cocite: bool = True,
    enable_bibcoupling: bool = True,
    top_neighbors: int = 10,
) -> list[GraphRecallHit]:
    """从 seed 论文沿 cocitation / bibcoupling 边扩展邻居。"""
    hits: list[GraphRecallHit] = []
    seen: set[tuple[int, str, int]] = set()

    maps: list[tuple[str, dict[int, list[tuple[int, float]]]]] = []
    if enable_cocite:
        maps.append((SOURCE_GRAPH_COCITE, _neighbor_map(edges, "cocitation")))
    if enable_bibcoupling:
        maps.append((SOURCE_GRAPH_BIBCOUPLING, _neighbor_map(edges, "bibcoupling")))

    for seed in seed_ids:
        sid = int(seed)
        for source, nmap in maps:
            for nb_id, weight in nmap.get(sid, [])[:top_neighbors]:
                key = (sid, source, nb_id)
                if key in seen or nb_id == sid:
                    continue
                seen.add(key)
                hits.append(GraphRecallHit(
                    paper_id=nb_id,
                    rank=0,
                    raw_score=weight,
                    source=source,
                    seed_paper_id=str(sid),
                    seed_relation=source.split("_", 1)[-1],
                    provenance=[f"source:{source}", f"seed:{sid}"],
                ))

    # 按分数重排
    hits.sort(key=lambda h: h.raw_score, reverse=True)
    for i, h in enumerate(hits, start=1):
        h.rank = i
    return hits


def _paper_ids_from_graph(graph: dict[str, Any]) -> list[int]:
    out: list[int] = []
    for node in graph.get("nodes") or []:
        pid = parse_paper_node_id(node.get("id"))
        if pid is None:
            meta = node.get("meta") or {}
            raw = meta.get("paper_id")
            try:
                pid = int(raw) if raw is not None else None
            except (TypeError, ValueError):
                pid = None
        if pid is not None:
            out.append(pid)
    return out


def _default_ppr_seeds(graph: dict[str, Any], paper_ids: list[int] | None) -> list[int]:
    """无显式 seed 时：优先 importance 高的节点，否则取图内前若干篇。"""
    scored: list[tuple[float, int]] = []
    for node in graph.get("nodes") or []:
        pid = parse_paper_node_id(node.get("id"))
        if pid is None:
            continue
        if paper_ids is not None and pid not in paper_ids:
            continue
        meta = node.get("meta") or {}
        imp = float(meta.get("importance") or meta.get("ppr_semantic") or 0.0)
        scored.append((imp, pid))
    scored.sort(key=lambda x: (-x[0], x[1]))
    seeds = [pid for _, pid in scored[:5]]
    if seeds:
        return seeds
    all_pids = _paper_ids_from_graph(graph)
    if paper_ids is not None:
        allow = set(paper_ids)
        all_pids = [p for p in all_pids if p in allow]
    return all_pids[:5]


def score_papers_ppr(
    *,
    graph: dict[str, Any],
    question: str = "",
    paper_ids: list[int] | None = None,
    seed_ids: list[int] | None = None,
    config: GraphRetrieverConfig | None = None,
) -> dict[int, float]:
    """P7 HybridRetriever 适配入口：返回 {paper_id: ppr_score}。

    - ``seed_ids``：personalization 种子（通常来自 dense 命中论文）
    - ``paper_ids``：结果过滤（工作区/用户勾选范围）
    - ``question``：保留参数以兼容调用方；结构 PPR 本身不消费文本
    """
    _ = question  # 结构 PPR 暂不使用 query 文本
    cfg = config or GraphRetrieverConfig()
    if not cfg.enable_ppr:
        return {}

    nodes = list(graph.get("nodes") or [])
    edges = list(graph.get("edges") or [])
    if not nodes:
        return {}

    seeds = [int(s) for s in (seed_ids or [])]
    if not seeds:
        seeds = _default_ppr_seeds(graph, paper_ids)
    if not seeds:
        return {}

    ppr_g = build_ppr_graph(
        nodes,
        edges,
        edge_kinds=cfg.edge_kinds,
        bridge_weight_factor=cfg.bridge_weight_factor,
        cites_reverse_factor=cfg.cites_reverse_factor,
    )
    scores = personalized_pagerank(ppr_g, seeds, alpha=cfg.ppr_alpha)
    if paper_ids is not None:
        allow = set(int(p) for p in paper_ids)
        scores = {pid: sc for pid, sc in scores.items() if pid in allow}
    if cfg.exclude_seeds:
        seed_set = set(seeds)
        scores = {pid: sc for pid, sc in scores.items() if pid not in seed_set}
    return {pid: sc for pid, sc in scores.items() if sc >= cfg.min_ppr_score}


def retrieve_from_graph(
    graph: dict[str, Any],
    seed_ids: Iterable[int],
    papers_by_id: dict[int, Any] | None = None,
    *,
    config: GraphRetrieverConfig | None = None,
) -> list[GraphRecallHit]:
    """
    图谱召回主入口：PPR + 可选结构邻居扩展。

  当 enable_ppr=False 且邻居扩展关闭时返回空列表（非图 baseline 可复现）。
    """
    cfg = config or GraphRetrieverConfig()
    nodes = list(graph.get("nodes") or [])
    edges = list(graph.get("edges") or [])
    seeds = [int(s) for s in seed_ids]
    hits: list[GraphRecallHit] = []

    if cfg.enable_ppr and seeds:
        ppr_g = build_ppr_graph(
            nodes, edges,
            edge_kinds=cfg.edge_kinds,
            bridge_weight_factor=cfg.bridge_weight_factor,
            cites_reverse_factor=cfg.cites_reverse_factor,
        )
        scores = personalized_pagerank(ppr_g, seeds, alpha=cfg.ppr_alpha)
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        for rank, (pid, score) in enumerate(ranked, start=1):
            if score < cfg.min_ppr_score or pid in seeds:
                continue
            hits.append(GraphRecallHit(
                paper_id=pid,
                rank=rank,
                raw_score=score,
                source=SOURCE_GRAPH_PPR,
                seed_paper_id=",".join(str(s) for s in seeds),
                seed_relation="ppr",
                provenance=[f"source:{SOURCE_GRAPH_PPR}", f"seeds:{','.join(map(str, seeds))}"],
            ))
            if len(hits) >= cfg.top_k:
                break

    if cfg.enable_cocite_neighbors or cfg.enable_bibcoupling_neighbors:
        neighbor_hits = expand_structural_neighbors(
            seeds, edges,
            enable_cocite=cfg.enable_cocite_neighbors,
            enable_bibcoupling=cfg.enable_bibcoupling_neighbors,
        )
        hits.extend(neighbor_hits)

    return hits[: cfg.top_k]


def graph_hit_to_recall_candidate(
    hit: GraphRecallHit,
    paper: Any | None = None,
) -> RecallCandidate | None:
    """将 GraphRecallHit 转为统一 RecallCandidate（供融合层使用）。"""
    title = ""
    doi = arxiv_id = None
    if paper is not None:
        title = (getattr(paper, "title", None) or getattr(paper, "filename", None) or "").strip()
        doi = getattr(paper, "doi", None)
        arxiv_id = getattr(paper, "arxiv_id", None)
    if not title:
        title = f"Paper {hit.paper_id}"

    provenance = list(hit.provenance)
    provenance.append(f"source:{hit.source}")

    return build_recall_candidate(
        title=title,
        doi=doi,
        arxiv_id=arxiv_id,
        source=RecallSource.RELATED,
        source_rank=hit.rank,
        raw_score=hit.raw_score,
        seed_paper_id=hit.seed_paper_id,
        seed_relation=hit.seed_relation,
        provenance=provenance,
    )


def retrieve_as_candidates(
    graph: dict[str, Any],
    seed_ids: Iterable[int],
    papers_by_id: dict[int, Any] | None = None,
    *,
    config: GraphRetrieverConfig | None = None,
) -> list[RecallCandidate]:
    """便捷封装：直接返回 RecallCandidate 列表。"""
    papers_by_id = papers_by_id or {}
    out: list[RecallCandidate] = []
    for hit in retrieve_from_graph(graph, seed_ids, papers_by_id, config=config):
        cand = graph_hit_to_recall_candidate(hit, papers_by_id.get(hit.paper_id))
        if cand:
            out.append(cand)
    return out
