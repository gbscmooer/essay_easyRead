"""
relatedness — 论文关联度：全系统唯一的一套打分算法（纯函数，无 DB）

设计动机（这是 v3 重写的核心）
--------------------------------------------------
旧实现把图谱拓扑完全压在**一个数**上：全文 chunk 质心的余弦。全文质心是"大领域
表示"——一篇 CV 论文八成的篇幅在写骨干网络、训练配置、消融实验，这些内容整个领域
共享。于是余弦只能分出"大方向"，分不出细分方向（合成对照实验里稳定表现为：真值
4 个细分方向，聚类只能还原 2 个大方向，骨架里仅 46% 的边连接同一细分方向）。

更糟的是，能区分细分方向的信号**当时都算了但没用**：facet 分解只挂在 tooltip 上，
文献耦合 / 共引被建成独立边类型后，在社区检测里被 max 折叠掉（相似边 0.8+ 恒定
压过 Salton 0.1~0.3）。

本模块把这些信号合成**一个可解释的关联度**，让它同时决定：连哪条边、边多重、
社区怎么分、PageRank 谁重要、图检索推谁。

八个通道
--------------------------------------------------
语义通道（有文本就能算，按可用者归一化，占比 SEM_SHARE）：
  topic    全文/题录主题向量余弦 —— 粗粒度锚点，防止细分信号噪声导致离题
  purpose  研究目的文本向量余弦 —— 细分方向的主信号
  method   方法文本向量余弦
  term     IDF 加权术语重叠 —— 稀有专名（MVTec-AD / DINOv2 / few-shot）是细分方向的指纹
证据通道（有数据才加分，缺失记 0，不参与归一化）：
  dataset      共享数据集（IDF 加权：共享冷门数据集比共享 ImageNet 有信息量得多）
  baseline     共享对比基线
  bibcoupling  文献耦合 Salton —— 引同一批文献 = 站在同一个小方向里
  cocitation   共引 Salton

尺度归一（关键）
--------------------------------------------------
不同通道的原始分尺度差一个数量级（BGE 余弦挤在 0.76~0.95，Salton 常在 0.05~0.3）。
直接加权求和等于让余弦通道独吞，这正是旧实现 max 折叠的翻版。因此每个通道先做
**分位数缩放**（语义通道 q10→q99 拉满，证据通道 0→q90 拉满，都截断到 [0,1]），
之后权重才真正有意义。

权重自适应（v3.1）
--------------------------------------------------
DEFAULT_WEIGHTS 只是**先验**。哪个通道真正管用是随语料变的：ws1（179 篇真实 CV 论文）
上 topic 的判别力 AUC 0.888、method 只有 0.573（≈随机）；而在"一个大方向下含多个细分
方向"的语料上恰好相反。因此每次建图会用工作区自己的结构证据（谁引用谁 / 谁与谁共享
参考文献）作弱监督，按判别力重新分配语义通道的权重，见 `calibration.py`。证据不足时
原样使用配置权重。

输出
--------------------------------------------------
`compute_relations()` 返回 `RelationResult`：
- `pairs`: [(a, b, score)] 融合分，供骨架提取；
- `explain(a, b)`: 该对的可解释分解 {weight, components:[...]}，各分量 contribution
  之和恒等于 score（前端进度条据此渲染，不会对不上）。
- `calibration`: 本次权重校准报告（判别力 / 样本量 / 收缩系数），供设置页解释。
分解按需生成，不为 O(n²) 对预先物化 dict，大工作区不炸内存。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import numpy as np

from .calibration import CalibrationReport, build_supervision, calibrate_weights
from .features import PaperFeatures
from .lexical import build_idf, extract_terms, idf_cosine, normalize_entity
from .vectors import build_topic_vectors

logger = logging.getLogger(__name__)

# 注入式向量化：list[str] -> list[list[float]]（与生产 embed_texts 同签名）
EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]

SEMANTIC = ("topic", "purpose", "method", "term")
EVIDENCE = ("dataset", "baseline", "bibcoupling", "cocitation")

# 默认权重：刻意把重心从"全文质心"移到细分信号上（purpose+method+term=0.54 vs topic=0.18）。
# 语义通道合计 SEM_SHARE，证据通道合计 1-SEM_SHARE。
DEFAULT_WEIGHTS: dict[str, float] = {
    "topic": 0.18,
    "purpose": 0.24,
    "method": 0.16,
    "term": 0.14,
    "dataset": 0.06,
    "baseline": 0.04,
    "bibcoupling": 0.12,
    "cocitation": 0.06,
}

CHANNEL_LABELS: dict[str, str] = {
    "topic": "主题",
    "purpose": "研究目的",
    "method": "方法",
    "term": "术语",
    "dataset": "数据集",
    "baseline": "对比基线",
    "bibcoupling": "文献耦合",
    "cocitation": "共引",
}

CHANNEL_HINTS: dict[str, str] = {
    "topic": "全文/题录主题向量的余弦，粗粒度锚点。",
    "purpose": "研究目的文本（问题 + 一句话 + 核心贡献）的语义相似度。",
    "method": "方法文本（overview + pipeline）的语义相似度。",
    "term": "IDF 加权术语重叠：共享的稀有专名越多越高。",
    "dataset": "共享实验数据集（冷门数据集权重更高）。",
    "baseline": "共享对比基线。",
    "bibcoupling": "引用了相同的参考文献（Salton 归一）。",
    "cocitation": "被相同论文共同引用（Salton 归一）。",
}


def normalize_weights(weights: dict[str, float] | None) -> dict[str, float]:
    """合并默认权重并按语义 / 证据两组各自归一，返回每通道的绝对权重（总和 1）。"""
    merged = {k: max(0.0, float((weights or {}).get(k, v))) for k, v in DEFAULT_WEIGHTS.items()}
    sem_sum = sum(merged[c] for c in SEMANTIC)
    evi_sum = sum(merged[c] for c in EVIDENCE)
    if sem_sum <= 0 and evi_sum <= 0:                     # 全零配置 → 回默认
        return normalize_weights(None)
    # 语义组整体占比固定为默认值，避免调单个通道权重时两组比例意外漂移；
    # 某一组被整组关掉时，另一组独占全部权重。
    sem_share = sum(DEFAULT_WEIGHTS[c] for c in SEMANTIC)
    if evi_sum <= 0:
        sem_share = 1.0
    elif sem_sum <= 0:
        sem_share = 0.0
    out = {c: (merged[c] / sem_sum * sem_share if sem_sum > 0 else 0.0) for c in SEMANTIC}
    out.update({c: (merged[c] / evi_sum * (1.0 - sem_share) if evi_sum > 0 else 0.0) for c in EVIDENCE})
    return out


# ---------------------------------------------------------------------------
# 分位数缩放
# ---------------------------------------------------------------------------
def _scale_bounds(values: np.ndarray, *, lo_q: float, hi_q: float) -> tuple[float, float]:
    if values.size == 0:
        return 0.0, 1.0
    if values.size < 8:  # 样本太少，分位数不稳 → 退回 min/max
        lo, hi = float(values.min()), float(values.max())
    else:
        lo = float(np.quantile(values, lo_q))
        hi = float(np.quantile(values, hi_q))
    if hi - lo < 1e-9:
        return lo, lo + 1.0
    return lo, hi


def _rescale(x: float, lo: float, hi: float) -> float:
    return float(min(1.0, max(0.0, (x - lo) / (hi - lo))))


# ---------------------------------------------------------------------------
# 通道容器
# ---------------------------------------------------------------------------
@dataclass
class _DenseChannel:
    """矩阵型通道（语义向量余弦 / 术语重叠）。"""

    name: str
    index: dict[int, int]
    matrix: np.ndarray                    # (n, n) 原始分
    defined: np.ndarray                   # (n,) bool，是否有该通道的数据
    lo: float = 0.0
    hi: float = 1.0
    shared: dict[tuple[int, int], list[str]] = field(default_factory=dict)

    def has(self, a: int, b: int) -> bool:
        ia, ib = self.index.get(a), self.index.get(b)
        return ia is not None and ib is not None and bool(self.defined[ia]) and bool(self.defined[ib])

    def raw(self, a: int, b: int) -> float:
        ia, ib = self.index[a], self.index[b]
        return float(self.matrix[ia, ib])

    def norm(self, a: int, b: int) -> float:
        return _rescale(self.raw(a, b), self.lo, self.hi)

    def calibrate(self) -> None:
        idx = np.flatnonzero(self.defined)
        if idx.size < 2:
            self.lo, self.hi = 0.0, 1.0
            return
        sub = self.matrix[np.ix_(idx, idx)]
        iu = np.triu_indices(idx.size, k=1)
        self.lo, self.hi = _scale_bounds(sub[iu], lo_q=0.10, hi_q=0.99)


@dataclass
class _SparseChannel:
    """稀疏型通道（共享数据集 / baseline / 文献耦合 / 共引）。"""

    name: str
    values: dict[tuple[int, int], float]
    shared: dict[tuple[int, int], list[str]] = field(default_factory=dict)
    counts: dict[tuple[int, int], int] = field(default_factory=dict)
    hi: float = 1.0

    def raw(self, a: int, b: int) -> float:
        return self.values.get((min(a, b), max(a, b)), 0.0)

    def norm(self, a: int, b: int) -> float:
        return _rescale(self.raw(a, b), 0.0, self.hi)

    def calibrate(self) -> None:
        vals = np.array([v for v in self.values.values() if v > 0], dtype=float)
        if vals.size == 0:
            self.hi = 1.0
            return
        _, self.hi = _scale_bounds(vals, lo_q=0.0, hi_q=0.90)
        self.hi = max(self.hi, 1e-6)


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
        """{weight, components:[{facet,label,sim,raw,weight,contribution,reason,shared}]}。"""
        score, components = self._fuse(a, b, with_components=True)
        return {"weight": round(score, 4), "components": components}

    # -- 融合：语义通道按可用者归一，证据通道纯加分 --------------------------
    def _fuse(self, a: int, b: int, *, with_components: bool = False):
        sem_avail = [c for c in SEMANTIC if c in self._dense and self._dense[c].has(a, b)]
        sem_weight_sum = sum(self.weights[c] for c in sem_avail)
        sem_share = sum(self.weights[c] for c in SEMANTIC)

        score = 0.0
        components: list[dict] = []
        for name in SEMANTIC + EVIDENCE:
            if name in self._dense:
                ch = self._dense[name]
                available = ch.has(a, b)
                raw = ch.raw(a, b) if available else 0.0
                sim = ch.norm(a, b) if available else 0.0
                shared = ch.shared.get((min(a, b), max(a, b)), []) if available else []
                count = 0
            elif name in self._sparse:
                sp = self._sparse[name]
                raw = sp.raw(a, b)
                sim = sp.norm(a, b)
                shared = sp.shared.get((min(a, b), max(a, b)), [])
                count = sp.counts.get((min(a, b), max(a, b)), 0)
                available = raw > 0
            else:
                continue

            if name in SEMANTIC:
                # 可用语义通道之间重新分配 sem_share，缺失通道不惩罚该对
                w_eff = (self.weights[name] / sem_weight_sum * sem_share) if (available and sem_weight_sum > 0) else 0.0
            else:
                w_eff = self.weights[name]

            contrib = sim * w_eff
            score += contrib
            if with_components:
                components.append({
                    "facet": name,
                    "label": CHANNEL_LABELS[name],
                    "sim": round(sim, 4),
                    "raw": round(raw, 4),
                    "weight": round(w_eff, 4),
                    "contribution": round(contrib, 4),
                    "reason": _reason(name, sim, raw, shared, count, available),
                    "shared": shared,
                })
        score = min(1.0, score)
        return (score, components) if with_components else (score, None)


def _reason(name: str, sim: float, raw: float, shared: list[str], count: int, available: bool) -> str:
    label = CHANNEL_LABELS[name]
    if name in SEMANTIC:
        if not available:
            return f"{label}：缺少可比文本"
        if name == "term":
            return ("共享术语: " + "、".join(shared)) if shared else "无共享术语"
        level = "高度一致" if sim >= 0.66 else ("部分相关" if sim >= 0.33 else "差异较大")
        return f"{label}{level}（余弦 {raw:.2f}）"
    if name == "bibcoupling":
        return f"共享 {count} 篇参考文献" if count else "无共享参考文献"
    if name == "cocitation":
        return f"被 {count} 篇论文共同引用" if count else "无共引"
    return ("共享: " + "、".join(shared)) if shared else f"无共享{label}"


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
async def compute_relations(
    papers: list[PaperFeatures],
    *,
    embed_fn,
    weights: dict[str, float] | None = None,
    bibcoupling: dict[tuple[int, int], tuple[float, int]] | None = None,
    cocitation: dict[tuple[int, int], tuple[float, int]] | None = None,
    calibrate: bool = True,
) -> RelationResult:
    """计算工作区内两两论文的关联度。

    Args:
        papers: 论文特征（DB 无关）。
        embed_fn: 注入的向量化函数 list[str] -> list[list[float]]。
        weights: 通道权重覆盖（见 DEFAULT_WEIGHTS）。作为校准的先验。
        bibcoupling / cocitation: {(min,max): (salton, shared_count)}，由调用方从引用
            关系算好传入（见 structural.py）。缺省表示该通道无数据。
        calibrate: 是否用工作区自身的结构证据校准语义通道权重（见 calibration.py）。
            证据不足时自动退回 ``weights``。关掉即为纯先验权重（消融实验用）。
    """
    w = normalize_weights(weights)
    # 去重：同一 paper_id 只算一次（重复行会让 index 与 ids 错位，产出自环对）
    seen: set[int] = set()
    papers = [p for p in papers if not (p.id in seen or seen.add(p.id))]
    ids = [p.id for p in papers]
    index = {pid: i for i, pid in enumerate(ids)}
    n = len(ids)
    if n < 2:
        return RelationResult(pairs=[], weights=w, coverage={})

    dense: dict[str, _DenseChannel] = {}

    # 1) topic：doc_vector 优先，否则 embedding(topic_text)
    topic_vecs = await build_topic_vectors(papers, embed_fn=embed_fn)
    dense["topic"] = _dense_from_vectors("topic", index, topic_vecs)

    # 2) purpose / method：解读结果里的文本；缺失的论文该通道不可用
    for name, getter in (("purpose", lambda p: p.purpose_text), ("method", lambda p: p.method_text)):
        vecs = await _embed_field(papers, getter, embed_fn=embed_fn)
        dense[name] = _dense_from_vectors(name, index, vecs)

    # 3) term：IDF 加权术语重叠（标题 + 目的 + 方法 + 摘要 + 实体名）
    dense["term"] = _term_channel(papers, index)

    for ch in dense.values():
        ch.calibrate()

    # 4) 证据通道
    sparse: dict[str, _SparseChannel] = {
        "dataset": _entity_channel("dataset", papers, lambda p: p.datasets),
        "baseline": _entity_channel("baseline", papers, lambda p: p.baselines),
        "bibcoupling": _structural_channel("bibcoupling", bibcoupling),
        "cocitation": _structural_channel("cocitation", cocitation),
    }
    for sp in sparse.values():
        sp.calibrate()

    # 5) 权重自监督校准：用工作区自身的引用结构当弱标签，按判别力重分配语义通道权重
    report = _calibrate_semantic_weights(
        papers, ids, dense, w,
        bibcoupling=bibcoupling, cocitation=cocitation,
    ) if calibrate else CalibrationReport(False, "已关闭权重校准", {c: w[c] for c in SEMANTIC})
    if report.applied:
        w = {**w, **report.weights}
        logger.info(
            "relatedness: 权重校准生效 %s（%s）",
            {k: round(v, 3) for k, v in report.weights.items()}, report.reason,
        )

    result = RelationResult(
        pairs=[], weights=w,
        coverage={
            **{name: int(ch.defined.sum()) for name, ch in dense.items()},
            **{name: len([1 for v in sp.values.values() if v > 0]) for name, sp in sparse.items()},
        },
        calibration=report,
        _dense=dense, _sparse=sparse,
    )

    pairs: list[tuple[int, int, float]] = []
    for i in range(n):
        for j in range(i + 1, n):
            a, b = ids[i], ids[j]
            score, _ = result._fuse(a, b)
            if score > 0:
                pairs.append((a, b, round(score, 4)))
    result.pairs = pairs

    logger.info(
        "relatedness: %d 篇论文 → %d 对；通道覆盖 %s",
        n, len(pairs), result.coverage,
    )
    return result


def _calibrate_semantic_weights(
    papers: list[PaperFeatures],
    ids: list[int],
    dense: dict[str, _DenseChannel],
    weights: dict[str, float],
    *,
    bibcoupling: dict[tuple[int, int], tuple[float, int]] | None,
    cocitation: dict[tuple[int, int], tuple[float, int]] | None,
) -> CalibrationReport:
    """用引用结构做弱监督，按判别力重分配**语义**通道的权重。

    证据通道不校准：bibcoupling / cocitation 正是监督信号的来源，自己给自己打分是泄漏。
    """
    cites = {p.id: set(p.cites) for p in papers if p.cites}
    positives, negatives = build_supervision(
        ids, cites=cites, bibcoupling=bibcoupling, cocitation=cocitation,
    )
    prior = {c: weights[c] for c in SEMANTIC}
    if not positives or not negatives:
        return CalibrationReport(
            False, "没有可用的结构证据（引用未回填？），沿用配置权重", prior,
            n_positives=len(positives), n_negatives=len(negatives),
        )

    samples: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for name, ch in dense.items():
        if name not in SEMANTIC:
            continue
        pos = np.array([ch.norm(a, b) for a, b in positives if ch.has(a, b)], dtype=float)
        neg = np.array([ch.norm(a, b) for a, b in negatives if ch.has(a, b)], dtype=float)
        samples[name] = (pos, neg)

    return calibrate_weights(
        samples, prior, n_positives=len(positives), n_negatives=len(negatives),
    )


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
            if norm <= 0:
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
