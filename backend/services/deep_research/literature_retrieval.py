"""BM25+ and dense retrieval with RRF (Cormack et al., SIGIR 2009).

Scores rank available evidence; they are not calibrated relevance probabilities.
Embeddings reuse the configured pretrained model, without changing existing indices.
"""

from __future__ import annotations

import asyncio
import hashlib
from threading import RLock
from collections import OrderedDict

import numpy as np
from rank_bm25 import BM25Plus
from scipy.stats import rankdata
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from ..ai_config import get_embedding_config, get_reranker_config
from .embedder import embed_query, embed_texts
from .graph_analytics import _tokenize
from .reranker import rerank

RRF_K = 60
_vectors: OrderedDict[tuple, list[float]] = OrderedDict()
_vectors_lock = RLock()


def paper_text(paper: dict) -> str:
    return f"{paper.get('title') or ''}\n{paper.get('abstract') or ''}".strip()


def lexical_scores(texts: list[str], question: str) -> np.ndarray:
    tokens = [_tokenize(text) for text in texts]
    query = _tokenize(question)
    if not tokens or not any(tokens) or not query:
        return np.zeros(len(texts))
    scores = BM25Plus(tokens).get_scores(query)
    # BM25+ adds a collection-wide delta; unmatched documents are not evidence.
    matched = np.array([bool(set(query).intersection(t)) for t in tokens])
    return np.where(matched, scores, 0.0)


def lexical_order(papers: list[dict], question: str) -> list[dict]:
    scores = lexical_scores([paper_text(p) for p in papers], question)
    return [papers[i] for i in sorted(range(len(papers)), key=lambda i: (-scores[i], papers[i]["key"]))]


def _validated_vectors(values, count: int) -> np.ndarray:
    matrix = np.asarray(values, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] != count or matrix.shape[1] == 0:
        raise ValueError("embedding shape mismatch")
    if not np.isfinite(matrix).all() or np.any(np.linalg.norm(matrix, axis=1) <= 0):
        raise ValueError("invalid or missing embedding")
    return normalize(matrix)


async def document_vectors(texts: list[str]) -> np.ndarray:
    cfg = get_embedding_config()
    identity = (cfg.get("provider"), cfg.get("base_url"), cfg.get("model"))
    keys = [identity + (hashlib.sha256(t.encode()).hexdigest(),) for t in texts]
    with _vectors_lock:
        local = {k: _vectors[k] for k in keys if k in _vectors}
    missing = [i for i, k in enumerate(keys) if k not in local]
    batch = max(1, min(32, int(cfg.get("batch_size") or 32)))
    for start in range(0, len(missing), batch):
        indices = missing[start:start + batch]
        embedded = _validated_vectors(await embed_texts([texts[i] or " " for i in indices]), len(indices))
        for i, vector in zip(indices, embedded):
            local[keys[i]] = vector.tolist()
    result = _validated_vectors([local[k] for k in keys], len(keys))
    with _vectors_lock:
        for key in keys:
            _vectors[key] = local[key]
            _vectors.move_to_end(key)
        while len(_vectors) > 4096:
            _vectors.popitem(last=False)
    return result


async def paper_similarity(papers: list[dict]) -> tuple[np.ndarray, dict]:
    texts = [paper_text(p) for p in papers]
    cfg = get_embedding_config()
    meta = {"representation": "pretrained_embedding", "model": cfg.get("model"), "warnings": []}
    if not papers:
        return np.zeros((0, 0)), meta
    try:
        matrix = await asyncio.wait_for(document_vectors(texts), timeout=60)
        sims = matrix @ matrix.T
    except Exception as exc:
        meta.update(representation="sklearn_tfidf", model=None)
        meta["warnings"].append(f"语义表示不可用（{type(exc).__name__}），使用标准 TF-IDF")
        try:
            matrix = TfidfVectorizer(tokenizer=_tokenize, token_pattern=None, lowercase=False).fit_transform(texts)
            sims = (matrix @ matrix.T).toarray()
        except ValueError:
            sims = np.zeros((len(papers), len(papers)))
    sims = np.clip(sims, 0.0, 1.0)
    np.fill_diagonal(sims, 0.0)
    return sims, meta


async def retrieve_materials(
    papers: list[dict], question: str, selected: list[str] | None = None, *, limit: int = 32,
) -> tuple[list[dict], dict]:
    papers = list({p["key"]: p for p in papers if p.get("key")}.values())
    meta = {"method": "bm25plus_dense_rrf", "rrf_k": RRF_K, "channels": ["bm25plus"], "warnings": []}
    if not papers or limit <= 0:
        return [], meta
    texts = [paper_text(p) for p in papers]
    lexical = lexical_scores(texts, question)
    scores = np.zeros(len(papers))
    positive = lexical > 0
    scores[positive] += 1.0 / (RRF_K + rankdata(-lexical[positive], method="average"))
    try:
        async def semantic():
            docs = await document_vectors(texts)
            query = _validated_vectors([await embed_query(question)], 1)[0]
            if docs.shape[1] != query.shape[0]:
                raise ValueError("query embedding dimension mismatch")
            return docs @ query
        dense = await asyncio.wait_for(semantic(), timeout=45)
        positive = dense > 0
        scores[positive] += 1.0 / (RRF_K + rankdata(-dense[positive], method="average"))
        meta["channels"].append("dense")
        meta["embedding_model"] = get_embedding_config().get("model")
    except Exception as exc:
        meta["warnings"].append(f"向量检索不可用（{type(exc).__name__}），保留 BM25 排序")

    order = sorted(range(len(papers)), key=lambda i: (-scores[i], papers[i]["key"]))
    cfg = get_reranker_config()
    # Cover small exploration corpora completely, including cross-language BM25 misses.
    pool = order[:max(limit, min(240, len(order)))]
    if cfg.get("enabled", True):
        try:
            ranked = await asyncio.wait_for(rerank(question, [texts[i] for i in pool], top_n=len(pool)), timeout=35)
            usable = [r for r in ranked if isinstance(r.get("index"), int)
                      and 0 <= r["index"] < len(pool) and not r.get("fallback")]
            if usable:
                preferred = list(dict.fromkeys(pool[r["index"]] for r in usable))
                order = preferred + [i for i in order if i not in set(preferred)]
                meta["channels"].append("reranker")
                meta["reranker_model"] = cfg.get("model")
            else:
                meta["warnings"].append("重排服务不可用，保留检索排序")
        except Exception as exc:
            meta["warnings"].append(f"重排不可用（{type(exc).__name__}），保留检索排序")
    by_key = {p["key"]: p for p in papers}
    pins = list(dict.fromkeys(k for k in (selected or []) if k in by_key))[:limit]
    chosen = pins + [papers[i]["key"] for i in order if papers[i]["key"] not in pins]
    meta["selected_keys"] = pins
    meta["candidate_count"] = len(papers)
    return [by_key[k] for k in chosen[:limit]], meta
