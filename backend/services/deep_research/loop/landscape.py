"""领域概览（纯算法，不调模型）：引用富化、计量、相似度聚类、图谱。

输入是循环选中的论文（不是全部候选），输出写入 result.metrics / result.graph，前端总览视图直接使用。
所有步骤非致命：失败只缺字段，不影响作答。
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter, defaultdict

import networkx as nx
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from ..citation_metrics import annotate_citation_metrics
from ..graph_analytics import _detect_communities, _label_clusters, _tokenize
from ..graphkit.backbone import mutual_knn
from ..literature_retrieval import paper_similarity

logger = logging.getLogger(__name__)

_RECENT_WINDOW = 2          # 「近期」= 最近 N 年（相对当前年份）
_ENRICH_CONCURRENCY = 8
_ENRICH_PAPER_CAP = 200
_SERIAL_CHUNK = 20


async def enrich_papers(papers: list[dict], *, progress=None, history_cap: int | None = None) -> dict:
    """就地富化论文列表（全部非致命：任何一步失败只缺字段，不阻塞探索）。

    history_cap 只限制逐篇引用历史请求的数量预算（按传入顺序取前 N 篇缺历史的），
    期刊批量富化与 venue 字段投影始终覆盖全部论文——预算内敛到子步，
    避免外层裁剪输入时把已有历史的论文连带排除在期刊富化之外。
    """
    stats = {"history_matched": 0, "venue_metrics": 0}
    if not papers:
        return stats

    # 1) OpenAlex 按 DOI 逐篇补：逐年引用 / 摘要 / 关键词 / 期刊身份
    #    history 请求预算按传入顺序只花在缺历史的论文上（预算外的不发请求，
    #    但不缺席第 2 步的期刊批量富化与字段投影）
    from ..providers.openalex import get_shared_openalex_provider

    oa = get_shared_openalex_provider()
    sem = asyncio.Semaphore(_ENRICH_CONCURRENCY)
    done_count = 0
    history_budget = [history_cap] if history_cap is not None else [None]

    async def _one(p: dict) -> None:
        nonlocal done_count
        doi = p.get("doi")
        if not doi or p.get("counts_by_year"):
            return
        async with sem:
            # Reserve synchronously before the first await; failed requests also consume a slot.
            if history_budget[0] is not None:
                if history_budget[0] <= 0:
                    return
                history_budget[0] -= 1
            try:
                hist = await oa.get_citation_history(doi)
            except Exception as e:
                logger.debug(f"enrich history failed for {doi}: {e}")
                return
        if not hist:
            return
        p["counts_by_year"] = hist.get("counts_by_year") or []
        stats["history_matched"] += 1
        if not p.get("abstract") and hist.get("abstract"):
            p["abstract"] = hist["abstract"]
        if not p.get("keywords") and hist.get("keywords"):
            p["keywords"] = hist["keywords"]
        for field in ("venue_name", "venue_issn", "pdf_url", "arxiv_id"):
            if not p.get(field) and hist.get(field):
                p[field] = hist[field]
        done_count += 1
        if progress and done_count % 25 == 0:
            try:
                await progress(done=done_count)
            except Exception:
                pass

    await asyncio.gather(*[_one(p) for p in papers])

    # 2) 期刊级指标：Scopus source-id 批量（Scopus 侧身份），OpenAlex-only 的期刊按 ISSN 补
    source_ids = sorted({p["venue_source_id"] for p in papers if p.get("venue_source_id")})
    issns = sorted({
        p["venue_issn"] for p in papers
        if p.get("venue_issn") and not p.get("venue_source_id")
    })
    serial_map: dict[str, dict] = {}
    if source_ids or issns:
        from ...ai_config import get_research_config
        from ..providers import _elsevier_key
        from ..providers.scopus import ScopusProvider

        key = _elsevier_key(dict(get_research_config()))
        if key:
            scopus = ScopusProvider(api_key=key)
            try:
                serial_map.update(await scopus.get_serial_metrics(source_ids))
                for i in range(0, len(issns), _SERIAL_CHUNK):
                    chunk = ",".join(issns[i:i + _SERIAL_CHUNK])
                    data = await scopus._get("/content/serial/title", {"issn": chunk})
                    entries = [e for e in (data or {}).get("serial-metadata-response", {}).get("entry") or []
                               if isinstance(e, dict)]
                    for e in entries:
                        parsed = ScopusProvider._parse_serial_entries([e])
                        m = next(iter(parsed.values()), None) or {
                            "name": e.get("dc:title") or "", "sjr": None, "snip": None, "citescore": None,
                        }
                        # ISSN 查询的条目要能被论文侧的 venue_issn 命中：prism:issn / eIssn 都登记
                        for issn_resp in (e.get("prism:issn"), e.get("prism:eIssn")):
                            if issn_resp:
                                serial_map.setdefault(str(issn_resp), m)
            except Exception as e:
                logger.warning(f"enrich serial metrics failed (non-fatal): {e}")
            finally:
                await scopus.aclose()

    for p in papers:
        vid = p.get("venue_source_id") or p.get("venue_issn")
        m = serial_map.get(str(vid) if vid else "", None)
        name = p.get("venue_name") or (m or {}).get("name") or None
        if not name and not m:
            continue
        if m:
            stats["venue_metrics"] += 1
        p["venue"] = {
            "name": name,
            "sjr": (m or {}).get("sjr"),
            "snip": (m or {}).get("snip"),
            "citescore": (m or {}).get("citescore"),
        }
    return stats




def _venue_ranking(papers: list[dict]) -> list[dict]:
    """主阵地期刊：语料内论文数 ≥2 的期刊，按篇数排序（带 SJR/SNIP）。"""
    by_venue: dict[str, dict] = {}
    for p in papers:
        v = p.get("venue") or {}
        name = (v.get("name") or "").strip()
        if not name:
            continue
        row = by_venue.setdefault(name, {"name": name, "count": 0,
                                         "sjr": v.get("sjr"), "snip": v.get("snip"),
                                         "citescore": v.get("citescore")})
        row["count"] += 1
    ranked = sorted(by_venue.values(), key=lambda r: r["count"], reverse=True)
    return [r for r in ranked if r["count"] >= 2][:8]


def _keyword_trend(papers: list[dict], *, now_year: int) -> dict:
    """关键词 × 年份矩阵（热力图原料）：Top 关键词在每年新出现的论文数。"""
    kw_years: dict[str, Counter] = defaultdict(Counter)
    years_seen: set[int] = set()
    for p in papers:
        y = p.get("year")
        if not isinstance(y, int) or y <= 1900:
            continue
        years_seen.add(y)
        for kw in p.get("keywords") or []:
            if isinstance(kw, str) and kw.strip():
                kw_years[kw.strip()][y] += 1
    if not years_seen:
        return {"years": [], "rows": []}
    years = [y for y in range(max(min(years_seen), now_year - 11), max(years_seen) + 1)]
    rows = []
    for kw, yc in kw_years.items():
        total = sum(yc.values())
        if total < 3:
            continue
        rows.append({"keyword": kw, "total": total,
                     "counts": [yc.get(y) or 0 for y in years]})
    rows.sort(key=lambda r: r["total"], reverse=True)
    return {"years": years, "rows": rows[:20]}


def compute_metrics(papers: list[dict], *, now_year: int) -> dict:
    """Descriptive counts plus exact rate comparison over complete calendar years."""
    annotate_citation_metrics(papers, now_year=now_year)
    known = [p["year"] for p in papers if isinstance(p.get("year"), int) and 1900 < p["year"] <= now_year]
    by_year = dict(sorted(Counter(y for y in known if y > 1900).items()))
    recent_n = sum(1 for y in known if y >= now_year - _RECENT_WINDOW)
    top_cited = [p["key"] for p in sorted(papers, key=lambda x: x.get("citation_count") or 0, reverse=True)[:10]]
    rising = [
        p["key"]
        for p in sorted(
            (x for x in papers if x.get("trajectory") == "rising"),
            key=lambda x: x.get("citation_velocity_2y") or 0, reverse=True,
        )[:10]
    ]
    highly_cited = [
        p["key"]
        for p in sorted(
            (x for x in papers if x.get("highly_cited")),
            key=lambda x: (x.get("citation_percentile") or 0, x.get("citation_count") or 0),
            reverse=True,
        )[:10]
    ]
    return {
        "total": len(papers),
        "citation_method": {
            "algorithm": "conditional_poisson_rate_test", "version": 1,
            "adjustment": "benjamini_hochberg", "alpha": 0.05,
            "recent_years": [now_year - 2, now_year - 1],
            "baseline_years": [now_year - 4, now_year - 3],
        },
        "by_year": by_year,
        "recent_share": round(recent_n / len(known), 3) if known else 0.0,
        "year_span": [min(known), max(known)] if known else [],
        "top_cited": top_cited,
        "rising": rising,
        "highly_cited": highly_cited,
        "venue_ranking": _venue_ranking(papers),
        "keyword_trend": _keyword_trend(papers, now_year=now_year),
        "enriched": {
            "with_history": sum(1 for p in papers if p.get("counts_by_year")),
            "with_venue_metrics": sum(1 for p in papers if (p.get("venue") or {}).get("sjr") is not None),
        },
    }


def cluster_papers(
    papers: list[dict], *, min_sim: float = 0.0, max_neighbors: int = 10,
    similarities: np.ndarray | None = None,
) -> tuple[dict[str, int], list[dict], float]:
    """Mutual-kNN similarity graph and Leiden. TF-IDF is an explicit offline fallback."""
    if not papers:
        return {}, [], 0.0

    if similarities is None:
        try:
            matrix = TfidfVectorizer(tokenizer=_tokenize, token_pattern=None, lowercase=False).fit_transform(
                [f'{p["title"]} {p.get("abstract") or ""}' for p in papers])
            similarities = (matrix @ matrix.T).toarray()
        except ValueError:
            similarities = np.zeros((len(papers), len(papers)))
    if similarities.shape != (len(papers), len(papers)) or not np.isfinite(similarities).all():
        raise ValueError("invalid paper similarity matrix")
    sims: list[tuple[int, int, float]] = []
    for i in range(len(papers)):
        for j in range(i + 1, len(papers)):
            s = float(similarities[i, j])
            if s > min_sim:
                sims.append((i, j, s))

    neighbors = mutual_knn(sims, k=max_neighbors, min_sim=min_sim)
    keep = {(i, j) for i, j, _ in neighbors}
    rel = nx.Graph()
    rel.add_nodes_from(range(len(papers)))
    for i, j, similarity in neighbors:
        rel.add_edge(i, j, weight=similarity)

    communities = _detect_communities(rel)
    node2cluster: dict[int, int] = {}
    key2cluster: dict[str, int] = {}
    for cid, members in enumerate(communities):
        for idx in members:
            cluster = -1 if len(members) <= 1 else cid
            node2cluster[idx] = cluster
            key2cluster[papers[idx]["key"]] = cluster

    labels = _label_clusters({i: papers[i]["title"] for i in range(len(papers))}, communities)
    for p in papers:
        c = key2cluster.get(p["key"], -1)
        p["cluster"] = c
        p["cluster_label"] = labels.get(c, "") if c >= 0 else ""

    try:
        modularity = (
            round(float(nx.community.modularity(rel, communities, weight="weight")), 4)
            if communities and rel.number_of_edges() else 0.0
        )
    except Exception:
        modularity = 0.0

    edges = [
        {"source": papers[i]["key"], "target": papers[j]["key"], "kind": "related", "weight": round(s, 4)}
        for i, j, s in sims if (i, j) in keep
    ]
    return key2cluster, edges, modularity


def build_graph_payload(papers: list[dict], edges: list[dict], *, modularity: float) -> dict:
    """组装 ResearchGraph 形状的图谱（前端 DomainOverview 直接复用）。"""
    max_cites = max((p.get("citation_count") or 0 for p in papers), default=0) or 1
    node_ids = {p["key"]: f"x{i}" for i, p in enumerate(papers)}
    nodes = []
    for p in papers:
        meta = {
            "year": p.get("year"),
            "citation_count": p.get("citation_count") or 0,
            "cluster": p.get("cluster", -1),
            "source": "expanded",
            "importance": round((p.get("citation_count") or 0) / max_cites, 4),
        }
        if p.get("cluster_label"):
            meta["cluster_label"] = p["cluster_label"]
        if p.get("doi"):
            meta["external_id"] = p["doi"]
        nodes.append({"id": node_ids[p["key"]], "kind": "paper", "label": p["title"], "meta": meta})
    graph_edges = [
        {"source": node_ids[e["source"]], "target": node_ids[e["target"]], "kind": "related", "weight": e["weight"]}
        for e in edges
    ]
    clusters = len({p.get("cluster") for p in papers if p.get("cluster", -1) >= 0})
    return {
        "nodes": nodes,
        "edges": graph_edges,
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(graph_edges),
            "by_kind": {"related": len(graph_edges)},
            "clusters": clusters,
            "modularity": modularity,
        },
    }


async def build_landscape(papers: list[dict], *, now_year: int, enrich: bool = True) -> dict:
    """富化 → 计量 → 聚类 → 图谱；就地给论文写 cluster/cluster_label 等字段。返回 {metrics, graph, enrich}."""
    stats: dict = {}
    if enrich and papers:
        try:
            stats = await enrich_papers(papers, history_cap=_ENRICH_PAPER_CAP)
        except Exception as exc:  # noqa: BLE001
            logger.warning("landscape enrich failed (non-fatal): %s", exc)
            stats = {"error": type(exc).__name__}
    metrics = compute_metrics(papers, now_year=now_year)
    try:
        similarities, representation = await paper_similarity(papers)
    except Exception as exc:  # noqa: BLE001 —— 嵌入不可用时退回 TF-IDF
        logger.warning("paper similarity failed, using TF-IDF: %s", exc)
        similarities, representation = None, {"representation": "tfidf"}
    _, edges, modularity = cluster_papers(papers, similarities=similarities)
    metrics["clustering"] = {**representation, "algorithm": "leiden", "graph": "mutual_knn", "neighbors": 10}
    metrics["clusters"] = len({p.get("cluster") for p in papers if p.get("cluster", -1) >= 0})
    metrics["modularity"] = modularity
    return {"metrics": metrics, "graph": build_graph_payload(papers, edges, modularity=modularity), "enrich": stats}
