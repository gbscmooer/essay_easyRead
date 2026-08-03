"""
论文关联度 / 研究地图质量评测（真实数据，零 mock）

与 `real_graph_eval.py`（评图检索）互补：本脚本评的是**图本身的质量** —— 关联度打分
准不准、权重该怎么定、骨架该留多密。全部指标建立在一个不需要人工标注的弱真值上：

    工作区内的真实引用对 = "这两篇确实相关"

四张表：
  A 通道判别力      每个通道单独把引用对排到随机对前面的能力（AUC）
  B 权重口径对比    先验权重 vs 自监督校准（引用对 50/50 切分，只在 FIT 半边拟合）
  C 稀疏化方法对比  同一边预算下 全局阈值 / 互近邻 / 多尺度骨架 的精度与可导航性
  D 密度工作点      target_degree 扫描，给"地图该多密"一个有数的答案

用法（仓库根目录，需要 embedding API 可用于 purpose/method 通道；首跑会缓存通道矩阵，
之后加 --cache 即可完全离线复跑）：

    python -m backend.tests.evaluation.deep_research.relation_quality_eval --ws 1 \\
        [--cache data/relation_eval_ws1.pkl] [--out report.json]
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import pickle
import random
from collections import defaultdict
from datetime import datetime, timezone

import networkx as nx
import numpy as np
from sqlmodel import Session, select

from backend.models.paper import Paper
from backend.models.research import PaperReference
from backend.models.workspace import WorkspacePaper
from backend.services.database import get_engine
from backend.services.deep_research import paper_relatedness as pr
from backend.services.deep_research.embedder import embed_texts
from backend.services.deep_research.graph_builder import DEFAULT_MAP_DENSITY
from backend.services.deep_research.graphkit import (
    DEFAULT_WEIGHTS,
    EVIDENCE,
    SEMANTIC,
    compute_relations,
    extract_backbone,
    normalize_weights,
)
from backend.services.deep_research.graphkit.calibration import calibrate_weights, rank_auc

SEED = 42


# ── 数据加载 ──────────────────────────────────────────────

def _load(ws_id: int):
    with Session(get_engine()) as s:
        papers = s.exec(
            select(Paper)
            .join(WorkspacePaper, WorkspacePaper.paper_id == Paper.id)
            .where(WorkspacePaper.workspace_id == ws_id)
            .order_by(Paper.id)
        ).all()
        pids = [p.id for p in papers if p.id is not None]
        refs = list(
            s.exec(select(PaperReference).where(PaperReference.citing_paper_id.in_(pids))).all()
        ) if pids else []
        for p in papers:
            s.expunge(p)
        for r in refs:
            s.expunge(r)
    return list(papers), refs


async def _channels(ws_id: int):
    """跑一遍生产关联度算法，把各通道的原始矩阵抽出来缓存。"""
    papers, refs = _load(ws_id)
    feats = pr.build_paper_features(papers, ws_id, refs)
    bib, cocite = pr.structural_channels(refs, {p.id for p in papers})
    rel = await compute_relations(
        feats, embed_fn=embed_texts, bibcoupling=bib, cocitation=cocite, calibrate=False,
    )
    return {
        "ws_id": ws_id,
        "ids": [f.id for f in feats],
        "dense": {
            n: {"matrix": ch.matrix, "defined": ch.defined, "index": ch.index,
                "lo": ch.lo, "hi": ch.hi}
            for n, ch in rel._dense.items()
        },
        "sparse": {n: {"values": sp.values, "hi": sp.hi} for n, sp in rel._sparse.items()},
        "coverage": rel.coverage,
        "cites": sorted({
            (r.citing_paper_id, r.cited_paper_id) for r in refs
            if r.cited_paper_id and r.citing_paper_id != r.cited_paper_id
        }),
    }


# ── 通道取值 / 融合（与生产 _fuse 同口径）──────────────────

class Channels:
    def __init__(self, data: dict):
        self.d = data
        self.ids = data["ids"]

    def norm(self, name: str, a: int, b: int) -> float | None:
        if name in self.d["dense"]:
            ch = self.d["dense"][name]
            ia, ib = ch["index"].get(a), ch["index"].get(b)
            if ia is None or ib is None or not ch["defined"][ia] or not ch["defined"][ib]:
                return None
            raw, lo, hi = float(ch["matrix"][ia, ib]), ch["lo"], ch["hi"]
        else:
            sp = self.d["sparse"][name]
            raw, lo, hi = sp["values"].get((min(a, b), max(a, b)), 0.0), 0.0, sp["hi"]
        return float(min(1.0, max(0.0, (raw - lo) / (hi - lo))))

    def fuse(self, a: int, b: int, weights: dict[str, float]) -> float:
        sem_share = sum(weights[c] for c in SEMANTIC)
        avail = [c for c in SEMANTIC if weights[c] > 0 and self.norm(c, a, b) is not None]
        wsum = sum(weights[c] for c in avail)
        score = sum(self.norm(c, a, b) * weights[c] / wsum * sem_share for c in avail) if wsum else 0.0
        score += sum((self.norm(c, a, b) or 0.0) * weights[c] for c in EVIDENCE)
        return min(1.0, score)

    def pairs(self, weights: dict[str, float]) -> list[tuple[int, int, float]]:
        out = []
        for a, b in itertools.combinations(self.ids, 2):
            s = self.fuse(a, b, weights)
            if s > 0:
                out.append((a, b, s))
        return out


# ── 表 A：通道判别力 ──────────────────────────────────────

def table_channel_power(ch: Channels, pos: list, neg: list) -> list[dict]:
    rows = []
    for name in SEMANTIC + EVIDENCE:
        p = np.array([v for v in (ch.norm(name, *k) for k in pos) if v is not None])
        n = np.array([v for v in (ch.norm(name, *k) for k in neg) if v is not None])
        rows.append({
            "channel": name,
            "auc": round(rank_auc(p, n), 4),
            "mean_pos": round(float(p.mean()) if p.size else 0.0, 4),
            "mean_neg": round(float(n.mean()) if n.size else 0.0, 4),
            "nonzero_pos_ratio": round(float((p > 0).mean()) if p.size else 0.0, 4),
            "prior_weight": DEFAULT_WEIGHTS[name],
            "leaks_supervision": name in ("bibcoupling", "cocitation"),
        })
    return rows


# ── 表 B：权重口径对比（FIT / TEST 切分，杜绝自评）────────

def table_weight_schemes(ch: Channels, cite_pairs: list, neg: list) -> dict:
    rng = random.Random(SEED)
    shuffled = list(cite_pairs)
    rng.shuffle(shuffled)
    half = len(shuffled) // 2
    fit_pos, test_pos = shuffled[:half], shuffled[half:]
    rng.shuffle(neg)
    fit_neg, test_neg = neg[: len(neg) // 2], neg[len(neg) // 2:]

    prior = normalize_weights(None)
    samples = {}
    for c in SEMANTIC:
        p = np.array([v for v in (ch.norm(c, *k) for k in fit_pos) if v is not None])
        n = np.array([v for v in (ch.norm(c, *k) for k in fit_neg) if v is not None])
        samples[c] = (p, n)
    report = calibrate_weights(
        samples, {c: prior[c] for c in SEMANTIC},
        n_positives=len(fit_pos), n_negatives=len(fit_neg),
    )
    calibrated = {**prior, **report.weights}
    topic_only = {c: (1.0 if c == "topic" else 0.0) for c in SEMANTIC}
    topic_only.update({c: prior[c] for c in EVIDENCE})

    schemes = {"prior": prior, "calibrated": calibrated, "topic_only": topic_only}
    out = {"fit_pairs": len(fit_pos), "test_pairs": len(test_pos),
           "calibration": report.to_dict(), "schemes": {}}
    for name, w in schemes.items():
        p = np.array([ch.fuse(*k, w) for k in test_pos])
        n = np.array([ch.fuse(*k, w) for k in test_neg])
        out["schemes"][name] = {
            "auc_heldout": round(rank_auc(p, n), 4),
            "semantic_weights": {c: round(w[c], 4) for c in SEMANTIC},
        }
    return out


# ── 表 C / D：稀疏化方法与密度工作点 ──────────────────────

def _mutual_knn(pairs, k: int):
    adj = defaultdict(list)
    for a, b, w in pairs:
        adj[a].append((b, w))
        adj[b].append((a, w))
    top = {n: {m for m, _ in sorted(v, key=lambda x: -x[1])[:k]} for n, v in adj.items()}
    return [(a, b, w) for a, b, w in pairs if b in top[a] and a in top[b]]


def _map_metrics(edges, ids, cite_pairs: set) -> dict:
    g = nx.Graph()
    g.add_nodes_from(ids)
    for a, b, w in edges:
        g.add_edge(a, b, weight=w)
    kept = {(min(a, b), max(a, b)) for a, b, _ in edges}
    if not kept:
        return {"edges": 0}
    base_rate = len(cite_pairs) / (len(ids) * (len(ids) - 1) / 2)
    hit = len(kept & cite_pairs)
    comms = [set(c) for c in nx.community.louvain_communities(g, weight="weight", seed=SEED)]
    degs = [d for _, d in g.degree()]
    return {
        "edges": len(kept),
        "mean_degree": round(float(np.mean(degs)), 2),
        "citation_recall": round(hit / max(1, len(cite_pairs)), 4),
        "precision_lift": round((hit / len(kept)) / base_rate, 2) if base_rate else None,
        "modularity": round(nx.community.modularity(g, comms, weight="weight"), 4),
        "components": nx.number_connected_components(g),
        "isolated_nodes": sum(1 for d in degs if d == 0),
        "clusters": sum(1 for c in comms if len(c) > 1),
    }


def table_sparsifiers(ch: Channels, weights: dict, cite_pairs: set, degrees=(4, 8, 12)) -> list[dict]:
    pairs = ch.pairs(weights)
    ids = ch.ids
    rows = []
    for deg in degrees:
        budget = int(deg * len(ids) / 2)
        rows.append({"method": f"global_threshold@deg{deg}", "target_degree": deg,
                     **_map_metrics(sorted(pairs, key=lambda x: -x[2])[:budget], ids, cite_pairs)})
        rows.append({"method": f"mutual_knn@k{max(1, deg // 2)}", "target_degree": deg,
                     **_map_metrics(_mutual_knn(pairs, max(1, deg // 2)), ids, cite_pairs)})
        rows.append({"method": f"backbone@deg{deg}", "target_degree": deg,
                     **_map_metrics(extract_backbone(pairs, target_degree=deg), ids, cite_pairs)})
    return rows


def table_density(ch: Channels, weights: dict, cite_pairs: set,
                  degrees=(4, 6, 8, 12, 20, 40)) -> list[dict]:
    pairs = ch.pairs(weights)
    return [
        {"target_degree": d,
         **_map_metrics(extract_backbone(pairs, target_degree=d), ch.ids, cite_pairs)}
        for d in degrees
    ]


# ── 主流程 ────────────────────────────────────────────────

def _supervision(ids: list[int], cites: list[tuple[int, int]]):
    id_set = set(ids)
    pos = sorted({(min(a, b), max(a, b)) for a, b in cites if a in id_set and b in id_set})
    pos_set = set(pos)
    neg = [k for k in itertools.combinations(sorted(id_set), 2) if k not in pos_set]
    return pos, neg


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ws", type=int, default=1)
    ap.add_argument("--cache", default="", help="通道矩阵缓存文件（存在则离线复跑）")
    ap.add_argument("--out", default="", help="JSON 报告输出路径")
    args = ap.parse_args()

    cache = args.cache or f"data/relation_eval_ws{args.ws}.pkl"
    if os.path.exists(cache):
        data = pickle.load(open(cache, "rb"))
        print(f"[cache] 复用通道矩阵 {cache}")
    else:
        data = asyncio.run(_channels(args.ws))
        os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
        pickle.dump(data, open(cache, "wb"))
        print(f"[cache] 已写入 {cache}")

    ch = Channels(data)
    pos, neg = _supervision(ch.ids, data["cites"])
    cite_set = set(pos)
    print(f"\n工作区 {args.ws}: {len(ch.ids)} 篇论文，{len(pos)} 对工作区内真实引用"
          f"（占全部对 {100 * len(pos) / max(1, len(pos) + len(neg)):.2f}%）")
    print(f"通道覆盖: {data['coverage']}")

    report: dict = {
        "workspace": args.ws,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "papers": len(ch.ids),
        "citation_pairs": len(pos),
        "coverage": data["coverage"],
    }

    print("\n=== A. 通道判别力（正=工作区内真实引用对）===")
    print(f"{'channel':<14}{'AUC':>8}{'mean+':>8}{'mean-':>8}{'先验权重':>10}  备注")
    report["channel_power"] = table_channel_power(ch, pos, neg)
    for r in report["channel_power"]:
        note = "（监督来源，AUC 有泄漏）" if r["leaks_supervision"] else ""
        print(f"{r['channel']:<14}{r['auc']:>8.3f}{r['mean_pos']:>8.3f}"
              f"{r['mean_neg']:>8.3f}{r['prior_weight']:>10.2f}  {note}")

    print("\n=== B. 权重口径（引用对 50/50 切分，只在 FIT 半边拟合）===")
    report["weight_schemes"] = table_weight_schemes(ch, pos, list(neg))
    for name, r in report["weight_schemes"]["schemes"].items():
        print(f"{name:<14} AUC(held-out)={r['auc_heldout']:.4f}  "
              + " ".join(f"{c}={r['semantic_weights'][c]:.3f}" for c in SEMANTIC))
    print(f"校准报告: {report['weight_schemes']['calibration']['reason']}")

    # 生产口径：用全部结构证据校准一次（表 B 的 FIT/TEST 切分只是为了证明能泛化）
    prior = normalize_weights(None)
    full_samples = {}
    for c in SEMANTIC:
        p = np.array([v for v in (ch.norm(c, *k) for k in pos) if v is not None])
        n = np.array([v for v in (ch.norm(c, *k) for k in neg) if v is not None])
        full_samples[c] = (p, n)
    full_report = calibrate_weights(
        full_samples, {c: prior[c] for c in SEMANTIC},
        n_positives=len(pos), n_negatives=len(neg),
    )
    weights = {**prior, **full_report.weights}
    report["production_weights"] = {k: round(v, 4) for k, v in weights.items()}

    hdr = (f"{'method':<26}{'edges':>7}{'deg':>7}{'citeR':>8}{'lift':>7}"
           f"{'mod':>8}{'comp':>6}{'iso':>5}{'clu':>5}")

    print(f"\n=== C0. 权重口径对地图质量的影响（骨架 target_degree={DEFAULT_MAP_DENSITY}）===")
    print(hdr)
    report["weights_vs_map"] = []
    for name, w in (("prior", prior), ("calibrated", weights)):
        m = _map_metrics(
            extract_backbone(ch.pairs(w), target_degree=DEFAULT_MAP_DENSITY), ch.ids, cite_set
        )
        report["weights_vs_map"].append({"weights": name, **m})
        print(f"{name:<26}{m['edges']:>7}{m['mean_degree']:>7.1f}{m['citation_recall']:>8.1%}"
              f"{m['precision_lift']:>6.1f}x{m['modularity']:>8.3f}"
              f"{m['components']:>6}{m['isolated_nodes']:>5}{m['clusters']:>5}")

    print("\n=== C. 稀疏化方法对比（同边预算，均用校准后权重）===")
    print(hdr)
    report["sparsifiers"] = table_sparsifiers(ch, weights, cite_set)
    for r in report["sparsifiers"]:
        print(f"{r['method']:<26}{r['edges']:>7}{r['mean_degree']:>7.1f}"
              f"{r['citation_recall']:>8.1%}{r['precision_lift']:>6.1f}x"
              f"{r['modularity']:>8.3f}{r['components']:>6}{r['isolated_nodes']:>5}{r['clusters']:>5}")

    print(f"\n=== D. 地图密度工作点（生产默认 target_degree={DEFAULT_MAP_DENSITY}）===")
    print(hdr)
    report["density"] = table_density(ch, weights, cite_set)
    for r in report["density"]:
        print(f"{'target_degree=' + str(r['target_degree']):<26}{r['edges']:>7}{r['mean_degree']:>7.1f}"
              f"{r['citation_recall']:>8.1%}{r['precision_lift']:>6.1f}x"
              f"{r['modularity']:>8.3f}{r['components']:>6}{r['isolated_nodes']:>5}{r['clusters']:>5}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"\n报告已写入 {args.out}")


if __name__ == "__main__":
    main()
