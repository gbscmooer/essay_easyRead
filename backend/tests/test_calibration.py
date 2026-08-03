"""
通道权重自监督校准单测（graphkit.calibration）

守住的契约：
1. 判别力强的通道拿到更多权重，判别力≈随机的通道被压下去；
2. 结构证据不足时**整体放弃校准**，原样沿用配置权重（宁可不动，也不要拿噪声调权重）；
3. 组内总份额不变（否则语义组 / 证据组之间的比例会被意外改动）；
4. 同一份输入结果可复现（负样本抽样有固定种子）。
"""

import asyncio

import numpy as np

from backend.services.deep_research.graphkit import PaperFeatures, compute_relations
from backend.services.deep_research.graphkit.calibration import (
    build_supervision,
    calibrate_weights,
    rank_auc,
)

PRIOR = {"topic": 0.18, "purpose": 0.24, "method": 0.16, "term": 0.14}


def _samples(rng, *, signal_channels, n_pos=200, n_neg=2000):
    """构造 {通道: (正样本分数, 负样本分数)}：signal 通道可分，其余纯噪声。"""
    out = {}
    for c in PRIOR:
        if c in signal_channels:
            pos = rng.normal(0.75, 0.12, n_pos)
            neg = rng.normal(0.30, 0.12, n_neg)
        else:
            pos = rng.normal(0.50, 0.20, n_pos)
            neg = rng.normal(0.50, 0.20, n_neg)
        out[c] = (np.clip(pos, 0, 1), np.clip(neg, 0, 1))
    return out


# ---------------------------------------------------------------------------
# AUC
# ---------------------------------------------------------------------------
def test_rank_auc_edge_cases():
    assert rank_auc(np.array([1.0, 0.9]), np.array([0.1, 0.2])) == 1.0
    assert rank_auc(np.array([0.1, 0.2]), np.array([1.0, 0.9])) == 0.0
    assert rank_auc(np.array([0.5, 0.5]), np.array([0.5, 0.5])) == 0.5   # 全并列 → 随机
    assert rank_auc(np.array([]), np.array([0.5])) == 0.5


# ---------------------------------------------------------------------------
# 权重拟合
# ---------------------------------------------------------------------------
def test_discriminating_channel_gets_more_weight():
    rng = np.random.default_rng(0)
    report = calibrate_weights(
        _samples(rng, signal_channels={"term"}), PRIOR, n_positives=200, n_negatives=2000,
    )
    assert report.applied, report.reason
    assert report.weights["term"] > PRIOR["term"] * 2
    assert report.weights["method"] < PRIOR["method"]
    assert report.channels["term"].auc > 0.9
    assert 0.4 < report.channels["method"].auc < 0.6      # 噪声通道≈随机


def test_group_share_is_preserved():
    rng = np.random.default_rng(1)
    report = calibrate_weights(
        _samples(rng, signal_channels={"topic", "term"}), PRIOR,
        n_positives=200, n_negatives=2000,
    )
    assert abs(sum(report.weights.values()) - sum(PRIOR.values())) < 1e-9


def test_scarce_evidence_falls_back_to_prior():
    rng = np.random.default_rng(2)
    report = calibrate_weights(
        _samples(rng, signal_channels={"term"}, n_pos=5, n_neg=50), PRIOR,
        n_positives=5, n_negatives=50,
    )
    assert not report.applied
    assert report.weights == PRIOR
    assert "不足" in report.reason


def test_all_random_channels_falls_back_to_prior():
    rng = np.random.default_rng(3)
    report = calibrate_weights(
        _samples(rng, signal_channels=set()), PRIOR, n_positives=200, n_negatives=2000,
    )
    assert not report.applied
    assert report.weights == PRIOR


def test_shrinkage_pulls_toward_prior_when_evidence_is_thin():
    rng = np.random.default_rng(4)
    samples = _samples(rng, signal_channels={"term"}, n_pos=30, n_neg=600)
    thin = calibrate_weights(samples, PRIOR, n_positives=30, n_negatives=600)
    rich = calibrate_weights(samples, PRIOR, n_positives=3000, n_negatives=600)
    assert thin.applied and rich.applied
    assert thin.shrink < rich.shrink
    assert abs(thin.weights["term"] - PRIOR["term"]) < abs(rich.weights["term"] - PRIOR["term"])


# ---------------------------------------------------------------------------
# 弱监督样本构造
# ---------------------------------------------------------------------------
def test_supervision_from_citations_and_bibcoupling():
    ids = list(range(1, 21))
    pos, neg = build_supervision(
        ids,
        cites={1: {2}, 3: {99}},                    # 99 不在工作区 → 忽略
        bibcoupling={(4, 5): (0.3, 4), (6, 7): (0.1, 1)},   # 共享 1 篇不算正样本
        cocitation={(8, 9): (0.2, 3)},
    )
    assert (1, 2) in pos and (4, 5) in pos
    assert (6, 7) not in pos                        # 证据太弱
    assert (3, 99) not in pos
    evidenced = {(1, 2), (4, 5), (6, 7), (8, 9)}
    assert evidenced.isdisjoint(set(neg)), "有任何结构证据的对都不能当负样本"
    assert all(a < b for a, b in neg)


def test_supervision_is_deterministic():
    ids = list(range(1, 60))
    cites = {i: {i + 1} for i in range(1, 30)}
    a = build_supervision(ids, cites=cites)
    b = build_supervision(ids, cites=cites)
    assert a == b


def test_supervision_without_evidence_is_empty():
    pos, neg = build_supervision(list(range(1, 20)))
    assert pos == [] and neg == []


# ---------------------------------------------------------------------------
# 端到端：compute_relations 里的校准
# ---------------------------------------------------------------------------
def _corpus(n=24):
    """两个方向；term 通道（标题里的专名）完全可分，其余通道信息量低。"""
    papers = []
    for i in range(1, n + 1):
        side = i % 2
        papers.append(PaperFeatures(
            id=i,
            title=f"paper {i} about {'mvtec-ad patchcore' if side else 'coco-caption blip2'}",
            is_analyzed=True,
            purpose_text=f"purpose {i}",
            method_text=f"method {i}",
            doc_vector=[1.0, 0.0] if side else [0.98, 0.2],
        ))
    return papers


async def _embed(texts):
    return [[1.0, 0.0] for _ in texts]      # 所有短文本向量一样 → purpose/method 无判别力


def test_compute_relations_reports_calibration():
    papers = _corpus()
    # 同方向论文共享参考文献 → 弱监督正样本
    bib = {}
    ids = [p.id for p in papers]
    for x in range(len(ids)):
        for y in range(x + 1, len(ids)):
            a, b = ids[x], ids[y]
            if a % 2 == b % 2:
                bib[(a, b)] = (0.3, 5)
    rel = asyncio.run(compute_relations(papers, embed_fn=_embed, bibcoupling=bib))
    report = rel.calibration
    assert report is not None and report.applied, report.reason if report else "no report"
    # term 通道能分开两个方向，purpose/method 常量向量分不开 → 权重此消彼长
    assert report.weights["term"] > report.weights["purpose"]
    assert report.weights["term"] > report.weights["method"]
    assert rel.weights["term"] == report.weights["term"]


def test_calibration_can_be_disabled():
    papers = _corpus()
    rel = asyncio.run(compute_relations(papers, embed_fn=_embed, calibrate=False))
    assert rel.calibration is not None and not rel.calibration.applied
    assert rel.weights["term"] == 0.14        # 默认先验原样保留
