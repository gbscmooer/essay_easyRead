"""Versioned exchange contracts; no framework or database dependencies."""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LOOP_VERSION = "question_loop_v20"
SUPPORTED_LOOP_VERSIONS = ("question_loop_v1", "question_loop_v2", "question_loop_v3", "question_loop_v4", "question_loop_v5", "question_loop_v6", "question_loop_v7", "question_loop_v8", "question_loop_v9", "question_loop_v10", "question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", LOOP_VERSION)
PROMPT_VERSION = LOOP_VERSION
TOOLS = ("plan_reading", "search_papers", "expand_citations", "fetch_document", "read_document", "read_documents", "assess_answer", "propose_stop")
EvidenceKind = Literal["fact", "mechanism", "comparison", "condition", "limitation", "counterexample"]


def digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class QuestionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=2, max_length=4000)
    required: bool = True
    priority: int = Field(1, ge=1, le=10)
    intent: str = Field("understand", max_length=100)
    answer_criteria: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(min_length=1, max_length=12)
    expected_evidence: list[EvidenceKind] = Field(default_factory=lambda: ["fact"], min_length=1, max_length=12)


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal["plan_reading", "search_papers", "expand_citations", "fetch_document", "read_document", "read_documents", "assess_answer", "propose_stop"]
    question_id: str
    query: str = Field("", max_length=500)
    asset_id: str = Field("", max_length=100)
    asset_ids: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=3)
    reason: str = Field("", max_length=500)
    gap_key: str = Field("", max_length=64)
    focus_queries: list[Annotated[str, Field(min_length=2, max_length=160)]] = Field(default_factory=list, max_length=4)

    def key(self, protocol_hash: str) -> str:
        # Wording of a reason cannot evade action deduplication.
        payload = self.model_dump(exclude={"reason"})
        if not self.gap_key:
            payload.pop("gap_key")
        if not self.focus_queries:
            payload.pop("focus_queries")
        if not self.asset_ids:
            payload.pop("asset_ids")  # Preserve the keys of frozen v1/v2 tasks.
        else:
            payload["asset_ids"] = sorted(self.asset_ids)
        return digest({"protocol_hash": protocol_hash, **payload})


class AtomicFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact: str = Field(min_length=1, max_length=1600)
    quote: str = Field(min_length=10, max_length=3000)
    evidence_kind: EvidenceKind = "fact"
    conditions: dict[str, str] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)


class DocumentFact(AtomicFact):
    asset_id: str


class AnswerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conclusion: str = Field("", max_length=6000)
    kind: Literal["fact", "inference"] = "fact"
    evidence_ids: list[str] = Field(default_factory=list, max_length=40)
    conditions: list[str] = Field(default_factory=list, max_length=40)
    conflicts: list[str] = Field(default_factory=list, max_length=20)
    gaps: list[str] = Field(default_factory=list, max_length=20)
    criteria_met: list[str] = Field(default_factory=list, max_length=12)
    status: Literal["supported", "conflicted", "insufficient"] = "insufficient"


class QuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str
    query: str = Field(min_length=3, max_length=160)

    @field_validator("query")
    @classmethod
    def compact_english(cls, value):
        import re
        if re.search(r"[\u4e00-\u9fff]", value) or not 2 <= len(value.split()) <= 8:
            raise ValueError("检索式须为 2–8 个英文词，避免原中文问句及过长 AND 检索")
        return " ".join(value.split())


class SearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[QuerySpec] = Field(min_length=1, max_length=8)
    answer_units: list[Annotated[str, Field(min_length=2, max_length=500)]] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def bounded_queries(self):
        counts = {}
        for query in self.queries:
            counts[query.question_id] = counts.get(query.question_id, 0) + 1
        if any(count > 2 for count in counts.values()):
            raise ValueError("每个问题最多2条查询；优先整体综述与近期方向，不得罗列全部可能方向")
        if len({(q.question_id, q.query.casefold()) for q in self.queries}) != len(self.queries):
            raise ValueError("检索式不得重复")
        return self


class ResearchTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str
    name: str = Field(min_length=2, max_length=80)


class FocusedSearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Normalize mechanically redundant queries in Tools, without spending another
    # generation attempt or weakening the bounds of historical SearchPlan.
    queries: list[QuerySpec] = Field(min_length=1, max_length=8)
    answer_units: list[Annotated[str, Field(min_length=2, max_length=500)]] = Field(min_length=1, max_length=4)
    targets: list[ResearchTarget] = Field(default_factory=list, max_length=6)
    reading_queries: list[Annotated[str, Field(min_length=2, max_length=160)]] = Field(default_factory=list, max_length=4)


class RelevanceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    status: Literal["relevant", "unrelated", "uncertain"]
    role: Literal["survey", "foundation", "recent", "method", "dataset", "other"] = "other"
    scope: Literal["overview", "specific"] = "specific"
    reason: str = Field(max_length=500)
    primary_targets: list[str] = Field(default_factory=list, max_length=6)


class RelevanceBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decisions: list[RelevanceDecision] = Field(max_length=36)


class SupportCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supported: bool
    reason: str = Field(max_length=1000)


class EvidenceSupportCheck(SupportCheck):
    relevant: bool


class SupportChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checks: list[EvidenceSupportCheck] = Field(max_length=18)


class AnswerVerdict(SupportCheck):
    conditions_preserved: bool
    criteria_met: list[str] = Field(max_length=12)
    conflicts: list[str] = Field(default_factory=list, max_length=20)


class AnswerParagraph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion_index: int = Field(ge=0, lt=12)
    title: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=10, max_length=1400)
    evidence_ids: list[str] = Field(min_length=1, max_length=6)
    kind: Literal["fact", "inference"] = "fact"


class UnresolvedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion_index: int = Field(ge=0, lt=12)
    missing_requirement: str = Field(min_length=2, max_length=500)


class EvidenceAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paragraphs: list[AnswerParagraph] = Field(min_length=1, max_length=10)
    conditions: list[str] = Field(default_factory=list, max_length=12)
    unresolved_requests: list[UnresolvedRequest] = Field(default_factory=list, max_length=12)


class GapStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal["read_documents", "search_papers"]
    asset_ids: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=2)
    query: str = Field(min_length=3, max_length=160)
    reason: str = Field(min_length=2, max_length=500)

    @model_validator(mode="after")
    def actionable(self):
        import re
        if re.search(r"[\u4e00-\u9fff]", self.query) or not 2 <= len(self.query.split()) <= 8:
            raise ValueError("缺口导航须为2–8词英文，不写答案事实")
        if self.tool == "read_documents" and (not self.asset_ids or len(set(self.asset_ids)) != len(self.asset_ids)):
            raise ValueError("定向补读必须选择不重复的真实资产")
        if self.tool == "search_papers" and self.asset_ids:
            raise ValueError("补搜不携带阅读资产")
        return self


class AnswerCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    met_indices: list[Annotated[int, Field(ge=0, lt=12)]] = Field(max_length=12)
    gaps: list[str] = Field(default_factory=list, max_length=12)
    next_steps: list[GapStep] = Field(default_factory=list, max_length=2)


class ParagraphVerdict(SupportChecks):
    conditions_preserved: bool
    conflicts: list[str] = Field(default_factory=list, max_length=12)
