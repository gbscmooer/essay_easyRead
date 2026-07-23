"""
真实数据图检索评测（零 mock）

与 eval/ 目录的 mock 回归门禁互补：本脚本只读真实数据库（ResearchGraph +
PaperReference），不用任何 fixture、不联网、不调 LLM/embedding。回答两个问题：

1. **结构 / 通道行为**：真实图上 PPR 信号的质量——dangling 占比、PPR 质量困在
   seed 的比例、Top-10 是否被固定枢纽霸榜（跨 seed Jaccard）。
2. **held-out 引用恢复**（核心有效性检验）：随机遮蔽 20% 已解析的 cites 对，
   用被遮蔽边的 citing 论文做 seed 跑 PPR，看能否把被遮蔽的 cited 论文排回来。
   对比三臂：旧配置 PPR（无反向边）/ 新配置 PPR（反向边 + 排除 seed）/ 入度基线。
   bibcoupling / cocitation 边在**遮蔽后的引用集**上用生产函数重算，杜绝泄漏。

用法（仓库根目录）：
    python -m backend.tests.evaluation.deep_research.real_graph_eval --ws 1 \
        [--mask 0.2] [--trials 20] [--tag pre_fix] [--out report.json]
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone

from sqlmodel import Session, select

from backend.models.paper import Paper
from backend.models.research import PaperReference, ResearchGraph
from backend.models.workspace import WorkspacePaper
from backend.services.database import get_engine
from backend.services.deep_research.graph_builder import (
    _build_cites_edges,
    _build_structural_edges,
)
from backend.services.deep_research.graph_retriever import (
    GraphRetrieverConfig,
    build_ppr_graph,
    score_papers_ppr,
)

RNG_SEED = 42
RECALL_KS = (5, 10, 20)

CFG_OLD = GraphRetrieverConfig(cites_reverse_factor=0.0, exclude_seeds=False)
CFG_NEW = GraphRetrieverConfig()  # 生产默认：反向边 0.25 + 排除 seed


# ── 数据加载 ──────────────────────────────────────────────

def _load(ws_id: int):
    with Session(get_engine()) as s:
        graph_row = s.exec(
            select(ResearchGraph).where(ResearchGraph.workspace_id == ws_id)
        ).first()
        papers = s.exec(
            select(Paper)
            .join(WorkspacePaper, WorkspacePaper.paper_id == Paper.id)
            .where(WorkspacePaper.workspace_id == ws_id)
            .order_by(Paper.id)
        ).all()
        pids = [p.id for p in papers if p.id is not None]
        refs = list(
            s.exec(
                select(PaperReference).where(PaperReference.citing_paper_id.in_(pids))  # type: ignore[attr-defined]
            ).all()
        ) if pids else []
        # detach：session 关闭后仍要访问属性
        for p in papers:
            s.expunge(p)
        for r in refs:
            s.expunge(r)
    graph = None
    if graph_row:
        graph = {"nodes": graph_row.nodes or [], "edges": graph_row.edges or []}
    return graph, list(papers), refs


# ── 结构指标 ──────────────────────────────────────────────

def structure_metrics(graph: dict) -> dict:
    nodes, edges = graph["nodes"], graph["edges"]
    ids = [n.get("id") for n in nodes if n.get("id")]
    kinds: Counter[str] = Counter(str(e.get("kind")) for e in edges)
    bridges = sum(1 for e in edges if e.get("bridge"))

    g = build_ppr_graph(nodes, edges)  # 旧语义（无反向边）下的 dangling 才是原生结构
    dangling = sum(1 for n in g.nodes() if g.out_degree(n) == 0)

    # 弱连通分量
    und: dict[str, set[str]] = defaultdict(set)
    for u, v in g.edges():
        und[u].add(v)
        und[v].add(u)
    seen: set[str] = set()
    comps: list[int] = []
    for n in g.nodes():
        if n in seen:
            continue
        q, size = deque([n]), 0
        seen.add(n)
        while q:
            x = q.popleft()
            size += 1
            for y in und.get(x, ()):  # noqa: B023
                if y not in seen:
                    seen.add(y)
                    q.append(y)
        comps.append(size)
    comps.sort(reverse=True)

    return {
        "node_entries": len(ids),
        "unique_nodes": len(set(ids)),
        "duplicate_entries": len(ids) - len(set(ids)),
        "edges_total": len(edges),
        "edges_by_kind": dict(kinds),
        "bridge_edges": bridges,
        "ppr_graph_nodes": g.number_of_nodes(),
        "ppr_graph_edges": g.number_of_edges(),
        "dangling_nodes": dangling,
        "dangling_ratio": round(dangling / max(1, g.number_of_nodes()), 4),
        "wcc_count": len(comps),
        "wcc_top5": comps[:5],
    }


# ── 通道行为（跨 seed 稳定性 / seed 质量占比 / 一跳占比）────────

def _spearman(a: list[float], b: list[float]) -> float:
    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    ra, rb = ranks(a), ranks(b)
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = (sum((x - ma) ** 2 for x in ra)) ** 0.5
    db = (sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / (da * db) if da > 0 and db > 0 else float("nan")


def channel_behavior(graph: dict, *, trials: int, cfg: GraphRetrieverConfig, label: str) -> dict:
    g = build_ppr_graph(
        graph["nodes"], graph["edges"], cites_reverse_factor=cfg.cites_reverse_factor
    )
    indeg = {n: g.in_degree(n) for n in g.nodes()}
    eligible: list[int] = []
    for n in g.nodes():
        if g.degree(n) > 0 and str(n).startswith("paper:"):
            try:
                eligible.append(int(str(n).split(":", 1)[1]))
            except ValueError:
                pass
    if len(eligible) < 10:
        return {"label": label, "skipped": "graph too small"}

    rng = random.Random(RNG_SEED)
    seed_mass: list[float] = []
    onehop_share: list[float] = []
    sp_indeg: list[float] = []
    tops: list[set[int]] = []

    for _ in range(trials):
        seeds = rng.sample(eligible, 5)
        scores = score_papers_ppr(graph=graph, seed_ids=seeds, config=cfg)
        if not scores:
            continue
        total = sum(scores.values()) or 1.0
        seed_mass.append(sum(scores.get(s, 0.0) for s in seeds) / total)
        nonseed = sorted(
            ((p, v) for p, v in scores.items() if p not in seeds),
            key=lambda kv: (-kv[1], kv[0]),
        )
        top10 = [p for p, _ in nonseed[:10]]
        hop1: set[int] = set()
        for s in seeds:
            nid = f"paper:{s}"
            if g.has_node(nid):
                for nb in set(g.successors(nid)) | set(g.predecessors(nid)):
                    try:
                        hop1.add(int(str(nb).split(":", 1)[1]))
                    except (IndexError, ValueError):
                        pass
        if top10:
            onehop_share.append(sum(1 for p in top10 if p in hop1) / len(top10))
        tops.append(set(top10))
        common = [(p, v) for p, v in scores.items() if p not in seeds]
        if len(common) >= 5:
            sp_indeg.append(
                _spearman(
                    [v for _, v in common],
                    [float(indeg.get(f"paper:{p}", 0)) for p, _ in common],
                )
            )

    jacs: list[float] = []
    for i in range(len(tops)):
        for j in range(i + 1, len(tops)):
            u = tops[i] | tops[j]
            jacs.append(len(tops[i] & tops[j]) / len(u) if u else 0.0)
    distinct = len(set().union(*tops)) if tops else 0

    return {
        "label": label,
        "trials": len(tops),
        "seed_mass_share_mean": round(statistics.mean(seed_mass), 4) if seed_mass else None,
        "top10_onehop_share_mean": round(statistics.mean(onehop_share), 4) if onehop_share else None,
        "cross_seed_top10_jaccard_mean": round(statistics.mean(jacs), 4) if jacs else None,
        "distinct_papers_in_top10s": distinct,
        "spearman_ppr_vs_indegree_mean": round(statistics.mean(sp_indeg), 4) if sp_indeg else None,
    }


# ── held-out 引用恢复 ─────────────────────────────────────

def _eval_graph_from_refs(papers, kept_refs, similar_edges) -> dict:
    """用生产构图函数在遮蔽后的引用集上重建 cites/bibcoupling/cocitation 边。"""
    from backend.services.deep_research.similarity.bibcoupling import (
        build_cites_map,
        compute_bibliographic_coupling_pairs,
    )
    from backend.services.deep_research.similarity.cocitation import (
        build_cited_by_map,
        compute_co_citation_pairs,
    )

    by_id = {p.id: p for p in papers}
    paper_ids = set(by_id.keys())
    edges, _ = _build_cites_edges(papers, kept_refs, by_id)

    cites_map = build_cites_map(kept_refs, paper_ids)
    cited_by_map = build_cited_by_map(kept_refs, paper_ids)
    edges.extend(
        _build_structural_edges(compute_bibliographic_coupling_pairs(cites_map), kind="bibcoupling")
    )
    edges.extend(
        _build_structural_edges(compute_co_citation_pairs(cited_by_map, paper_ids), kind="cocitation")
    )
    edges.extend(similar_edges)
    nodes = [{"id": f"paper:{pid}"} for pid in sorted(paper_ids)]
    return {"nodes": nodes, "edges": edges}


def _rank_of(target: int, scores: dict[int, float], exclude: set[int]) -> int | None:
    tv = scores.get(target)
    if tv is None or tv <= 0:
        return None
    better = sum(1 for p, v in scores.items() if p not in exclude and v > tv)
    return better + 1


def holdout_citation_recovery(
    papers, refs, similar_edges, *, mask_ratio: float
) -> dict:
    ws_ids = {p.id for p in papers if p.id is not None}
    resolved = sorted({
        (r.citing_paper_id, r.cited_paper_id)
        for r in refs
        if r.cited_paper_id and r.citing_paper_id in ws_ids and r.cited_paper_id in ws_ids
        and r.citing_paper_id != r.cited_paper_id
    })
    if len(resolved) < 20:
        return {"skipped": f"resolved in-workspace citation pairs = {len(resolved)} < 20"}

    rng = random.Random(RNG_SEED)
    masked = rng.sample(resolved, max(1, int(len(resolved) * mask_ratio)))
    masked_set = set(masked)
    kept_refs = [
        r for r in refs
        if (r.citing_paper_id, r.cited_paper_id) not in masked_set
    ]
    eval_graph = _eval_graph_from_refs(papers, kept_refs, similar_edges)

    # 入度基线（遮蔽后 cites 入度，query 无关的流行度排序）
    indeg: Counter[int] = Counter()
    for u, v in set((r.citing_paper_id, r.cited_paper_id) for r in kept_refs if r.cited_paper_id):
        if v in ws_ids:
            indeg[v] += 1

    arms = {
        "ppr_old(no_reverse)": CFG_OLD,
        "ppr_new(reverse+excl_seed)": CFG_NEW,
    }
    ranks: dict[str, list[int | None]] = {k: [] for k in arms}
    ranks["indegree_baseline"] = []

    for u, v in masked:
        for name, cfg in arms.items():
            scores = score_papers_ppr(graph=eval_graph, seed_ids=[u], config=cfg)
            ranks[name].append(_rank_of(v, scores, exclude={u}))
        # 入度基线：v 在入度排序中的位置
        tv = indeg.get(v, 0)
        better = sum(1 for p, c in indeg.items() if p != u and c > tv)
        ranks["indegree_baseline"].append(better + 1 if tv > 0 else None)

    n_candidates = len(ws_ids) - 1
    out: dict = {
        "resolved_pairs": len(resolved),
        "masked_pairs": len(masked),
        "candidate_pool": n_candidates,
        "random_baseline_recall": {f"@{k}": round(k / max(1, n_candidates), 4) for k in RECALL_KS},
        "arms": {},
    }
    for name, rs in ranks.items():
        hit = [r for r in rs if r is not None]
        arm = {
            f"recall@{k}": round(sum(1 for r in rs if r is not None and r <= k) / len(rs), 4)
            for k in RECALL_KS
        }
        arm["mrr"] = round(sum((1.0 / r) for r in hit) / len(rs), 4) if rs else 0.0
        arm["median_rank"] = statistics.median(hit) if hit else None
        arm["unreachable"] = sum(1 for r in rs if r is None)
        out["arms"][name] = arm
    return out


# ── 主入口 ────────────────────────────────────────────────

def run(ws_id: int, *, mask_ratio: float, trials: int, tag: str) -> dict:
    graph, papers, refs = _load(ws_id)
    report: dict = {
        "schema": "real_graph_eval_v1",
        "tag": tag,
        "workspace_id": ws_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "papers_in_workspace": len(papers),
        "paper_references_rows": len(refs),
        "no_mock": True,
    }
    if not graph:
        report["error"] = "workspace has no persisted graph"
        return report

    report["structure"] = structure_metrics(graph)
    report["channel_behavior"] = [
        channel_behavior(graph, trials=trials, cfg=CFG_OLD, label="old(no_reverse,keep_seed)"),
        channel_behavior(graph, trials=trials, cfg=CFG_NEW, label="new(reverse+excl_seed)"),
    ]
    similar_edges = [e for e in graph["edges"] if e.get("kind") == "similar"]
    report["holdout_citation_recovery"] = holdout_citation_recovery(
        papers, refs, similar_edges, mask_ratio=mask_ratio
    )
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="真实数据图检索评测（零 mock）")
    ap.add_argument("--ws", type=int, default=1)
    ap.add_argument("--mask", type=float, default=0.2)
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--tag", type=str, default="")
    ap.add_argument("--out", type=str, default="")
    args = ap.parse_args()

    report = run(args.ws, mask_ratio=args.mask, trials=args.trials, tag=args.tag)
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    main()
