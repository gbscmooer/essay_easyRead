"""
structural — 引用结构相似（纯函数，无 DB）

两个经典文献计量量，都是**细分方向的强信号**：同一个小方向的论文往往引同一批文献
（文献耦合），也往往被同一批后续工作一起引用（共引）。它们与语义向量互补：向量看
"讲得像不像"，结构看"跟谁站在一起"。

- **文献耦合 bibliographic coupling**：A、B 共同引用的文献数（Salton 归一）。
  被引方**不要求在工作区内**——用 doi / arxiv / 标题键匹配，因此叶子论文也算得出。
- **共引 co-citation**：同时引用 A 和 B 的论文数（Salton 归一）。只在工作区内的
  citing 论文上统计。

两者都返回 [(a, b, salton, shared_count)]，a < b。
"""

from __future__ import annotations

from collections import defaultdict
from types import SimpleNamespace
from typing import Iterable


def ref_target_key(ref) -> str | int | None:
    """引用目标的规范键：工作区内 paper_id 优先，否则 doi / arxiv / 标题。"""
    if getattr(ref, "cited_paper_id", None):
        return int(ref.cited_paper_id)
    if getattr(ref, "cited_doi", None):
        return f"doi:{ref.cited_doi}"
    if getattr(ref, "cited_arxiv_id", None):
        return f"arxiv:{ref.cited_arxiv_id}"
    title = (getattr(ref, "cited_title", None) or "").strip().lower()
    return f"title:{title}" if title else None


def build_cites_map(references: Iterable, paper_ids: set[int]) -> dict[int, set[str | int]]:
    """citing_paper_id → 其引用的目标键集合。"""
    cites_map: dict[int, set[str | int]] = {pid: set() for pid in paper_ids}
    for ref in references:
        citing_id = int(ref.citing_paper_id)
        if citing_id not in paper_ids:
            continue
        key = ref_target_key(ref)
        if key is not None:
            cites_map.setdefault(citing_id, set()).add(key)
    return cites_map


def compute_bibliographic_coupling_pairs(
    cites_map: dict[int, set[str | int]],
    *,
    min_shared: int = 1,
) -> list[tuple[int, int, float, int]]:
    """文献耦合对 [(a, b, salton, shared_count)]，a < b。"""
    ref_to_papers: dict[str | int, set[int]] = defaultdict(set)
    for pid, refs in cites_map.items():
        for rk in refs:
            ref_to_papers[rk].add(pid)

    pair_shared: dict[tuple[int, int], int] = defaultdict(int)
    for _rk, papers in ref_to_papers.items():
        plist = sorted(papers)
        for i in range(len(plist)):
            for j in range(i + 1, len(plist)):
                pair_shared[(plist[i], plist[j])] += 1

    out: list[tuple[int, int, float, int]] = []
    for (a, b), shared in pair_shared.items():
        if shared < min_shared:
            continue
        refs_a = len(cites_map.get(a, set()))
        refs_b = len(cites_map.get(b, set()))
        if refs_a == 0 or refs_b == 0:
            continue
        salton = shared / ((refs_a * refs_b) ** 0.5)
        out.append((a, b, round(float(salton), 6), shared))
    return out


def build_cited_by_map(references: Iterable, paper_ids: set[int]) -> dict[int, set[int]]:
    """cited_paper_id → 引用它的 citing_paper_id 集合（两端都须在工作区内）。"""
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
    """共引对 [(a, b, salton, shared_citer_count)]，a < b。"""
    citer_to_cited: dict[int, set[int]] = defaultdict(set)
    for cited_id, citers in cited_by_map.items():
        for citer in citers:
            citer_to_cited[citer].add(cited_id)

    pair_shared: dict[tuple[int, int], int] = defaultdict(int)
    for _citer, cited_set in citer_to_cited.items():
        cited_list = sorted(cited_set & paper_ids)
        for i in range(len(cited_list)):
            for j in range(i + 1, len(cited_list)):
                pair_shared[(cited_list[i], cited_list[j])] += 1

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
    """基于 cites 边列表近似共引（评测 / 引用数据不足时的回退）。"""
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


def pair_map(pairs: list[tuple[int, int, float, int]]) -> dict[tuple[int, int], tuple[float, int]]:
    """[(a,b,w,shared)] → {(min,max): (w, shared)}，供 relatedness 当通道输入。"""
    return {(min(a, b), max(a, b)): (w, shared) for a, b, w, shared in pairs}
