"""
沙盒运行器：加载 fixture → FacetEngine 算多维关系 → 建图 → PageRank/Louvain → 打印

完全离线、确定性。用法见 test_g/README.md。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from backend.services.deep_research.graphkit import PaperFeatures, FacetEngine
from backend.services.deep_research.graphkit import graph as gk
from backend.services.deep_research.graph_analytics import annotate_graph
from test_g.fake_embedder import fake_embed

DEFAULT_FIXTURE = Path(__file__).parent / "fixtures" / "sample.json"


def load_papers(path: Path) -> list[PaperFeatures]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [PaperFeatures.from_dict(d) for d in data["papers"]]


async def main(fixture: Path, knn_k: int, mode: str):
    papers = load_papers(fixture)
    print(f"\n载入 {len(papers)} 篇论文（{sum(p.is_analyzed for p in papers)} 篇已解读）：{fixture}\n")

    # 1) 多维关系
    engine = FacetEngine()
    pairs, details = await engine.score_pairs(papers, embed_fn=fake_embed)
    print("=== 论文对的多维分解（综合分降序）===")
    for a, b, score in sorted(pairs, key=lambda x: x[2], reverse=True):
        comps = details[(min(a, b), max(a, b))]["components"]
        brk = "  ".join(f"{c['label']}={c['sim']:.2f}×{c['weight']:.2f}" for c in comps)
        print(f"  {a}↔{b}  综合={score:.3f}  | {brk}")

    # 2) 建图：cites 边 + 关系边（单 similar 形态 vs 多关系形态）
    nodes = gk.build_paper_nodes(papers)
    edges = gk.cites_edges(papers)
    if mode == "faceted":
        edges += gk.faceted_edges(details, knn_k=knn_k, min_sim=0.3)
    else:
        edges += gk.similar_edges(pairs, details, knn_k=knn_k)

    by_kind: dict[str, int] = {}
    for e in edges:
        by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
    print(f"\n=== 图谱（mode={mode}, knn_k={knn_k}）: {len(nodes)} 节点 / {len(edges)} 边 ===")
    print("  边类型分布:", by_kind)

    # 3) 分析：PageRank 影响力 + Louvain 子方向
    stats = annotate_graph(nodes, edges)
    print(f"\n=== 分析: {stats['clusters']} 个子方向 ===")
    print("  关键论文(按影响力):")
    for tp in stats["top_papers"]:
        print(f"    {tp['importance']:.3f}  {tp['label']}")
    print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    ap.add_argument("--knn-k", type=int, default=8)
    ap.add_argument("--mode", choices=["similar", "faceted"], default="faceted",
                    help="similar=单条相似边(生产现状); faceted=多关系拆边(重设计目标)")
    args = ap.parse_args()
    asyncio.run(main(args.fixture, args.knn_k, args.mode))
