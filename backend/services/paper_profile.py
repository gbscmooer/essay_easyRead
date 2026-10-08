"""论文画像抽取与规范化工具。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import Session, select

from ..models.analysis import AnalysisResult
from ..models.paper import Paper


def _as_text(value) -> str:
    return str(value).strip() if value is not None else ""


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [_as_text(item) for item in value if _as_text(item)]
    item = _as_text(value)
    return [item] if item else []


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


@dataclass(slots=True)
class PaperProfile:
    """单篇论文的结构化画像。"""

    paper_id: int
    title: str
    authors: str = ""
    venue: str = ""
    year: Optional[int] = None
    source: str = "uploaded"
    read_level: str = "none"
    detail_level: str = "metadata"
    summary: str = ""
    one_sentence: str = ""
    problem: str = ""
    key_contributions: list[str] = field(default_factory=list)
    method_overview: str = ""
    method_pipeline: str = ""
    method_modules: list[dict] = field(default_factory=list)
    datasets: list[str] = field(default_factory=list)
    baselines: list[str] = field(default_factory=list)
    code_mapping: list[dict] = field(default_factory=list)
    limitations: str = ""
    graph_digest: dict = field(default_factory=dict)

    @property
    def purpose_text(self) -> str:
        parts = [self.summary, self.one_sentence, self.problem, *self.key_contributions]
        return " ".join(part for part in parts if part).strip()

    @property
    def method_text(self) -> str:
        parts = [self.method_overview, self.method_pipeline]
        parts.extend(
            " ".join(filter(None, [_as_text(module.get("name")), _as_text(module.get("role")), _as_text(module.get("innovation"))]))
            for module in self.method_modules
            if isinstance(module, dict)
        )
        return " ".join(part for part in parts if part).strip()

    @property
    def experiment_text(self) -> str:
        parts = [self._digest_text("main_results"), self._digest_text("ablation")]
        parts.extend(self.datasets)
        parts.extend(self.baselines)
        return " ".join(part for part in parts if part).strip()

    @property
    def implementation_text(self) -> str:
        parts = []
        for mapping in self.code_mapping:
            if not isinstance(mapping, dict):
                continue
            concept = _as_text(mapping.get("concept"))
            impl = _as_text(mapping.get("likely_implementation"))
            parts.append(" ".join(part for part in (concept, impl) if part))
        return " ".join(part for part in parts if part).strip()

    def _digest_text(self, key: str) -> str:
        return _as_text(self.graph_digest.get(key)) if self.graph_digest else ""

    def canonical_text(self) -> str:
        """供相似度 provider / reranker 使用的综合文本。"""
        parts = [
            self.title,
            self.authors,
            self.venue,
            self.purpose_text,
            self.method_text,
            self.experiment_text,
            self.implementation_text,
            self.limitations,
        ]
        return " ".join(part for part in parts if part).strip()


def build_paper_profile(paper: Paper, analysis: AnalysisResult | None = None) -> PaperProfile:
    digest = paper.graph_digest if isinstance(getattr(paper, "graph_digest", None), dict) else {}
    if paper.status == "partial":
        analysis = None
    read_level = "full" if analysis is not None else ("digest" if digest else "none")
    detail_level = "full" if analysis is not None else ("digest" if digest else "metadata")

    title = _as_text(getattr(analysis, "title", None) or paper.title or paper.filename)
    authors = _as_text(getattr(analysis, "authors", None) or paper.authors)
    venue = _as_text(getattr(analysis, "venue", None) or paper.venue)
    year = getattr(analysis, "year", None) or paper.year

    if analysis is not None:
        method = _as_dict(analysis.method)
        experiments = _as_dict(analysis.experiments)
        return PaperProfile(
            paper_id=paper.id,
            title=title,
            authors=authors,
            venue=venue,
            year=year,
            source=paper.source or "uploaded",
            read_level=read_level,
            detail_level=detail_level,
            summary=_as_text(analysis.one_sentence),
            one_sentence=_as_text(analysis.one_sentence),
            problem=_as_text(analysis.problem),
            key_contributions=_as_list(analysis.key_contributions),
            method_overview=_as_text(method.get("overview")),
            method_pipeline=_as_text(method.get("pipeline")),
            method_modules=[module for module in (method.get("key_modules") or []) if isinstance(module, dict)],
            datasets=_as_list(experiments.get("datasets")),
            baselines=_as_list(experiments.get("baselines")),
            code_mapping=[mapping for mapping in (analysis.code_mapping or []) if isinstance(mapping, dict)],
            limitations=_as_text(analysis.limitations),
            graph_digest=digest,
        )

    if digest:
        return PaperProfile(
            paper_id=paper.id,
            title=title,
            authors=authors,
            venue=venue,
            year=year,
            source=paper.source or "uploaded",
            read_level=read_level,
            detail_level=detail_level,
            summary=_as_text(digest.get("summary")),
            one_sentence=_as_text(digest.get("one_sentence")),
            problem=_as_text(digest.get("problem")),
            key_contributions=_as_list(digest.get("key_contributions")),
            method_overview=_as_text(digest.get("method")),
            datasets=_as_list(digest.get("datasets")),
            baselines=_as_list(digest.get("baselines")),
            graph_digest=digest,
        )

    return PaperProfile(
        paper_id=paper.id,
        title=title,
        authors=authors,
        venue=venue,
        year=year,
        source=paper.source or "uploaded",
        read_level=read_level,
        detail_level=detail_level,
    )


def load_paper_profiles(session: Session, paper_ids: list[int]) -> dict[int, PaperProfile]:
    """批量加载论文画像。"""
    if not paper_ids:
        return {}

    papers = {
        paper.id: paper
        for paper in session.exec(select(Paper).where(Paper.id.in_(paper_ids))).all()
    }
    analyses = {
        analysis.paper_id: analysis
        for analysis in session.exec(
            select(AnalysisResult).where(AnalysisResult.paper_id.in_(paper_ids))
        ).all()
    }

    profiles: dict[int, PaperProfile] = {}
    for paper_id in paper_ids:
        paper = papers.get(paper_id)
        if paper is None:
            continue
        profiles[paper_id] = build_paper_profile(paper, analyses.get(paper_id))
    return profiles
