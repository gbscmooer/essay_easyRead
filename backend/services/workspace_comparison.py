"""工作区论文对比服务。"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher

from sqlmodel import Session, select

from ..models.paper import Paper
from ..models.research import ResearchGraph
from ..models.schemas import (
    WorkspaceComparisonOrder,
    WorkspaceComparisonPaper,
    WorkspaceComparisonResponse,
    WorkspaceComparisonSection,
)
from ..models.workspace import Workspace, WorkspacePaper
from .database import get_engine
from .paper_profile import load_paper_profiles

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "for", "on", "with", "and", "or", "is", "are", "this",
    "that", "we", "our", "by", "from", "at", "as", "be", "using", "via", "based", "method",
    "model", "models", "paper", "study", "novel", "new", "framework", "system",
    "其", "与", "和", "在", "对", "的", "了", "为", "基于", "本文", "我们", "提出",
}

_SECTION_ORDER = ["purpose", "method", "experiments", "implementation", "metadata"]
_SECTION_LABELS = {
    "purpose": "研究问题",
    "method": "方法",
    "experiments": "实验设置",
    "implementation": "代码映射",
    "metadata": "元信息",
}
_SECTION_WEIGHTS = {
    "purpose": 0.30,
    "method": 0.30,
    "experiments": 0.20,
    "implementation": 0.10,
    "metadata": 0.10,
}


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _compact_text(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(text or "").lower())


def _tokenize(text: str) -> list[str]:
    raw = str(text or "")
    tokens = [w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9\-]+", raw.lower()) if len(w) > 2 and w not in _STOPWORDS]
    cjk = re.findall(r"[\u4e00-\u9fff]", raw)
    tokens.extend(cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1))
    return tokens


def _sequence_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, _compact_text(left), _compact_text(right)).ratio()


def _token_jaccard(left: str, right: str) -> float:
    left_tokens = set(_tokenize(left))
    right_tokens = set(_tokenize(right))
    if not left_tokens or not right_tokens:
        return 0.0
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 0.0


def _text_similarity(left: str, right: str) -> float:
    return round(0.55 * _sequence_similarity(left, right) + 0.45 * _token_jaccard(left, right), 4)


def _list_jaccard(left: list[str], right: list[str]) -> float:
    left_norm = {_normalize_text(item) for item in left if _normalize_text(item)}
    right_norm = {_normalize_text(item) for item in right if _normalize_text(item)}
    if not left_norm or not right_norm:
        return 0.0
    union = left_norm | right_norm
    return len(left_norm & right_norm) / len(union) if union else 0.0


def _shared_items(left: list[str], right: list[str], *, limit: int = 4, threshold: float = 0.45) -> list[str]:
    shared: list[str] = []
    right_norm = [_normalize_text(item) for item in right if _normalize_text(item)]
    for item in left:
        norm = _normalize_text(item)
        if not norm:
            continue
        best = max((_text_similarity(norm, candidate) for candidate in right_norm), default=0.0)
        if best >= threshold and item not in shared:
            shared.append(str(item).strip())
        if len(shared) >= limit:
            break
    return shared


def _unique_items(source: list[str], other: list[str], *, limit: int = 3, threshold: float = 0.35) -> list[str]:
    unique: list[str] = []
    other_norm = [_normalize_text(item) for item in other if _normalize_text(item)]
    for item in source:
        norm = _normalize_text(item)
        if not norm:
            continue
        best = max((_text_similarity(norm, candidate) for candidate in other_norm), default=0.0)
        if best < threshold:
            unique.append(str(item).strip())
        if len(unique) >= limit:
            break
    return unique


def _top_terms(left: str, right: str, *, limit: int = 5) -> list[str]:
    left_tokens = _tokenize(left)
    right_tokens = _tokenize(right)
    if not left_tokens or not right_tokens:
        return []
    left_counts = Counter(left_tokens)
    right_counts = Counter(right_tokens)
    common = left_counts.keys() & right_counts.keys()
    scored = sorted(
        common,
        key=lambda token: (
            left_counts[token] + right_counts[token],
            len(token),
        ),
        reverse=True,
    )
    return scored[:limit]


def _build_summary(label: str, shared: list[str], unique_a: list[str], unique_b: list[str], score: float) -> str:
    if shared:
        shared_text = "、".join(shared[:2])
    else:
        shared_text = "重合较少"
    left_text = "、".join(unique_a[:1]) if unique_a else "更偏向左侧论文"
    right_text = "、".join(unique_b[:1]) if unique_b else "更偏向右侧论文"
    if score >= 0.7:
        prefix = f"{label}高度一致"
    elif score >= 0.45:
        prefix = f"{label}部分重合"
    else:
        prefix = f"{label}差异明显"
    return f"{prefix}：共同点是 {shared_text}；左侧更强调 {left_text}，右侧更强调 {right_text}。"


def _paper_payload(profile) -> WorkspaceComparisonPaper:
    return WorkspaceComparisonPaper(
        paper_id=profile.paper_id,
        title=profile.title,
        venue=profile.venue or None,
        year=profile.year,
        source=profile.source,
        read_level=profile.read_level,
        detail_level=profile.detail_level,
    )


def _graph_context(graph: ResearchGraph | None, paper_a_id: int, paper_b_id: int) -> tuple[bool, str | None, list[str]]:
    if not graph or not graph.nodes:
        return False, None, []

    node_map = {node.get("id"): node for node in graph.nodes if node.get("kind") == "paper"}
    node_a = node_map.get(f"paper:{paper_a_id}")
    node_b = node_map.get(f"paper:{paper_b_id}")
    same_cluster = False
    cluster_label = None
    if node_a and node_b:
        def cluster_id(node: dict) -> int:
            value = node.get("meta", {}).get("cluster", -1)
            if value is None:
                return -1
            try:
                return int(value)
            except (TypeError, ValueError):
                return -1

        cluster_a = cluster_id(node_a)
        cluster_b = cluster_id(node_b)
        same_cluster = cluster_a >= 0 and cluster_a == cluster_b
        if same_cluster:
            cluster_label = node_a.get("meta", {}).get("cluster_label") or node_b.get("meta", {}).get("cluster_label")

    direct_edge_kinds = sorted({
        str(edge.get("kind"))
        for edge in (graph.edges or [])
        if {
            edge.get("source"),
            edge.get("target"),
        } == {f"paper:{paper_a_id}", f"paper:{paper_b_id}"} and edge.get("kind")
    })
    return same_cluster, cluster_label, direct_edge_kinds


def _reading_order(profile_a, profile_b) -> WorkspaceComparisonOrder:
    year_a = profile_a.year or 0
    year_b = profile_b.year or 0
    if year_a and year_b and year_a != year_b:
        if year_a < year_b:
            first, second = profile_a, profile_b
            reason = f"按年份顺序先读 {profile_a.year} 年论文，再读 {profile_b.year} 年论文。"
        else:
            first, second = profile_b, profile_a
            reason = f"按年份顺序先读 {profile_b.year} 年论文，再读 {profile_a.year} 年论文。"
    else:
        first, second = profile_a, profile_b
        if profile_a.detail_level != profile_b.detail_level:
            reason = "先看信息更完整的论文，再看补充论文。"
            if profile_a.detail_level != "full" and profile_b.detail_level == "full":
                first, second = profile_b, profile_a
        else:
            reason = "两篇年份接近，按当前排列顺序阅读即可。"
    return WorkspaceComparisonOrder(
        first_paper_id=first.paper_id,
        first_title=first.title,
        second_paper_id=second.paper_id,
        second_title=second.title,
        reason=reason,
    )


def _section_purpose(profile_a, profile_b) -> WorkspaceComparisonSection:
    shared = _top_terms(profile_a.purpose_text, profile_b.purpose_text, limit=4)
    shared.extend(_shared_items(profile_a.key_contributions, profile_b.key_contributions, limit=2))
    shared = list(dict.fromkeys(shared))[:4]
    unique_a = _unique_items(profile_a.key_contributions or [profile_a.problem or profile_a.one_sentence or profile_a.title], profile_b.key_contributions)
    unique_b = _unique_items(profile_b.key_contributions or [profile_b.problem or profile_b.one_sentence or profile_b.title], profile_a.key_contributions)
    score = round(min(1.0, 0.7 * _text_similarity(profile_a.purpose_text, profile_b.purpose_text) + 0.3 * _list_jaccard(profile_a.key_contributions, profile_b.key_contributions)), 4)
    return WorkspaceComparisonSection(
        key="purpose",
        label=_SECTION_LABELS["purpose"],
        score=score,
        summary=_build_summary(_SECTION_LABELS["purpose"], shared, unique_a, unique_b, score),
        shared=shared,
        unique_a=unique_a,
        unique_b=unique_b,
    )


def _section_method(profile_a, profile_b) -> WorkspaceComparisonSection:
    modules_a = [str(module.get("name") or "").strip() for module in profile_a.method_modules if isinstance(module, dict) and str(module.get("name") or "").strip()]
    modules_b = [str(module.get("name") or "").strip() for module in profile_b.method_modules if isinstance(module, dict) and str(module.get("name") or "").strip()]
    shared = _shared_items(modules_a, modules_b, limit=4)
    if not shared:
        shared = _top_terms(profile_a.method_text, profile_b.method_text, limit=4)
    unique_a = _unique_items(modules_a or [profile_a.method_overview or profile_a.method_pipeline or profile_a.title], modules_b)
    unique_b = _unique_items(modules_b or [profile_b.method_overview or profile_b.method_pipeline or profile_b.title], modules_a)
    score = round(min(1.0, 0.65 * _text_similarity(profile_a.method_text, profile_b.method_text) + 0.35 * _list_jaccard(modules_a, modules_b)), 4)
    return WorkspaceComparisonSection(
        key="method",
        label=_SECTION_LABELS["method"],
        score=score,
        summary=_build_summary(_SECTION_LABELS["method"], shared, unique_a, unique_b, score),
        shared=shared,
        unique_a=unique_a,
        unique_b=unique_b,
    )


def _section_experiments(profile_a, profile_b) -> WorkspaceComparisonSection:
    shared_datasets = _shared_items(profile_a.datasets, profile_b.datasets, limit=4)
    shared_baselines = _shared_items(profile_a.baselines, profile_b.baselines, limit=4)
    shared = list(dict.fromkeys([*(f"数据集: {item}" for item in shared_datasets), *(f"基线: {item}" for item in shared_baselines)]))

    unique_a = []
    unique_b = []
    for item in _unique_items(profile_a.datasets, profile_b.datasets, limit=3):
        unique_a.append(f"数据集: {item}")
    for item in _unique_items(profile_a.baselines, profile_b.baselines, limit=3):
        unique_a.append(f"基线: {item}")
    for item in _unique_items(profile_b.datasets, profile_a.datasets, limit=3):
        unique_b.append(f"数据集: {item}")
    for item in _unique_items(profile_b.baselines, profile_a.baselines, limit=3):
        unique_b.append(f"基线: {item}")

    dataset_score = _list_jaccard(profile_a.datasets, profile_b.datasets)
    baseline_score = _list_jaccard(profile_a.baselines, profile_b.baselines)
    score = round(min(1.0, 0.6 * dataset_score + 0.4 * baseline_score), 4)
    return WorkspaceComparisonSection(
        key="experiments",
        label=_SECTION_LABELS["experiments"],
        score=score,
        summary=_build_summary(_SECTION_LABELS["experiments"], shared, unique_a, unique_b, score),
        shared=shared,
        unique_a=unique_a,
        unique_b=unique_b,
    )


def _section_implementation(profile_a, profile_b) -> WorkspaceComparisonSection:
    concepts_a = [str(item.get("concept") or "").strip() for item in profile_a.code_mapping if isinstance(item, dict) and str(item.get("concept") or "").strip()]
    concepts_b = [str(item.get("concept") or "").strip() for item in profile_b.code_mapping if isinstance(item, dict) and str(item.get("concept") or "").strip()]
    impl_a = [str(item.get("likely_implementation") or "").strip() for item in profile_a.code_mapping if isinstance(item, dict) and str(item.get("likely_implementation") or "").strip()]
    impl_b = [str(item.get("likely_implementation") or "").strip() for item in profile_b.code_mapping if isinstance(item, dict) and str(item.get("likely_implementation") or "").strip()]

    shared = _shared_items(concepts_a, concepts_b, limit=4)
    if not shared:
        shared = _top_terms(profile_a.implementation_text, profile_b.implementation_text, limit=4)
    unique_a = _unique_items(concepts_a or impl_a or [profile_a.title], concepts_b)
    unique_b = _unique_items(concepts_b or impl_b or [profile_b.title], concepts_a)
    concept_score = _list_jaccard(concepts_a, concepts_b)
    impl_score = _text_similarity(profile_a.implementation_text, profile_b.implementation_text)
    score = round(min(1.0, 0.55 * concept_score + 0.45 * impl_score), 4)
    return WorkspaceComparisonSection(
        key="implementation",
        label=_SECTION_LABELS["implementation"],
        score=score,
        summary=_build_summary(_SECTION_LABELS["implementation"], shared, unique_a, unique_b, score),
        shared=shared,
        unique_a=unique_a,
        unique_b=unique_b,
    )


def _section_metadata(profile_a, profile_b) -> WorkspaceComparisonSection:
    shared: list[str] = []
    if profile_a.venue and profile_b.venue and _normalize_text(profile_a.venue) == _normalize_text(profile_b.venue):
        shared.append(profile_a.venue)
    if profile_a.year and profile_b.year and profile_a.year == profile_b.year:
        shared.append(str(profile_a.year))

    unique_a = []
    unique_b = []
    if profile_a.venue and (not profile_b.venue or _normalize_text(profile_a.venue) != _normalize_text(profile_b.venue)):
        unique_a.append(profile_a.venue)
    if profile_b.venue and (not profile_a.venue or _normalize_text(profile_a.venue) != _normalize_text(profile_b.venue)):
        unique_b.append(profile_b.venue)
    if profile_a.year and profile_b.year and profile_a.year != profile_b.year:
        unique_a.append(str(profile_a.year))
        unique_b.append(str(profile_b.year))

    year_score = 0.0
    if profile_a.year and profile_b.year:
        year_score = max(0.0, 1.0 - min(abs(profile_a.year - profile_b.year), 10) / 10)
    venue_score = 1.0 if profile_a.venue and profile_b.venue and _normalize_text(profile_a.venue) == _normalize_text(profile_b.venue) else 0.0
    def split_authors(text: str | None) -> list[str]:
        return [item.strip() for item in re.split(r"[,，;；]", text or "") if item.strip()]

    author_score = _list_jaccard(split_authors(profile_a.authors), split_authors(profile_b.authors))
    score = round(min(1.0, 0.5 * venue_score + 0.35 * year_score + 0.15 * author_score), 4)
    return WorkspaceComparisonSection(
        key="metadata",
        label=_SECTION_LABELS["metadata"],
        score=score,
        summary=_build_summary(_SECTION_LABELS["metadata"], shared, unique_a, unique_b, score),
        shared=shared,
        unique_a=unique_a,
        unique_b=unique_b,
    )


def build_workspace_comparison(workspace_id: int, paper_a_id: int, paper_b_id: int) -> WorkspaceComparisonResponse:
    if paper_a_id == paper_b_id:
        raise ValueError("papers must be different")

    engine = get_engine()
    with Session(engine) as session:
        ws = session.get(Workspace, workspace_id)
        if not ws:
            raise ValueError("workspace not found")

        memberships = session.exec(
            select(WorkspacePaper.paper_id).where(WorkspacePaper.workspace_id == workspace_id)
        ).all()
        member_ids = set(memberships)
        if paper_a_id not in member_ids or paper_b_id not in member_ids:
            raise ValueError("paper not in workspace")

        graph = session.exec(
            select(ResearchGraph).where(ResearchGraph.workspace_id == workspace_id)
        ).first()
        profiles = load_paper_profiles(session, [paper_a_id, paper_b_id])

    profile_a = profiles.get(paper_a_id)
    profile_b = profiles.get(paper_b_id)
    if not profile_a or not profile_b:
        raise ValueError("paper not found")

    sections = [
        _section_purpose(profile_a, profile_b),
        _section_method(profile_a, profile_b),
        _section_experiments(profile_a, profile_b),
        _section_implementation(profile_a, profile_b),
        _section_metadata(profile_a, profile_b),
    ]

    same_cluster, cluster_label, direct_edge_kinds = _graph_context(graph, paper_a_id, paper_b_id)
    overall = sum(section.score * _SECTION_WEIGHTS.get(section.key, 0.0) for section in sections)
    if same_cluster:
        overall += 0.05
    has_relation_edge = any(k in ("related", "similar") for k in direct_edge_kinds)  # similar = 旧图谱边名
    if has_relation_edge:
        overall += 0.05
    if "cites" in direct_edge_kinds:
        overall += 0.03
    overall = round(min(1.0, overall), 4)

    if "cites" in direct_edge_kinds:
        relation = "引用关联"
    elif same_cluster and overall >= 0.45:
        relation = f"同子方向{f' · {cluster_label}' if cluster_label else ''}"
    elif sections[0].score >= 0.65 and sections[1].score >= 0.55:
        relation = "同问题同方法"
    elif sections[1].score >= 0.60 and sections[2].score < 0.40:
        relation = "方法迁移"
    elif sections[2].score >= 0.60 and sections[0].score >= 0.35:
        relation = "同实验设定"
    elif overall < 0.30:
        relation = "跨方向参考"
    else:
        relation = "部分相关"

    reason_parts = []
    if same_cluster and cluster_label:
        reason_parts.append(f"同属图谱子方向「{cluster_label}」")
    if has_relation_edge:
        reason_parts.append("图谱中存在直接关联边")
    if "cites" in direct_edge_kinds:
        reason_parts.append("图谱中存在直接引用关系")
    if sections[0].shared:
        reason_parts.append(f"研究目标重合：{sections[0].shared[0]}")
    if sections[1].shared:
        reason_parts.append(f"方法重合：{sections[1].shared[0]}")
    if sections[2].shared:
        reason_parts.append(f"实验共性：{sections[2].shared[0]}")
    if not reason_parts:
        reason_parts.append("主要依据论文结构化分析结果与图谱关系综合判断")
    relation_reason = "；".join(reason_parts[:4])

    shared_topics = _top_terms(profile_a.purpose_text + " " + profile_a.method_text, profile_b.purpose_text + " " + profile_b.method_text, limit=5)
    if not shared_topics:
        shared_topics = list(dict.fromkeys([*sections[0].shared, *sections[1].shared]))[:5]

    reading_order = _reading_order(profile_a, profile_b)

    logger.info(
        "workspace comparison ws=%s paper_a=%s paper_b=%s overall=%.3f relation=%s",
        workspace_id,
        paper_a_id,
        paper_b_id,
        overall,
        relation,
    )

    return WorkspaceComparisonResponse(
        workspace_id=workspace_id,
        paper_a=_paper_payload(profile_a),
        paper_b=_paper_payload(profile_b),
        overall_score=overall,
        relation=relation,
        relation_reason=relation_reason,
        shared_topics=shared_topics,
        same_cluster=same_cluster,
        cluster_label=cluster_label,
        direct_edge_kinds=direct_edge_kinds,
        reading_order=reading_order,
        sections=sections,
        updated_at=datetime.now(timezone.utc),
    )
