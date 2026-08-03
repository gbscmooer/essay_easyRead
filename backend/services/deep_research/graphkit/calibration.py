"""
calibration — 通道权重自监督校准（纯函数，无 DB）

为什么需要
--------------------------------------------------
v3 把八个通道融合成一个关联度，但**权重是拍脑袋定的**（purpose 0.24 / method 0.16 …）。
在真实工作区上实测，这套先验权重明显不成立：

    工作区 ws1（179 篇 CV/ML 论文，573 对工作区内真实引用）
    通道判别力（AUC，正=真实引用对）
        topic 0.888   term 0.797   purpose 0.642   method 0.573(≈随机)
    → 先验权重把 0.40 的份额压在 purpose+method 上，把判别力最强的 topic 压到 0.18，
      融合分反而**打不过单用 topic**（AUC 0.831 vs 0.886）。

而在另一种语料上结论会**完全反过来**：当一个大方向下有多个细分方向时，全文质心
（topic）只能分出大方向，恰恰是 purpose/method/term 在区分细分方向（见
tests/test_relation_quality.py 的合成对照）。所以不存在一套"对所有工作区都对"的
固定权重——权重必须**按工作区自适应**。

思路：用工作区自己的结构证据当弱监督
--------------------------------------------------
不需要人工标注：**谁引用谁、谁和谁引了同一批文献**，本身就是"这两篇确实相关"的
弱标签（引文推荐文献里的标准做法）。于是：

    正样本  = 工作区内直接引用对 ∪ 文献耦合共享文献数 ≥ min_shared 的对
    负样本  = 完全没有结构证据的对（不互引、无共享参考、无共引），抽样
    通道权重 ∝ 该通道把正样本排到负样本前面的能力
              d_c = max(0, 2·AUC_c − 1)   （Somers' D / Gini，AUC=0.5 的随机通道得 0）

只校准**语义通道**（topic/purpose/method/term）。证据通道（dataset/baseline/
bibcoupling/cocitation）不参与：bibcoupling / cocitation 正是监督信号的来源，
让它们自己给自己打分就是泄漏。

小样本保护
--------------------------------------------------
AUC 的标准误 ~1/√n_pos，工作区小的时候拟合值不可信。用经验贝叶斯式收缩把拟合值
拉回先验：

    λ = n_pos / (n_pos + K)          # 证据越多越信拟合值
    w = (1 − λ)·prior + λ·fitted

正样本少于 min_positives 时整体放弃校准，原样返回先验（新建的小工作区、还没回填
引用的工作区都会走这条路，不会因为噪声估计把图搞坏）。

实测（ws1，引用对 50/50 切分，只在 FIT 半边拟合，TEST 半边评估）
--------------------------------------------------
    先验权重      AUC 0.831
    校准后        AUC 0.886  （topic .44 term .34 purpose .16 method .06）
    并且骨架质量同步提升：同样边预算下引用对召回 45.0% → 50.6%，
    每条边命中引用对的提升倍数 9.7x → 11.0x，modularity 0.577 → 0.629。
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

# 校准参数默认值
MIN_POSITIVES = 20          # 正样本少于此值不校准（估计不可信）
MIN_CHANNEL_SAMPLES = 10    # 单通道可用样本少于此值该通道不校准（保留先验）
SHRINK_K = 50.0             # 收缩强度：λ = n_pos / (n_pos + K)
MIN_SHARED_REFS = 2         # 文献耦合共享几篇参考文献才算正样本
MAX_NEGATIVE_RATIO = 25     # 负样本最多取正样本的多少倍
MAX_NEGATIVES = 20000       # 负样本绝对上限（大工作区的 O(n²) 保护）
SAMPLE_SEED = 20260803      # 负样本抽样种子，保证同一份数据结果可复现


@dataclass(frozen=True)
class ChannelEvidence:
    """单个通道在本工作区的判别力。"""

    auc: float          # 正样本排在负样本前面的概率
    gini: float         # 扣掉噪声下限后的判别力，随机通道为 0（见 _effective_gini）
    n_pos: int
    n_neg: int


# 判别力显著性：AUC 估计本身有抽样噪声，纯噪声通道也会随机落在 0.5 上下。若直接按
# 原始 gini 分配权重，某个"恰好 AUC=0.52"的通道会独吞全部权重（实测会出这种事）。
# 因此先扣掉噪声下限 z·SE(AUC) 再当判别力用；扣完 ≤0 的通道视为"无判别力"。
AUC_Z = 1.64        # 单侧 95%


def _auc_stderr(n_pos: int, n_neg: int) -> float:
    """AUC 的保守标准误（Bamber 上界，与分布无关）。"""
    if n_pos <= 0 or n_neg <= 0:
        return 0.5
    return math.sqrt((n_pos + n_neg + 1) / (12.0 * n_pos * n_neg))


def _effective_gini(auc: float, n_pos: int, n_neg: int) -> float:
    """扣掉抽样噪声下限的判别力：max(0, 2·(AUC − 0.5 − z·SE))。"""
    return max(0.0, 2.0 * (auc - 0.5 - AUC_Z * _auc_stderr(n_pos, n_neg)))


@dataclass
class CalibrationReport:
    """校准结果（同时是给用户看的解释）。"""

    applied: bool
    reason: str
    weights: dict[str, float] = field(default_factory=dict)   # 校准后的绝对权重（仅校准通道）
    channels: dict[str, ChannelEvidence] = field(default_factory=dict)
    n_positives: int = 0
    n_negatives: int = 0
    shrink: float = 0.0

    def to_dict(self) -> dict:
        return {
            "applied": self.applied,
            "reason": self.reason,
            "shrink": round(self.shrink, 4),
            "n_positives": self.n_positives,
            "n_negatives": self.n_negatives,
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "channels": {
                k: {"auc": round(v.auc, 4), "gini": round(v.gini, 4), "n_pos": v.n_pos}
                for k, v in self.channels.items()
            },
        }


def rank_auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """Mann–Whitney U 对应的 AUC（并列取平均秩，精确解非抽样）。"""
    pos = np.asarray(pos, dtype=float)
    neg = np.asarray(neg, dtype=float)
    if pos.size == 0 or neg.size == 0:
        return 0.5
    allv = np.concatenate([pos, neg])
    order = np.argsort(allv, kind="mergesort")
    ranks = np.empty(allv.size, dtype=float)
    ranks[order] = np.arange(1, allv.size + 1, dtype=float)
    # 并列值取平均秩，否则大量相同分（如同为 0 的稀疏通道）会把 AUC 拉偏
    uniq, inv, cnt = np.unique(allv, return_inverse=True, return_counts=True)
    if uniq.size < allv.size:
        sums = np.zeros(uniq.size, dtype=float)
        np.add.at(sums, inv, ranks)
        ranks = (sums / cnt)[inv]
    n1, n0 = float(pos.size), float(neg.size)
    return float((ranks[: pos.size].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def build_supervision(
    ids: list[int],
    *,
    cites: dict[int, set[int]] | None = None,
    bibcoupling: dict[tuple[int, int], tuple[float, int]] | None = None,
    cocitation: dict[tuple[int, int], tuple[float, int]] | None = None,
    min_shared_refs: int = MIN_SHARED_REFS,
    max_negative_ratio: int = MAX_NEGATIVE_RATIO,
    max_negatives: int = MAX_NEGATIVES,
    seed: int = SAMPLE_SEED,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """从引用结构造弱监督样本。

    正样本：工作区内直接引用对，或共享参考文献数 ≥ ``min_shared_refs`` 的对。
    负样本：三种结构证据全无的对，确定性抽样（同输入 → 同输出）。
    """
    id_set = set(ids)
    positives: set[tuple[int, int]] = set()
    evidenced: set[tuple[int, int]] = set()      # 任何一种结构证据（不能当负样本）

    for citing, cited_ids in (cites or {}).items():
        if citing not in id_set:
            continue
        for cited in cited_ids:
            if cited in id_set and cited != citing:
                positives.add((min(citing, cited), max(citing, cited)))

    for (a, b), (_w, shared) in (bibcoupling or {}).items():
        if a not in id_set or b not in id_set or a == b:
            continue
        key = (min(a, b), max(a, b))
        evidenced.add(key)
        if shared >= min_shared_refs:
            positives.add(key)

    for (a, b), (_w, shared) in (cocitation or {}).items():
        if a in id_set and b in id_set and a != b and shared > 0:
            evidenced.add((min(a, b), max(a, b)))

    evidenced |= positives

    n_target = min(max_negatives, max(0, len(positives)) * max_negative_ratio)
    negatives: list[tuple[int, int]] = []
    if n_target and len(ids) >= 4:
        rng = random.Random(seed)
        sorted_ids = sorted(id_set)
        n = len(sorted_ids)
        total_pairs = n * (n - 1) // 2
        if total_pairs - len(evidenced) <= n_target:
            # 小工作区：直接全量枚举，避免抽样反复空转
            negatives = [
                (a, b)
                for i, a in enumerate(sorted_ids)
                for b in sorted_ids[i + 1:]
                if (a, b) not in evidenced
            ]
        else:
            seen: set[tuple[int, int]] = set()
            for _ in range(n_target * 8):        # 上限保护：命中率低时也会停
                if len(negatives) >= n_target:
                    break
                a = sorted_ids[rng.randrange(n)]
                b = sorted_ids[rng.randrange(n)]
                if a == b:
                    continue
                key = (min(a, b), max(a, b))
                if key in evidenced or key in seen:
                    continue
                seen.add(key)
                negatives.append(key)
    return sorted(positives), negatives


def calibrate_weights(
    samples: dict[str, tuple[np.ndarray, np.ndarray]],
    prior: dict[str, float],
    *,
    n_positives: int,
    n_negatives: int,
    min_positives: int = MIN_POSITIVES,
    min_channel_samples: int = MIN_CHANNEL_SAMPLES,
    shrink_k: float = SHRINK_K,
) -> CalibrationReport:
    """按判别力重新分配一组通道的权重。

    Args:
        samples: {通道名: (正样本分数, 负样本分数)}；缺失值请在调用前剔除。
                 分数应当是**已归一到同一尺度**的通道分（生产里是分位数缩放后的值）。
        prior: 这组通道的先验绝对权重。校准只在组内重新分配，**组的总份额不变**，
               因此不会意外改变语义组 / 证据组之间的比例。
        n_positives / n_negatives: 样本量（用于收缩强度与报告）。

    Returns:
        CalibrationReport。``applied=False`` 时 ``weights`` 原样等于 prior。
    """
    share = sum(max(0.0, v) for v in prior.values())
    if share <= 0:
        return CalibrationReport(False, "该组权重全为 0，跳过校准", dict(prior))
    if n_positives < min_positives:
        return CalibrationReport(
            False,
            f"结构证据不足（正样本 {n_positives} < {min_positives}），沿用配置权重",
            dict(prior), n_positives=n_positives, n_negatives=n_negatives,
        )

    evidence: dict[str, ChannelEvidence] = {}
    gini: dict[str, float] = {}
    for name in prior:
        if prior[name] <= 0:
            continue        # 用户把该通道权重调成 0 = 明确要关掉它，校准不得把它重新打开
        pos, neg = samples.get(name, (np.array([]), np.array([])))
        pos = np.asarray(pos, dtype=float)
        neg = np.asarray(neg, dtype=float)
        if pos.size < min_channel_samples or neg.size < min_channel_samples:
            continue                              # 样本太少 → 该通道不校准，保留先验份额
        auc = rank_auc(pos, neg)
        g = _effective_gini(auc, int(pos.size), int(neg.size))
        evidence[name] = ChannelEvidence(auc=auc, gini=g, n_pos=int(pos.size), n_neg=int(neg.size))
        gini[name] = g

    fitted_total = sum(gini.values())
    if not evidence or fitted_total <= 0:
        return CalibrationReport(
            False, "所有通道的判别力都与随机无异，沿用配置权重", dict(prior),
            channels=evidence, n_positives=n_positives, n_negatives=n_negatives,
        )

    # 只在"有判别力估计"的通道之间重分配它们原本占有的份额，
    # 样本不足的通道保持先验权重不动。
    fitted_share = sum(max(0.0, prior[c]) for c in evidence)
    lam = n_positives / (n_positives + shrink_k) if shrink_k > 0 else 1.0

    weights = {c: max(0.0, v) for c, v in prior.items()}
    for c in evidence:
        target = gini[c] / fitted_total * fitted_share
        weights[c] = (1.0 - lam) * max(0.0, prior[c]) + lam * target

    # 数值兜底：组内总份额必须与先验一致（下游按组归一，漂了会改变组间比例）
    total = sum(weights.values())
    if total > 0:
        weights = {c: v / total * share for c, v in weights.items()}

    return CalibrationReport(
        True,
        f"按 {n_positives} 对结构证据校准（收缩 λ={lam:.2f}）",
        weights, channels=evidence,
        n_positives=n_positives, n_negatives=n_negatives, shrink=lam,
    )
