"""骨架提取（graphkit/backbone.py）单测：密度控制 + 多尺度性质 + 连通兜底。

背景：v3.0 用 α 当密度旋钮，在 v3 融合分尺度上双峰退化（ws1 实测 α=0.05→平均度 2.2、
α=0.25→41.8、α=0.4→114，中间没有可用区间）。v3.1 改成"给目标平均度、反解环境地板"，
这些用例守住新旋钮的行为契约。
"""

import random

from backend.services.deep_research.graphkit.backbone import (
    extract_backbone,
    extract_backbone_with_bridges,
)


def _clustered_pairs(n_clusters=4, per=12, seed=0):
    """簇内高分、簇间低分的稠密图（模拟真实语料：任何两篇都有点像）。"""
    rng = random.Random(seed)
    labels = {i: i // per for i in range(1, n_clusters * per + 1)}
    pairs = []
    ids = sorted(labels)
    for x in range(len(ids)):
        for y in range(x + 1, len(ids)):
            a, b = ids[x], ids[y]
            same = labels[a] == labels[b]
            base = 0.62 if same else 0.30      # 环境地板 0.30：不从 0 起跳
            pairs.append((a, b, base + rng.uniform(0, 0.18)))
    return pairs, labels


def test_target_degree_controls_density():
    pairs, _ = _clustered_pairs()
    n = 48
    for target in (4, 8, 12):
        kept = extract_backbone(pairs, target_degree=target)
        mean_degree = 2 * len(kept) / n
        assert abs(mean_degree - target) <= max(1.5, target * 0.35), (
            f"target={target} 实际平均度={mean_degree:.1f}（边数 {len(kept)}）"
        )


def test_density_is_monotone_in_target():
    pairs, _ = _clustered_pairs()
    sizes = [len(extract_backbone(pairs, target_degree=t)) for t in (4, 8, 16)]
    assert sizes[0] < sizes[1] < sizes[2], sizes


def test_backbone_keeps_within_cluster_edges():
    """去毛球的意义在于留下的边要更"对"：簇内边占比应远高于原图。"""
    pairs, labels = _clustered_pairs()
    raw_purity = sum(1 for a, b, _ in pairs if labels[a] == labels[b]) / len(pairs)
    kept = extract_backbone(pairs, target_degree=8)
    purity = sum(1 for a, b, _ in kept if labels[a] == labels[b]) / len(kept)
    assert purity > 0.9, f"骨架簇内边占比 {purity:.2f}（原图 {raw_purity:.2f}）"


def test_map_stays_connected_without_isolated_nodes():
    pairs, _ = _clustered_pairs()
    kept = extract_backbone(pairs, target_degree=6, ensure_connected=True)
    seen = {x for a, b, _ in kept for x in (a, b)}
    assert len(seen) == 48, f"有 {48 - len(seen)} 个节点掉出了地图"

    parent = {n: n for n in seen}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, _ in kept:
        parent[find(a)] = find(b)
    assert len({find(n) for n in seen}) == 1, "研究地图应当连通"


def test_bridges_are_exactly_the_connectivity_only_edges():
    pairs, _ = _clustered_pairs()
    full, bridges = extract_backbone_with_bridges(pairs, target_degree=6, ensure_connected=True)
    sparse = extract_backbone(pairs, target_degree=6, ensure_connected=False)
    sparse_keys = {(a, b) for a, b, _ in sparse}
    full_keys = {(a, b) for a, b, _ in full}
    assert bridges == full_keys - sparse_keys
    assert bridges.isdisjoint(sparse_keys)


def test_explicit_baseline_overrides_target_degree():
    """显式给地板时走经典 disparity filter（消融实验/旧配置复现用）。"""
    pairs, _ = _clustered_pairs()
    a = extract_backbone(pairs, target_degree=6, baseline=0.0)
    b = extract_backbone(pairs, target_degree=None, baseline=0.0)
    assert {(x, y) for x, y, _ in a} == {(x, y) for x, y, _ in b}


def test_degree_one_node_keeps_its_only_edge():
    pairs = [(1, 2, 0.9), (2, 3, 0.8), (3, 4, 0.7), (4, 5, 0.6), (5, 1, 0.5), (6, 1, 0.4)]
    kept = extract_backbone(pairs, target_degree=2)
    assert (1, 6) in {(a, b) for a, b, _ in kept}, "度为 1 的节点不能被剪成孤点"


def test_weights_returned_are_original_scores():
    pairs, _ = _clustered_pairs()
    kept = extract_backbone(pairs, target_degree=8)
    original = {(a, b): w for a, b, w in pairs}
    for a, b, w in kept:
        assert w == original[(a, b)], "返回边权必须是原始关联度（地板扣除只用于显著性检验）"


def test_deterministic():
    pairs, _ = _clustered_pairs()
    assert extract_backbone(pairs, target_degree=8) == extract_backbone(pairs, target_degree=8)


def test_degenerate_inputs():
    assert extract_backbone([]) == []
    assert extract_backbone([(1, 1, 0.9)]) == []          # 自环
    assert len(extract_backbone([(1, 2, 0.9)])) == 1
    # 三个节点以内退化保留全部上地板边（不把小地图拆散）
    tiny = extract_backbone([(1, 2, 0.9), (2, 3, 0.8), (1, 3, 0.7)], target_degree=1)
    assert len(tiny) == 3
    # 低于地板的对全部丢弃
    assert extract_backbone([(1, 2, 0.01), (2, 3, 0.02)], min_sim=0.05) == []
