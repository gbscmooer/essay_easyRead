"""
Unsupervised paper relatedness using Reciprocal Rank Fusion (RRF).

Cormack, Clarke & Buettcher (SIGIR 2009), doi:10.1145/1571941.1572114.
Project adaptation: rank positive finite neighbors separately at each endpoint,
then average their reciprocal ranks to obtain an undirected channel score.
Ties use average rank; missing/nonpositive evidence casts no vote.

Multiplication by k+1 (k=60) and globally normalized priors keep scores in [0,1]
for graph consumers. This is rank fusion, not a probability, semantic proof, or
calibrated similarity. Missing channels are not redistributed per pair.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import numpy as np
from scipy.stats import rankdata

from .calibration import CalibrationReport
from .features import PaperFeatures
from .lexical import build_idf, extract_terms, idf_cosine, normalize_entity
from .vectors import build_topic_vectors

logger = logging.getLogger(__name__)

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]
SEMANTIC = ("topic", "purpose", "method", "term")
EVIDENCE = ("dataset", "baseline", "bibcoupling", "cocitation")
RRF_K = 60
DEFAULT_WEIGHTS: dict[str, float] = dict.fromkeys(SEMANTIC + EVIDENCE, 1 / 8)

CHANNEL_LABELS: dict[str, str] = {
    "topic": "主题", "purpose": "研究目的", "method": "方法", "term": "术语",
    "dataset": "数据集", "baseline": "对比基线", "bibcoupling": "文献耦合",
    "cocitation": "共引",
}
CHANNEL_HINTS: dict[str, str] = {
    "topic": "全文/题录主题向量的余弦。",
    "purpose": "研究目的文本（问题 + 一句话 + 核心贡献）的语义相似度。",
    "method": "方法文本（overview + pipeline）的语义相似度。",
    "term": "IDF 加权术语重叠：共享的稀有专名越多越高。",
    "dataset": "共享实验数据集（冷门数据集权重更高）。",
    "baseline": "共享对比基线。",
    "bibcoupling": "引用了相同的参考文献（Salton 归一）。",
    "cocitation": "被相同论文共同引用（Salton 归一）。",
}


def normalize_weights(weights: dict[str, float] | None) -> dict[str, float]:
    """Merge overrides with equal defaults; normalize all channels together."""
    merged = {}
    for name, default in DEFAULT_WEIGHTS.items():
        value = float((weights or {}).get(name, default))
        if not np.isfinite(value):
            raise ValueError(f"Non-finite relatedness weight: {name}")
        merged[name] = max(0.0, value)
    peak = max(merged.values())
    if peak == 0:
        return dict(DEFAULT_WEIGHTS)
    # Scale first to avoid overflow for large, finite custom weights.
    merged = {name: value / peak for name, value in merged.items()}
    total = sum(merged.values())
    return {name: value / total for name, value in merged.items()}


def _reciprocal(rank: float) -> float:
    return (RRF_K + 1) / (RRF_K + rank) if rank > 0 else 0.0


@dataclass
class _DenseChannel:
    name: str
    index: dict[int, int]
    matrix: np.ndarray
    defined: np.ndarray
    shared: dict[tuple[int, int], list[str]] = field(default_factory=dict)
    ranks: np.ndarray | None = field(default=None, repr=False)

    def has(self, a: int, b: int) -> bool:
        ia, ib = self.index.get(a), self.index.get(b)
        return (
            a != b and ia is not None and ib is not None
            and bool(self.defined[ia]) and bool(self.defined[ib])
        )

    def raw(self, a: int, b: int) -> float:
        value = float(self.matrix[self.index[a], self.index[b]])
        return value if np.isfinite(value) else 0.0

    def rank_pair(self, a: int, b: int) -> tuple[float, float]:
        if not self.has(a, b) or self.ranks is None:
            return 0.0, 0.0
        i, j = self.index[a], self.index[b]
        return float(self.ranks[i, j]), float(self.ranks[j, i])

    def norm(self, a: int, b: int) -> float:
        ra, rb = self.rank_pair(a, b)
        return (_reciprocal(ra) + _reciprocal(rb)) / 2

    def prepare_ranks(self) -> None:
        self.ranks = np.zeros_like(self.matrix, dtype=float)
        for i in np.flatnonzero(self.defined):
            valid = self.defined & np.isfinite(self.matrix[i]) & (self.matrix[i] > 0)
            valid[i] = False
            self.ranks[i, valid] = rankdata(-self.matrix[i, valid], method="average")


@dataclass
class _SparseChannel:
    name: str
    values: dict[tuple[int, int], float]
    shared: dict[tuple[int, int], list[str]] = field(default_factory=dict)
    counts: dict[tuple[int, int], int] = field(default_factory=dict)
    ranks: dict[tuple[int, int], float] = field(default_factory=dict, repr=False)

    def raw(self, a: int, b: int) -> float:
        return self.values.get((min(a, b), max(a, b)), 0.0)

    def rank_pair(self, a: int, b: int) -> tuple[float, float]:
        return self.ranks.get((a, b), 0.0), self.ranks.get((b, a), 0.0)

    def norm(self, a: int, b: int) -> float:
        ra, rb = self.rank_pair(a, b)
        return (_reciprocal(ra) + _reciprocal(rb)) / 2

    def prepare_ranks(self, ids: set[int]) -> None:
        self.values = {
            (a, b): v for (a, b), v in self.values.items()
            if a in ids and b in ids and a != b and np.isfinite(v) and v > 0
        }
        neighbors: dict[int, list[tuple[int, float]]] = {}
        for (a, b), value in self.values.items():
            neighbors.setdefault(a, []).append((b, value))
            neighbors.setdefault(b, []).append((a, value))
        self.ranks = {}
        for a, entries in neighbors.items():
            ranks = rankdata([-value for _, value in entries], method="average")
            self.ranks.update({(a, b): float(r) for (b, _), r in zip(entries, ranks)})


@dataclass
class RelationResult:
    pairs: list[tuple[int, int, float]]
    weights: dict[str, float]
    coverage: dict[str, int]
    calibration: CalibrationReport | None = None
    _dense: dict[str, _DenseChannel] = field(default_factory=dict, repr=False)
    _sparse: dict[str, _SparseChannel] = field(default_factory=dict, repr=False)

    def score(self, a: int, b: int) -> float:
        return self._fuse(a, b)[0]

    def explain(self, a: int, b: int) -> dict:
        score, components = self._fuse(a, b, with_components=True)
        return {
            "weight": score, "components": components,
            "method": "weighted_rrf_v1", "rrf_k": RRF_K,
            "symmetrization": "mean_endpoint_reciprocal_ranks",
            "score_semantics": "normalized_rank_fusion",
        }

    def _fuse(self, a: int, b: int, *, with_components: bool = False):
        components: list[dict] = []
        score = 0.0
        key = (min(a, b), max(a, b))
        for name in SEMANTIC + EVIDENCE:
            channel = self._dense.get(name) or self._sparse.get(name)
            if channel is None:
                continue
            available = channel.has(a, b) if name in self._dense else (a != b and channel.raw(a, b) > 0)
            raw = channel.raw(a, b) if available else 0.0
            sim = channel.norm(a, b) if available else 0.0
            ranks = channel.rank_pair(a, b) if available else (0.0, 0.0)
            shared = channel.shared.get(key, []) if available else []
            count = channel.counts.get(key, 0) if name in self._sparse and available else 0
            weight = self.weights[name] if available else 0.0
            contribution = weight * sim
            score += contribution
            if with_components:
                components.append({
                    "facet": name, "label": CHANNEL_LABELS[name], "sim": sim,
                    "raw": raw, "weight": weight, "prior_weight": self.weights[name],
                    "contribution": contribution,
                    "rank_source": ranks[0] or None, "rank_target": ranks[1] or None,
                    "reason": _reason(name, sim, raw, shared, count, available),
                    "shared": shared,
                })
        return (score, components) if with_components else (score, None)


def _reason(name: str, sim: float, raw: float, shared: list[str], count: int, available: bool) -> str:
    label = CHANNEL_LABELS[name]
    if name in SEMANTIC:
        if not available:
            return f"{label}：缺少可比文本"
        if name == "term":
            return ("共享术语: " + "、".join(shared)) if shared else "无共享术语"
        return f"{label}余弦 {raw:.2f}；双端排名贡献 {sim:.3f}"
    if name == "bibcoupling":
        return f"共享 {count} 篇参考文献" if count else "无共享参考文献"
    if name == "cocitation":
        return f"被 {count} 篇论文共同引用" if count else "无共引"
    return ("共享: " + "、".join(shared)) if shared else f"无共享{label}"


async def compute_relations(
    papers: list[PaperFeatures],
    *,
    embed_fn,
    weights: dict[str, float] | None = None,
    bibcoupling: dict[tuple[int, int], tuple[float, int]] | None = None,
    cocitation: dict[tuple[int, int], tuple[float, int]] | None = None,
    calibrate: bool = False,
) -> RelationResult:
    """Fuse channel ranks without labels; custom priors are never fitted.

    Structural inputs map unordered paper pairs to (Salton score, shared count).
    The legacy calibrate argument is accepted but has no effect. The report
    remains available for older clients and explicitly reports no calibration.
    """
    w = normalize_weights(weights)
    report = CalibrationReport(False, "对称 RRF（k=60），不使用弱标签校准", dict(w))
    seen: set[int] = set()
    papers = [p for p in papers if not (p.id in seen or seen.add(p.id))]
    papers.sort(key=lambda p: p.id)
    ids = [p.id for p in papers]
    index = {pid: i for i, pid in enumerate(ids)}
    if len(ids) < 2:
        return RelationResult(pairs=[], weights=w, coverage={}, calibration=report)

    dense = {"topic": _dense_from_vectors(
        "topic", index, await build_topic_vectors(papers, embed_fn=embed_fn),
    )}
    for name, getter in (("purpose", lambda p: p.purpose_text), ("method", lambda p: p.method_text)):
        dense[name] = _dense_from_vectors(name, index, await _embed_field(papers, getter, embed_fn=embed_fn))
    dense["term"] = _term_channel(papers, index)
    for channel in dense.values():
        channel.prepare_ranks()

    sparse = {
        "dataset": _entity_channel("dataset", papers, lambda p: p.datasets),
        "baseline": _entity_channel("baseline", papers, lambda p: p.baselines),
        "bibcoupling": _structural_channel("bibcoupling", bibcoupling),
        "cocitation": _structural_channel("cocitation", cocitation),
    }
    for channel in sparse.values():
        channel.prepare_ranks(set(ids))

    result = RelationResult(
        pairs=[], weights=w, calibration=report,
        coverage={
            **{name: int(ch.defined.sum()) for name, ch in dense.items()},
            **{name: len(ch.values) for name, ch in sparse.items()},
        },
        _dense=dense, _sparse=sparse,
    )
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            score = result.score(a, b)
            if score > 0:
                result.pairs.append((a, b, score))
    logger.info("relatedness RRF: %d papers, %d pairs; coverage %s", len(ids), len(result.pairs), result.coverage)
    return result


# ---------------------------------------------------------------------------
# 通道构造
# ---------------------------------------------------------------------------
def _dense_from_vectors(
    name: str, index: dict[int, int], vectors: dict[int, np.ndarray]
) -> _DenseChannel:
    n = len(index)
    mat = np.zeros((n, n), dtype=float)
    defined = np.zeros(n, dtype=bool)
    if vectors:
        dims = {np.asarray(v).shape[0] for v in vectors.values()}
        if len(dims) > 1:  # 维度不一致（模型混用）→ 整个通道作废，宁可缺失不要垃圾余弦
            logger.warning("relatedness[%s]: 向量维度不一致 %s，跳过该通道", name, dims)
            return _DenseChannel(name=name, index=index, matrix=mat, defined=defined)
        dim = dims.pop()
        stack = np.zeros((n, dim), dtype=float)
        for pid, vec in vectors.items():
            i = index.get(pid)
            if i is None:
                continue
            v = np.asarray(vec, dtype=float)
            norm = float(np.linalg.norm(v))
            if not np.isfinite(norm) or norm <= 0:
                continue
            stack[i] = v / norm
            defined[i] = True
        mat = np.clip(stack @ stack.T, 0.0, 1.0)
    return _DenseChannel(name=name, index=index, matrix=mat, defined=defined)


async def _embed_field(papers: list[PaperFeatures], getter, *, embed_fn) -> dict[int, np.ndarray]:
    usable = [p for p in papers if (getter(p) or "").strip()]
    if len(usable) < 2:
        return {}
    try:
        vecs = await embed_fn([getter(p) for p in usable])
    except Exception as ex:
        logger.warning("relatedness: embed 字段失败，跳过该通道: %s", ex)
        return {}
    return {p.id: np.asarray(v, dtype=float) for p, v in zip(usable, vecs)}


def _term_channel(papers: list[PaperFeatures], index: dict[int, int]) -> _DenseChannel:
    doc_terms: dict[int, set[str]] = {}
    for p in papers:
        terms = extract_terms(
            p.title, p.purpose_text, p.method_text, p.abstract,
            " ".join(p.datasets), " ".join(p.baselines),
        )
        if terms:
            doc_terms[p.id] = terms
    n = len(index)
    mat = np.zeros((n, n), dtype=float)
    defined = np.zeros(n, dtype=bool)
    shared_map: dict[tuple[int, int], list[str]] = {}
    if len(doc_terms) < 2:
        return _DenseChannel(name="term", index=index, matrix=mat, defined=defined)

    idf = build_idf(doc_terms)
    ids = [pid for pid in index if pid in doc_terms]
    for pid in ids:
        defined[index[pid]] = True
    for x in range(len(ids)):
        for y in range(x + 1, len(ids)):
            a, b = ids[x], ids[y]
            score, shared = idf_cosine(doc_terms[a], doc_terms[b], idf)
            if score <= 0:
                continue
            ia, ib = index[a], index[b]
            mat[ia, ib] = mat[ib, ia] = score
            shared_map[(min(a, b), max(a, b))] = shared
    return _DenseChannel(
        name="term", index=index, matrix=mat, defined=defined, shared=shared_map,
    )


def _entity_channel(name: str, papers: list[PaperFeatures], getter) -> _SparseChannel:
    """共享实体（数据集 / baseline）：IDF 加权重叠，冷门实体权重更高。"""
    sets: dict[int, dict[str, str]] = {}
    for p in papers:
        items = {normalize_entity(x): str(x).strip() for x in (getter(p) or []) if str(x).strip()}
        items.pop("", None)
        if items:
            sets[p.id] = items
    values: dict[tuple[int, int], float] = {}
    shared_map: dict[tuple[int, int], list[str]] = {}
    counts: dict[tuple[int, int], int] = {}
    if len(sets) >= 2:
        idf = build_idf({pid: set(items) for pid, items in sets.items()})
        ids = sorted(sets)
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                a, b = ids[x], ids[y]
                score, shared = idf_cosine(set(sets[a]), set(sets[b]), idf, top_shared=8)
                if score <= 0:
                    continue
                key = (min(a, b), max(a, b))
                values[key] = score
                shared_map[key] = [sets[a].get(s, s) for s in shared]
                counts[key] = len(shared)
    return _SparseChannel(name=name, values=values, shared=shared_map, counts=counts)


def _structural_channel(
    name: str, data: dict[tuple[int, int], tuple[float, int]] | None
) -> _SparseChannel:
    values: dict[tuple[int, int], float] = {}
    counts: dict[tuple[int, int], int] = {}
    for (a, b), (weight, shared) in (data or {}).items():
        key = (min(a, b), max(a, b))
        values[key] = float(weight)
        counts[key] = int(shared)
    return _SparseChannel(name=name, values=values, counts=counts)


def channel_catalog() -> list[dict]:
    """通道元信息（供设置页渲染权重编辑器）。"""
    return [
        {
            "name": name,
            "label": CHANNEL_LABELS[name],
            "group": "semantic" if name in SEMANTIC else "evidence",
            "default_weight": DEFAULT_WEIGHTS[name],
            "hint": CHANNEL_HINTS[name],
        }
        for name in SEMANTIC + EVIDENCE
    ]
