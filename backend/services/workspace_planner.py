"""工作区阅读路径与综述矩阵服务。"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Iterable

from sqlmodel import Session, select

from ..models.paper import Paper
from ..models.research import ResearchGraph
from ..models.schemas import (
    WorkspaceReadingPlanEdge,
    WorkspaceReadingPlanPaper,
    WorkspaceReadingPlanResponse,
    WorkspaceReadingPlanStage,
    WorkspaceSurveyCluster,
    WorkspaceSurveyDraftSection,
    WorkspaceSurveyMatrixRow,
    WorkspaceSurveyResponse,
)
from ..models.workspace import Workspace, WorkspacePaper
from .database import get_engine
from .paper_profile import PaperProfile, load_paper_profiles

_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "using", "based", "paper",
    "model", "models", "method", "methods", "approach", "learning", "network", "neural",
    "framework", "propose", "proposed", "towards", "toward", "via", "into", "over",
    "论文", "方法", "模型", "研究", "提出", "基于", "学习", "网络", "任务", "使用",
}

_READ_LEVEL_WEIGHT = {"full": 0.2, "digest": 0.1, "none": 0.0}
_DETAIL_ORDER = {"full": 0, "digest": 1, "metadata": 2}


def _paper_id(node_id: object) -> int | None:
    if not isinstance(node_id, str) or not node_id.startswith("paper:"):
        return None
    try:
        return int(node_id.split(":", 1)[1])
    except (TypeError, ValueError):
        return None


def _profile_title(profile: PaperProfile) -> str:
    return profile.title or f"Paper {profile.paper_id}"


def _short_text(text: str, *, limit: int = 96) -> str:
    value = re.sub(r"\s+", " ", str(text or "").strip())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _unique(items: Iterable[str], *, limit: int = 6) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = re.sub(r"\s+", " ", str(item or "").strip())
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def _tokens(text: str) -> list[str]:
    raw = str(text or "")
    english = [
        word.lower()
        for word in re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{2,}", raw)
        if word.lower() not in _STOPWORDS
    ]
    cjk = [raw[i : i + 2] for i in range(len(raw) - 1) if re.match(r"[\u4e00-\u9fff]{2}", raw[i : i + 2])]
    return english + [item for item in cjk if item not in _STOPWORDS]


def _top_terms(texts: Iterable[str], *, limit: int = 8) -> list[str]:
    counts: Counter[str] = Counter()
    for text in texts:
        counts.update(_tokens(text))
    return [token for token, _ in counts.most_common(limit)]


def _method_terms(profile: PaperProfile) -> list[str]:
    modules = [
        str(module.get("name") or "").strip()
        for module in profile.method_modules
        if isinstance(module, dict) and str(module.get("name") or "").strip()
    ]
    if modules:
        return _unique(modules, limit=4)
    return _top_terms([profile.method_text], limit=4)


def _code_concepts(profile: PaperProfile) -> list[str]:
    return _unique(
        str(item.get("concept") or "").strip()
        for item in profile.code_mapping
        if isinstance(item, dict)
    )


def _load_workspace_context(workspace_id: int) -> tuple[list[int], dict[int, PaperProfile], ResearchGraph | None, datetime]:
    engine = get_engine()
    with Session(engine) as session:
        ws = session.get(Workspace, workspace_id)
        if not ws:
            raise ValueError("workspace not found")

        rows = session.exec(
            select(Paper.id)
            .join(WorkspacePaper, WorkspacePaper.paper_id == Paper.id)
            .where(WorkspacePaper.workspace_id == workspace_id)
            .order_by(WorkspacePaper.added_at.asc())
        ).all()
        paper_ids = [int(pid) for pid in rows]
        profiles = load_paper_profiles(session, paper_ids)
        graph = session.exec(select(ResearchGraph).where(ResearchGraph.workspace_id == workspace_id)).first()
        updated_at = graph.updated_at if graph else ws.updated_at
    return paper_ids, profiles, graph, updated_at


def _graph_meta(graph: ResearchGraph | None) -> dict[int, dict]:
    if not graph:
        return {}
    meta: dict[int, dict] = {}
    for node in graph.nodes or []:
        if node.get("kind") != "paper":
            continue
        pid = _paper_id(node.get("id"))
        if pid is None:
            continue
        meta[pid] = node.get("meta", {}) if isinstance(node.get("meta"), dict) else {}
    return meta


def _score_profile(profile: PaperProfile, meta: dict) -> float:
    importance = float(meta.get("importance", 0.0) or 0.0)
    centrality = float(meta.get("centrality", 0.0) or 0.0)
    foundational = float(meta.get("foundational", 0.0) or 0.0)
    detail_bonus = _READ_LEVEL_WEIGHT.get(profile.read_level, 0.0)
    year_bonus = 0.05 if profile.year else 0.0
    score = 0.45 * importance + 0.20 * centrality + 0.20 * foundational + detail_bonus + year_bonus
    return round(min(1.0, max(0.0, score)), 4)


def _readable_status(profile: PaperProfile) -> str:
    if profile.read_level == "full":
        return "已精读"
    if profile.read_level == "digest":
        return "已速读"
    return "未解读"


def _reading_reason(profile: PaperProfile, score: float, dependents: int, meta: dict) -> str:
    parts: list[str] = []
    if dependents > 0:
        parts.append(f"支撑 {dependents} 篇后续论文")
    if profile.year:
        parts.append(f"{profile.year} 年")
    if score >= 0.55:
        parts.append("图谱权重高")
    if meta.get("cluster_label"):
        parts.append(str(meta["cluster_label"]))
    parts.append(_readable_status(profile))
    return " · ".join(_unique(parts, limit=4))


def _dependency_edges(
    graph: ResearchGraph | None,
    profiles: dict[int, PaperProfile],
) -> tuple[list[WorkspaceReadingPlanEdge], dict[int, set[int]], dict[int, set[int]]]:
    if not graph:
        return [], defaultdict(set), defaultdict(set)

    member_ids = set(profiles)
    edge_map: dict[tuple[int, int, str], WorkspaceReadingPlanEdge] = {}
    prereq_map: dict[int, set[int]] = defaultdict(set)
    dependent_map: dict[int, set[int]] = defaultdict(set)

    for edge in graph.edges or []:
        if edge.get("kind") != "cites":
            continue
        src = _paper_id(edge.get("source"))
        tgt = _paper_id(edge.get("target"))
        if src not in member_ids or tgt not in member_ids or src == tgt:
            continue

        src_year = profiles[src].year or 0
        tgt_year = profiles[tgt].year or 0
        if src_year and tgt_year and src_year < tgt_year:
            prereq, paper = src, tgt
        else:
            prereq, paper = tgt, src

        reason = "引用前置关系"
        weight = float(edge.get("weight", 1.0) or 1.0)
        key = (prereq, paper, reason)
        edge_map[key] = WorkspaceReadingPlanEdge(
            source_paper_id=prereq,
            target_paper_id=paper,
            kind="prerequisite",
            weight=round(weight, 4),
            reason=reason,
        )
        prereq_map[paper].add(prereq)
        dependent_map[prereq].add(paper)

    return list(edge_map.values()), prereq_map, dependent_map


def _target_closure(target_paper_id: int | None, profiles: dict[int, PaperProfile], prereq_map: dict[int, set[int]]) -> set[int]:
    if target_paper_id is None:
        return set(profiles)
    if target_paper_id not in profiles:
        raise ValueError("paper not in workspace")

    selected: set[int] = set()
    stack = [target_paper_id]
    while stack:
        pid = stack.pop()
        if pid in selected:
            continue
        selected.add(pid)
        stack.extend(prereq_map.get(pid, set()) - selected)
    return selected


def _sort_key(pid: int, profiles: dict[int, PaperProfile], scores: dict[int, float]) -> tuple[int, int, float, str]:
    profile = profiles[pid]
    return (
        profile.year or 9999,
        _DETAIL_ORDER.get(profile.detail_level, 3),
        -scores.get(pid, 0.0),
        _profile_title(profile).lower(),
    )


def _layered_order(
    selected: set[int],
    profiles: dict[int, PaperProfile],
    scores: dict[int, float],
    prereq_map: dict[int, set[int]],
) -> dict[int, int]:
    remaining = set(selected)
    prereqs = {pid: set(prereq_map.get(pid, set())) & selected for pid in selected}
    stages: dict[int, int] = {}
    stage = 1

    while remaining:
        ready = [pid for pid in remaining if not (prereqs.get(pid, set()) & remaining)]
        if not ready:
            ready = [min(remaining, key=lambda pid: _sort_key(pid, profiles, scores))]
        for pid in sorted(ready, key=lambda pid: _sort_key(pid, profiles, scores)):
            stages[pid] = stage
            remaining.remove(pid)
        stage += 1
    return stages


def _stage_label(index: int, total: int) -> tuple[str, str]:
    if total <= 1:
        return "阅读路径", "按年份、信息完整度与图谱权重排序"
    if index == 1:
        return "基础论文", "先建立概念与引用前置"
    if index == total:
        return "目标与前沿", "阅读目标论文和高权重后续工作"
    return "承接论文", "连接基础方法与当前工作"


def build_workspace_reading_plan(
    workspace_id: int,
    *,
    target_paper_id: int | None = None,
) -> WorkspaceReadingPlanResponse:
    paper_ids, profiles, graph, updated_at = _load_workspace_context(workspace_id)
    meta = _graph_meta(graph)
    graph_available = bool(graph and graph.nodes)
    scores = {pid: _score_profile(profile, meta.get(pid, {})) for pid, profile in profiles.items()}
    edges, prereq_map, dependent_map = _dependency_edges(graph, profiles)
    selected = _target_closure(target_paper_id, profiles, prereq_map)

    if not graph_available or not edges:
        selected = set(profiles)
    stages_by_pid = _layered_order(selected, profiles, scores, prereq_map)
    total_stages = max(stages_by_pid.values(), default=0)
    stage_papers: dict[int, list[WorkspaceReadingPlanPaper]] = defaultdict(list)

    for pid in sorted(selected, key=lambda item: (stages_by_pid[item], _sort_key(item, profiles, scores))):
        profile = profiles[pid]
        paper_meta = meta.get(pid, {})
        paper = WorkspaceReadingPlanPaper(
            paper_id=pid,
            title=_profile_title(profile),
            venue=profile.venue or None,
            year=profile.year,
            source=profile.source,
            read_level=profile.read_level,
            detail_level=profile.detail_level,
            cluster_label=paper_meta.get("cluster_label"),
            stage=stages_by_pid[pid],
            score=scores.get(pid, 0.0),
            prerequisites=sorted(prereq_map.get(pid, set()) & selected),
            reason=_reading_reason(profile, scores.get(pid, 0.0), len(dependent_map.get(pid, set()) & selected), paper_meta),
        )
        stage_papers[paper.stage].append(paper)

    stages = []
    for stage_index in sorted(stage_papers):
        label, objective = _stage_label(stage_index, total_stages)
        stages.append(
            WorkspaceReadingPlanStage(
                stage=stage_index,
                label=label,
                objective=objective,
                papers=stage_papers[stage_index],
            )
        )

    selected_edges = [
        edge for edge in edges
        if edge.source_paper_id in selected and edge.target_paper_id in selected
    ]
    read_counts = Counter(profiles[pid].read_level for pid in selected)
    coverage = {
        "paper_count": len(selected),
        "total_workspace_papers": len(paper_ids),
        "full": read_counts.get("full", 0),
        "digest": read_counts.get("digest", 0),
        "none": read_counts.get("none", 0),
        "citation_edges": len(selected_edges),
        "graph_nodes": len(graph.nodes or []) if graph else 0,
    }
    strategy = "target-prerequisite" if target_paper_id else "workspace-curriculum"
    if not graph_available:
        strategy = f"{strategy}-metadata"

    return WorkspaceReadingPlanResponse(
        workspace_id=workspace_id,
        target_paper_id=target_paper_id,
        graph_available=graph_available,
        strategy=strategy,
        coverage=coverage,
        stages=stages,
        edges=selected_edges,
        updated_at=updated_at,
    )


def _matrix_row(profile: PaperProfile) -> WorkspaceSurveyMatrixRow:
    method = profile.method_overview or profile.method_pipeline or "、".join(_method_terms(profile))
    task = profile.problem or profile.one_sentence or profile.summary
    return WorkspaceSurveyMatrixRow(
        paper_id=profile.paper_id,
        title=_profile_title(profile),
        venue=profile.venue or None,
        year=profile.year,
        detail_level=profile.detail_level,
        task=_short_text(task),
        method=_short_text(method),
        datasets=_unique(profile.datasets, limit=5),
        baselines=_unique(profile.baselines, limit=5),
        contributions=_unique(profile.key_contributions, limit=3),
        limitations=_short_text(profile.limitations, limit=120),
        code_concepts=_code_concepts(profile)[:5],
    )


def _cluster_groups(profiles: dict[int, PaperProfile], graph: ResearchGraph | None) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = defaultdict(list)
    meta = _graph_meta(graph)
    for pid, profile in profiles.items():
        label = meta.get(pid, {}).get("cluster_label")
        if not label:
            if profile.year:
                decade = profile.year - profile.year % 10
                label = f"{decade}s"
            else:
                label = "未分组"
        groups[str(label)].append(pid)
    return groups


def _survey_clusters(profiles: dict[int, PaperProfile], graph: ResearchGraph | None) -> list[WorkspaceSurveyCluster]:
    clusters: list[WorkspaceSurveyCluster] = []
    for label, ids in sorted(_cluster_groups(profiles, graph).items(), key=lambda item: (-len(item[1]), item[0])):
        cluster_profiles = [profiles[pid] for pid in ids]
        years = sorted({p.year for p in cluster_profiles if p.year})
        methods = _top_terms((p.method_text for p in cluster_profiles), limit=5)
        datasets = _unique((dataset for p in cluster_profiles for dataset in p.datasets), limit=5)
        titles = "、".join(_profile_title(p) for p in cluster_profiles[:2])
        if methods:
            summary = f"{label} 包含 {len(ids)} 篇论文，方法关键词集中在 {'、'.join(methods[:3])}。"
        elif titles:
            summary = f"{label} 包含 {len(ids)} 篇论文，代表论文包括 {titles}。"
        else:
            summary = f"{label} 包含 {len(ids)} 篇论文。"
        clusters.append(
            WorkspaceSurveyCluster(
                label=label,
                paper_count=len(ids),
                paper_ids=ids,
                years=years,
                common_methods=methods,
                datasets=datasets,
                summary=summary,
            )
        )
    return clusters


def _draft_sections(
    profiles: dict[int, PaperProfile],
    topics: list[str],
    datasets: list[str],
    baselines: list[str],
    clusters: list[WorkspaceSurveyCluster],
) -> list[WorkspaceSurveyDraftSection]:
    years = sorted({p.year for p in profiles.values() if p.year})
    year_span = f"{years[0]}-{years[-1]}" if len(years) >= 2 else (str(years[0]) if years else "未知年份")
    cluster_text = "、".join(cluster.label for cluster in clusters[:3]) or "未形成明显子方向"
    topic_text = "、".join(topics[:5]) or "主题信息不足"
    dataset_text = "、".join(datasets[:5]) or "数据集信息不足"
    baseline_text = "、".join(baselines[:5]) or "基线信息不足"
    limitation_terms = _top_terms((p.limitations for p in profiles.values()), limit=5)
    limitation_text = "、".join(limitation_terms) or "局限性信息不足"

    return [
        WorkspaceSurveyDraftSection(
            key="overview",
            title="研究脉络",
            content=f"工作区包含 {len(profiles)} 篇论文，时间跨度为 {year_span}，主要覆盖 {cluster_text}。高频主题包括 {topic_text}。",
        ),
        WorkspaceSurveyDraftSection(
            key="methods",
            title="方法分类",
            content=f"方法侧重点主要体现在 {topic_text}。可按图谱子方向组织为 {cluster_text}。",
        ),
        WorkspaceSurveyDraftSection(
            key="experiments",
            title="实验对比",
            content=f"实验数据集集中在 {dataset_text}，常见对比基线包括 {baseline_text}。",
        ),
        WorkspaceSurveyDraftSection(
            key="limitations",
            title="局限性",
            content=f"现有工作暴露出的限制主要关联 {limitation_text}，后续阅读可优先查看矩阵中贡献与局限互补的论文。",
        ),
    ]


def build_workspace_survey(workspace_id: int) -> WorkspaceSurveyResponse:
    _, profiles, graph, updated_at = _load_workspace_context(workspace_id)
    ordered_profiles = sorted(
        profiles.values(),
        key=lambda p: (p.year or 9999, _DETAIL_ORDER.get(p.detail_level, 3), _profile_title(p).lower()),
    )
    matrix = [_matrix_row(profile) for profile in ordered_profiles]
    topics = _top_terms((profile.purpose_text + " " + profile.method_text for profile in ordered_profiles), limit=10)
    datasets = _unique((dataset for profile in ordered_profiles for dataset in profile.datasets), limit=10)
    baselines = _unique((baseline for profile in ordered_profiles for baseline in profile.baselines), limit=10)
    detail_counts = Counter(profile.detail_level for profile in ordered_profiles)
    clusters = _survey_clusters(profiles, graph)
    draft_sections = _draft_sections(profiles, topics, datasets, baselines, clusters)

    return WorkspaceSurveyResponse(
        workspace_id=workspace_id,
        paper_count=len(profiles),
        detail_counts=dict(detail_counts),
        topics=topics,
        datasets=datasets,
        baselines=baselines,
        matrix=matrix,
        clusters=clusters,
        draft_sections=draft_sections,
        updated_at=updated_at,
    )
