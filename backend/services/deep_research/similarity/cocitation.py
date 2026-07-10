"""
Co-citation 共引相似度

两篇论文 A、B 被同一篇 citing 论文共同引用时构成共引对。
权重采用 Salton 系数：|共同引用者| / sqrt(|引用 A 的论文数| * |引用 B 的论文数|)，
共享引用者越多权重单调不减（分母固定时）。

若工作区内 citing 数据不足，可基于 cites 图近似：citing → {cited} 反转得 cited_by。
"""

from __future__ import annotations

from collections import defaultdict
from types import SimpleNamespace
from typing import Iterable


def _ref_key(ref) -> str | int | None:
    """将引用目标规范为可比较键（工作区内 paper_id 或外部标识）。"""
    if getattr(ref, "cited_paper_id", None):
        return int(ref.cited_paper_id)
    if getattr(ref, "cited_doi", None):
        return f"doi:{ref.cited_doi}"
    if getattr(ref, "cited_arxiv_id", None):
        return f"arxiv:{ref.cited_arxiv_id}"
    title = (getattr(ref, "cited_title", None) or "").strip().lower()
    return f"title:{title}" if title else None


def build_cited_by_map(
    references: Iterable,
    paper_ids: set[int],
) -> dict[int, set[int]]:
    """cited_paper_id → 引用它的 citing_paper_id 集合（仅工作区内节点）。"""
    cited_by: dict[int, set[int]] = defaultdict(set)
    for ref in references:
        citing_id = int(ref.citing_paper_id)
        cited_id = getattr(ref, "cited_paper_id", None)
        if citing_id not in paper_ids or not cited_id or cited_id not in paper_ids:
            continue
        cited_by[int(cited_id)].add(citing_id)
    return dict(cited_by)


def compute_co_citation_pairs(
    cited_by_map: dict[int, set[int]],
    paper_ids: set[int],
    *,
    min_shared: int = 1,
) -> list[tuple[int, int, float, int]]:
    """
    计算工作区内论文对的共引强度。

    Returns:
        [(paper_a, paper_b, salton_weight, shared_citer_count)]，a < b。
    """
    # citing_paper -> set of cited papers (for co-citation via common citer)
    citer_to_cited: dict[int, set[int]] = defaultdict(set)
    for cited_id, citers in cited_by_map.items():
        for citer in citers:
            citer_to_cited[citer].add(cited_id)

    pair_shared: dict[tuple[int, int], int] = defaultdict(int)
    for _citer, cited_set in citer_to_cited.items():
        cited_list = sorted(cited_set & paper_ids)
        for i in range(len(cited_list)):
            for j in range(i + 1, len(cited_list)):
                a, b = cited_list[i], cited_list[j]
                pair_shared[(a, b)] += 1

    out: list[tuple[int, int, float, int]] = []
    for (a, b), shared in pair_shared.items():
        if shared < min_shared:
            continue
        citers_a = len(cited_by_map.get(a, set()))
        citers_b = len(cited_by_map.get(b, set()))
        if citers_a == 0 or citers_b == 0:
            continue
        salton = shared / ((citers_a * citers_b) ** 0.5)
        out.append((a, b, round(float(salton), 6), shared))
    return out


def co_citation_from_cites_edges(
    cite_edges: list[dict],
    paper_ids: set[int],
    *,
    min_shared: int = 1,
) -> list[tuple[int, int, float, int]]:
    """
    基于 cites 边列表近似共引（测试/数据不足时的回退）。

    cite_edges: kind=cites, source=paper:{id}, target=paper:{id}
    """
    references = []
    for e in cite_edges:
        if e.get("kind") != "cites":
            continue
        try:
            citing = int(str(e["source"]).split(":")[1])
            cited = int(str(e["target"]).split(":")[1])
        except (IndexError, ValueError, KeyError, TypeError):
            continue
        references.append(SimpleNamespace(citing_paper_id=citing, cited_paper_id=cited))
    cited_by = build_cited_by_map(references, paper_ids)
    return compute_co_citation_pairs(cited_by, paper_ids, min_shared=min_shared)
