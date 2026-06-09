"""
Facet — 论文间一种"关系维度"的可插拔抽象

一个 Facet 回答"两篇论文在<某个维度>上有多像"，并能给出**可解释的理由**。
引擎（engine.py）持有一组 Facet，先对全体论文 `prepare()`（批量预计算，如 embedding
或集合归一化），再对每一对论文 `score()`。

加一个新维度（如"同会议""同作者""共享概念"）= 继承下面某个基类、实现 1-2 个方法、
在 facets/__init__.py 注册。引擎与前端无需改动。

两个常用基类：
- EmbeddingFacet：维度由一段文本表示，相似度 = 文本向量余弦（如 purpose / method）
- SetFacet：       维度由一组离散项表示，相似度 = Jaccard 重叠（如 dataset / baseline）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

import math

from ..features import PaperFeatures

# 注入式向量化：list[str] -> list[list[float]]（异步，与生产 embed_texts 同签名）
EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]


@dataclass
class FacetScore:
    """单个维度对某一对论文的打分结果。"""
    sim: float                       # [0,1]
    reason: str = ""                 # 人类可读理由（前端展示）
    shared: list[str] = field(default_factory=list)  # 共享项（如共同数据集）


class Facet:
    """关系维度基类。子类至少要设 name/label/default_weight，并实现 prepare + score。"""
    name: str = ""
    label: str = ""
    default_weight: float = 0.0

    async def prepare(self, papers: list[PaperFeatures], *, embed_fn: EmbedFn) -> None:
        """批量预计算，把每篇论文在本维度的中间表示存进内部状态。"""
        raise NotImplementedError

    def score(self, a: PaperFeatures, b: PaperFeatures) -> FacetScore:
        """计算一对论文在本维度的相似度 + 理由。"""
        raise NotImplementedError


# ----------------------------------------------------------------------------
# 基类 1：基于文本 embedding 余弦
# ----------------------------------------------------------------------------
class EmbeddingFacet(Facet):
    """子类只需实现 text(pf) 返回该维度的代表文本，并设置 name/label/default_weight。"""

    def __init__(self, *, center: bool = True):
        self.center = center                 # 全局均值中心化（拉开整体偏高的余弦）
        self._vecs: dict[int, list[float]] = {}

    def text(self, pf: PaperFeatures) -> str:
        raise NotImplementedError

    async def prepare(self, papers: list[PaperFeatures], *, embed_fn: EmbedFn) -> None:
        self._vecs = {}
        usable = [p for p in papers if self.text(p).strip()]
        if len(usable) < 2:
            return
        texts = [self.text(p) for p in usable]
        vecs = await embed_fn(texts)
        mat = [_l2_normalize(v) for v in vecs]
        if self.center and len(mat) >= 3:
            mat = _center_rows(mat)
        for p, v in zip(usable, mat):
            self._vecs[p.id] = v

    def score(self, a: PaperFeatures, b: PaperFeatures) -> FacetScore:
        va, vb = self._vecs.get(a.id), self._vecs.get(b.id)
        if va is None or vb is None:
            return FacetScore(0.0, reason=f"{self.label}：缺少可比文本")
        sim = max(0.0, _dot(va, vb))
        return FacetScore(round(sim, 4), reason=_level_note(self.label, sim))


# ----------------------------------------------------------------------------
# 基类 2：基于离散集合 Jaccard 重叠
# ----------------------------------------------------------------------------
class SetFacet(Facet):
    """子类只需实现 items(pf) 返回该维度的离散项列表，并设置 name/label/default_weight。"""

    def __init__(self, **_):
        self._sets: dict[int, set[str]] = {}
        self._orig: dict[int, list[str]] = {}

    def items(self, pf: PaperFeatures) -> list[str]:
        raise NotImplementedError

    async def prepare(self, papers: list[PaperFeatures], *, embed_fn: EmbedFn) -> None:
        self._sets, self._orig = {}, {}
        for p in papers:
            orig = [str(x).strip() for x in self.items(p) if str(x).strip()]
            self._orig[p.id] = orig
            self._sets[p.id] = {x.lower() for x in orig}

    def score(self, a: PaperFeatures, b: PaperFeatures) -> FacetScore:
        sa, sb = self._sets.get(a.id, set()), self._sets.get(b.id, set())
        sim = _jaccard(sa, sb)
        shared = [orig for orig in self._orig.get(a.id, []) if orig.lower() in sb]
        reason = ("共享: " + ", ".join(shared)) if shared else f"无共享{self.label}"
        return FacetScore(round(sim, 4), reason=reason, shared=shared)


# ----------------------------------------------------------------------------
# 纯数学工具（无 numpy 依赖，便于沙盒最小化运行）
# ----------------------------------------------------------------------------
def _l2_normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _center_rows(mat: list[list[float]]) -> list[list[float]]:
    dim = len(mat[0])
    mean = [sum(row[i] for row in mat) / len(mat) for i in range(dim)]
    return [_l2_normalize([row[i] - mean[i] for i in range(dim)]) for row in mat]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _level_note(label: str, sim: float) -> str:
    if sim >= 0.6:
        return f"{label}高度一致"
    if sim >= 0.3:
        return f"{label}部分相关"
    return f"{label}差异较大"
