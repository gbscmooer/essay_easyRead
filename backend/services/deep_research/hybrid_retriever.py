"""
HybridRetriever — dense + BM25 +（可选）graph PPR 多路召回，RRF 融合 + rerank 精排。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from .fusion_ranker import DEFAULT_RRF_K
from .lexical_index import BM25Index
from .reranker import rerank
from .vector_store import list_workspace_chunks, query_workspace

logger = logging.getLogger(__name__)

CHANNEL_DENSE = "dense"
CHANNEL_BM25 = "bm25"
CHANNEL_GRAPH = "graph_ppr"

DEFAULT_HYBRID_WEIGHTS: dict[str, float] = {
    CHANNEL_DENSE: 1.0,
    CHANNEL_BM25: 1.0,
    CHANNEL_GRAPH: 1.0,
}


def fuse_ranked_pools(
    pools: dict[str, list[str]],
    *,
    k: int = DEFAULT_RRF_K,
    weights: dict[str, float] | None = None,
) -> list[tuple[str, float, dict[str, int]]]:
    """对多路有序 chunk_id 列表做 RRF 融合（与 fusion_ranker 同公式）。"""
    wmap = {**DEFAULT_HYBRID_WEIGHTS, **(weights or {})}
    scores: dict[str, float] = {}
    channel_ranks: dict[str, dict[str, int]] = {}

    for channel in sorted(pools.keys()):
        weight = wmap.get(channel, 1.0)
        seen: set[str] = set()
        rank = 0
        for chunk_id in pools.get(channel) or []:
            if not chunk_id or chunk_id in seen:
                continue
            seen.add(chunk_id)
            rank += 1
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (k + rank)
            channel_ranks.setdefault(chunk_id, {})[channel] = rank

    fused = sorted(scores.keys(), key=lambda cid: (-scores[cid], cid))
    return [(cid, scores[cid], channel_ranks.get(cid, {})) for cid in fused]


def _chunk_id_from_hit(hit: dict[str, Any]) -> str:
    return str(
        hit.get("chunk_id")
        or hit.get("id")
        or f"{hit.get('paper_id')}:{hit.get('section_name', '')}:{hit.get('chunk_index', 0)}"
    )


def _attach_chunk_id(hit: dict[str, Any]) -> dict[str, Any]:
    out = dict(hit)
    out["chunk_id"] = _chunk_id_from_hit(hit)
    return out


def _graph_score_hook() -> Callable[..., dict[int, float]] | None:
    """可选接入 P6 graph_retriever；import 失败则返回 None。"""
    try:
        from .graph_retriever import score_papers_ppr  # type: ignore
    except ImportError:
        return None
    return score_papers_ppr


def get_graph_paper_scores(
    graph: dict[str, Any] | None,
    question: str,
    *,
    paper_ids: list[int] | None = None,
    seed_ids: list[int] | None = None,
    score_fn: Callable[..., dict[int, float]] | None = None,
    graph_config: dict[str, Any] | None = None,
) -> dict[int, float]:
    """图源 paper 级分数；失败或无图时返回空 dict。"""
    if not graph:
        return {}
    fn = score_fn or _graph_score_hook()
    if fn is None:
        return {}
    kwargs: dict[str, Any] = {
        "graph": graph,
        "question": question,
        "paper_ids": paper_ids,
        "seed_ids": seed_ids,
    }
    if graph_config:
        try:
            from .graph_retriever import GraphRetrieverConfig

            allowed = GraphRetrieverConfig.__dataclass_fields__.keys()
            kwargs["config"] = GraphRetrieverConfig(
                **{k: v for k, v in graph_config.items() if k in allowed}
            )
        except (ImportError, TypeError, ValueError):
            pass

    try:
        try:
            scores = fn(**kwargs)
        except TypeError:
            # 兼容不接受 config 的测试 hook / 旧扩展。
            kwargs.pop("config", None)
            try:
                scores = fn(**kwargs)
            except TypeError:
                kwargs.pop("seed_ids", None)
                scores = fn(**kwargs)
        if not isinstance(scores, dict):
            return {}
        return {int(k): float(v) for k, v in scores.items()}
    except Exception as exc:
        logger.warning(f"graph_ppr scoring failed: {exc}")
        return {}


# graph 通道每篇论文最多贡献的 chunk 数。旧行为（无上限）会把高分论文的全部
# chunk 平铺进池子，长论文淹没 RRF 排名（一篇 40-chunk 论文独占 rank 1-40）。
DEFAULT_GRAPH_POOL_PER_PAPER = 3


def _graph_chunk_pool(
    paper_scores: dict[int, float],
    chunks_by_paper: dict[int, list[str]],
    *,
    per_paper_cap: int = DEFAULT_GRAPH_POOL_PER_PAPER,
    chunk_scores: dict[str, float] | None = None,
) -> list[str]:
    """按论文 PPR 分数展开 chunk 池。

    - ``per_paper_cap``：每篇论文最多取多少 chunk（<=0 表示不限，复现旧行为）；
    - ``chunk_scores``：chunk 级参考分（dense/bm25 命中分），论文内部按它降序
      挑选代表 chunk；缺失分数的 chunk 按目录序兜底排后。
    """
    papers = sorted(paper_scores.keys(), key=lambda p: (-paper_scores[p], p))
    ref = chunk_scores or {}
    pool: list[str] = []
    seen: set[str] = set()
    for pid in papers:
        cids = chunks_by_paper.get(pid, [])
        if ref:
            order = {c: i for i, c in enumerate(cids)}
            cids = sorted(cids, key=lambda c: (-ref.get(c, 0.0), order[c]))
        picked = 0
        for cid in cids:
            if cid in seen:
                continue
            if per_paper_cap > 0 and picked >= per_paper_cap:
                break
            seen.add(cid)
            pool.append(cid)
            picked += 1
    return pool


@dataclass
class HybridRetrieveResult:
    hits: list[dict[str, Any]]
    debug: list[dict[str, Any]] = field(default_factory=list)


class HybridRetriever:
    """工作区 Hybrid RAG 检索器。"""

    def __init__(self, ws_id: int) -> None:
        self.ws_id = ws_id
        self._bm25: BM25Index | None = None
        self._chunk_catalog: dict[str, dict[str, Any]] = {}

    def _ensure_bm25(self, paper_ids: list[int] | None = None) -> BM25Index:
        if self._bm25 is None:
            chunks = list_workspace_chunks(self.ws_id, paper_ids=None)
            self._bm25 = BM25Index()
            self._bm25.build(chunks)
            for cid in self._bm25.chunk_ids:
                self._chunk_catalog[cid] = self._bm25.chunk_meta(cid)
        return self._bm25

    def _dense_recall(
        self,
        query_vec: list[float],
        recall_k: int,
        paper_ids: list[int] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, float]]:
        try:
            hits = query_workspace(self.ws_id, query_vec, top_k=recall_k, paper_ids=paper_ids)
            hits = [_attach_chunk_id(h) for h in hits]
            scores = {h["chunk_id"]: float(h.get("score", 0.0)) for h in hits}
            for h in hits:
                self._chunk_catalog[h["chunk_id"]] = h
            return hits, scores
        except Exception as exc:
            logger.warning(f"dense recall failed: {exc}")
            return [], {}

    def _bm25_recall(
        self,
        question: str,
        recall_k: int,
        paper_ids: list[int] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, float]]:
        try:
            index = self._ensure_bm25(paper_ids)
            hits = index.query(question, top_k=recall_k, paper_ids=paper_ids)
            scores = {h["chunk_id"]: float(h.get("score", 0.0)) for h in hits}
            for h in hits:
                self._chunk_catalog[h["chunk_id"]] = h
            return hits, scores
        except Exception as exc:
            logger.warning(f"bm25 recall failed: {exc}")
            return [], {}

    def _graph_recall(
        self,
        question: str,
        paper_ids: list[int] | None,
        graph: dict[str, Any] | None,
        score_fn: Callable[..., dict[int, float]] | None,
        seed_ids: list[int] | None = None,
        graph_config: dict[str, Any] | None = None,
        chunk_scores: dict[str, float] | None = None,
    ) -> tuple[list[str], dict[int, float]]:
        paper_scores = get_graph_paper_scores(
            graph,
            question,
            paper_ids=paper_ids,
            seed_ids=seed_ids,
            score_fn=score_fn,
            graph_config=graph_config,
        )
        if not paper_scores:
            return [], {}

        if paper_ids is not None:
            allowed = set(paper_ids)
            paper_scores = {p: s for p, s in paper_scores.items() if p in allowed}

        chunks_by_paper: dict[int, list[str]] = {}
        for cid, meta in self._chunk_catalog.items():
            pid = meta.get("paper_id")
            if pid is not None:
                chunks_by_paper.setdefault(int(pid), []).append(cid)

        if not chunks_by_paper:
            chunks = list_workspace_chunks(self.ws_id, paper_ids=paper_ids)
            for ch in chunks:
                cid = str(ch["id"])
                self._chunk_catalog[cid] = {
                    **{k: v for k, v in ch.items() if k != "text"},
                    "chunk_id": cid,
                    "snippet": ch.get("text", ""),
                }
                pid = ch.get("paper_id")
                if pid is not None:
                    chunks_by_paper.setdefault(int(pid), []).append(cid)

        per_paper_cap = DEFAULT_GRAPH_POOL_PER_PAPER
        if graph_config and "per_paper_chunks" in graph_config:
            try:
                per_paper_cap = int(graph_config["per_paper_chunks"])
            except (TypeError, ValueError):
                pass

        pool = _graph_chunk_pool(
            paper_scores,
            chunks_by_paper,
            per_paper_cap=per_paper_cap,
            chunk_scores=chunk_scores,
        )
        return pool, paper_scores

    async def retrieve(
        self,
        question: str,
        query_vec: list[float],
        *,
        top_k: int = 5,
        recall_k: int = 30,
        paper_ids: list[int] | None = None,
        mode: str = "vector",
        use_rerank: bool = True,
        graph: dict[str, Any] | None = None,
        graph_score_fn: Callable[..., dict[int, float]] | None = None,
        rrf_k: int = DEFAULT_RRF_K,
        weights: dict[str, float] | None = None,
        graph_config: dict[str, Any] | None = None,
    ) -> HybridRetrieveResult:
        dense_hits, dense_scores = self._dense_recall(query_vec, recall_k, paper_ids)
        bm25_hits: list[dict[str, Any]] = []
        bm25_scores: dict[str, float] = {}
        if mode in {"hybrid", "graph"}:
            bm25_hits, bm25_scores = self._bm25_recall(question, recall_k, paper_ids)

        pools: dict[str, list[str]] = {}
        if dense_hits:
            pools[CHANNEL_DENSE] = [h["chunk_id"] for h in dense_hits]
        if bm25_hits:
            pools[CHANNEL_BM25] = [h["chunk_id"] for h in bm25_hits]

        graph_paper_scores: dict[int, float] = {}
        graph_chunk_scores: dict[str, float] = {}
        if mode == "graph":
            # 用 dense Top-N 作为 PPR personalization；dense 失败时回退 BM25 Top-N。
            query_seeds: list[int] = []
            seen_seeds: set[int] = set()
            for h in (dense_hits or bm25_hits):
                pid = h.get("paper_id")
                if pid is None:
                    continue
                pid_i = int(pid)
                if pid_i not in seen_seeds:
                    seen_seeds.add(pid_i)
                    query_seeds.append(pid_i)
            graph_pool, graph_paper_scores = self._graph_recall(
                question,
                paper_ids,
                graph,
                graph_score_fn,
                seed_ids=query_seeds or None,
                graph_config=graph_config,
                chunk_scores={**bm25_scores, **dense_scores},
            )
            if graph_pool:
                pools[CHANNEL_GRAPH] = graph_pool
                for cid in graph_pool:
                    meta = self._chunk_catalog.get(cid, {})
                    pid = meta.get("paper_id")
                    if pid is not None and int(pid) in graph_paper_scores:
                        graph_chunk_scores[cid] = graph_paper_scores[int(pid)]

        if not pools:
            return HybridRetrieveResult(hits=[], debug=[])

        fused = fuse_ranked_pools(pools, k=rrf_k, weights=weights)
        candidates: list[dict[str, Any]] = []
        fused_debug: list[dict[str, Any]] = []

        for cid, fused_score, ranks in fused[:recall_k]:
            base = dict(self._chunk_catalog.get(cid, {}))
            if not base:
                continue
            source_hits: dict[str, float] = {}
            if cid in dense_scores:
                source_hits[CHANNEL_DENSE] = dense_scores[cid]
            if cid in bm25_scores:
                source_hits[CHANNEL_BM25] = bm25_scores[cid]
            graph_score = graph_chunk_scores.get(cid)
            if graph_score is not None:
                source_hits[CHANNEL_GRAPH] = graph_score

            base["score"] = fused_score
            candidates.append(base)
            fused_debug.append({
                "chunk_id": cid,
                "source_hits": source_hits,
                "fused_score": fused_score,
                "channel_ranks": ranks,
                "graph_score": graph_score,
                "rerank_score": None,
            })

        if not candidates:
            return HybridRetrieveResult(hits=[], debug=[])

        if use_rerank:
            ranked = await rerank(question, [c["snippet"] for c in candidates], top_n=top_k)
            hits: list[dict[str, Any]] = []
            debug_out: list[dict[str, Any]] = []
            for r in ranked:
                base = dict(candidates[r["index"]])
                base["score"] = r["score"]
                dbg = dict(fused_debug[r["index"]])
                dbg["rerank_score"] = r["score"]
                hits.append(base)
                debug_out.append(dbg)
            return HybridRetrieveResult(hits=hits, debug=debug_out)

        hits = candidates[:top_k]
        debug_out = fused_debug[:top_k]
        for h, d in zip(hits, debug_out):
            d["rerank_score"] = h.get("score")
        return HybridRetrieveResult(hits=hits, debug=debug_out)
