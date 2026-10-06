"""Freeze a complementary reading portfolio from admitted document identities."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ....models.research_core import ResearchAsset
from .contracts import digest
from ...research_exclusions import title_key


class Choice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str
    role: Literal["classification", "representative"]
    requirement_ids: list[str] = Field(min_length=1, max_length=12)
    focus_queries: list[str] = Field(min_length=1, max_length=3)
    reason: str = Field(min_length=2, max_length=300)


class Portfolio(BaseModel):
    model_config = ConfigDict(extra="forbid")
    choices: list[Choice] = Field(max_length=4)
    gaps: list[str] = Field(max_length=8)


def latest(tools, question_id):
    rows = [
        a
        for a in tools.repo.rows(ResearchAsset)
        if a.content_kind == "reading_portfolio"
        and a.status == "active"
        and (a.inline_payload or {}).get("question_id") == question_id
    ]
    return rows[-1].inline_payload if rows else None


def validate(raw, documents, requirements):
    """Navigation never changes paper identity, user requests, or evidence status."""
    known = {a.asset_id: a for a in documents}
    units = {u["unit_id"] for u in requirements}
    titles, families, choices = set(), set(), []
    if sum(c["role"] == "classification" for c in raw["choices"]) > 1:
        raise ValueError("分类来源不能挤占互补原论文组合")
    for choice in raw["choices"]:
        asset = known.get(choice["asset_id"])
        if not asset or any(uid not in units for uid in choice["requirement_ids"]):
            raise ValueError("阅读组合必须绑定准入材料与原有需求")
        payload, paper = (
            asset.inline_payload or {},
            (asset.inline_payload or {}).get("paper", {}),
        )
        if not payload.get("text", "").strip():
            raise ValueError("空材料不能成为阅读组合")
        if (
            choice["role"] == "representative"
            and paper.get("research_role") == "survey"
        ):
            raise ValueError("综述不能替代代表原论文")
        title, family = title_key(paper.get("title", "")), asset.family_key
        if title in titles or family in families:
            raise ValueError("阅读组合包含同一文献的重复版本")
        titles.add(title)
        families.add(family)
        if any(
            not q.strip() or len(q.split()) > 12 or len(q) > 160
            for q in choice["focus_queries"]
        ):
            raise ValueError("阅读导航须简短且有界")
        choices.append(choice)
    return {"choices": choices, "gaps": raw["gaps"]}


async def make(tools, question, *, save=True):
    existing = latest(tools, question.question_id)
    if existing is not None:
        return existing
    requirements = (
        (tools.plan() or {}).get("requirement_ledger", {}).get(question.question_id, [])
    )
    documents = tools.ranked_documents(question.question_id)
    # One entry per exact normalized title, preferring a body over its abstract.
    candidates = {}
    for asset in documents:
        payload, paper = (
            asset.inline_payload or {},
            (asset.inline_payload or {}).get("paper", {}),
        )
        if not payload.get("text", "").strip():
            continue
        name = title_key(paper.get("title", ""))
        previous = candidates.get(name)
        if previous is None or (
            payload.get("source_kind") != "abstract"
            and previous.inline_payload.get("source_kind") == "abstract"
        ):
            candidates[name] = asset
    docs = list(candidates.values())[:60]
    if not docs:
        raw = {"choices": [], "gaps": ["没有可读的准入材料"]}
    else:
        raw = await tools.model(
            "从已准入候选选择最多4篇互补阅读组合，不回答领域事实。优先1篇最贴用户任务范围的分类综述，"
            "其余2–3篇不同机制的代表原论文；只依据摘要实际内容，不能凭记忆填方法名单。"
            "通用异常检测综述不能代替工业/特定任务综述；同类机制不凑数，材料不足少选并gaps说明。"
            "每项asset_id必须来自候选；role=classification/representative，requirement_ids绑定用户原有需求。"
            "focus_queries给1–3条简短英文原文阅读导航，抓正常材料/学习或建库/测试评分及限制；reason仅解释选材理由。"
            "representative必须原论文自身方法；综述中的引文、只讲目录或其他任务不能冒充。"
            "严格保留用户模态、任务与监督范围；真实缺陷标注训练与正常建模、合成异常、外部预训练区别对待。"
            "不修改论文身份或用户需求，不把每篇论文变为新增必答项。没有支撑的类别不猜，返回gaps。",
            {
                "question": question.text,
                "requirements": requirements,
                "scope_constraints": (tools.plan() or {})
                .get("constraints", {})
                .get(question.question_id, []),
                "candidates": [
                    {
                        "asset_id": a.asset_id,
                        "title": a.inline_payload["paper"].get("title"),
                        "role": a.inline_payload["paper"].get("research_role"),
                        "scope": a.inline_payload["paper"].get("research_scope"),
                        "abstract": (
                            a.inline_payload["paper"].get("abstract")
                            or a.inline_payload.get("text", "")
                        )[:650],
                    }
                    for a in docs
                ],
            },
            max_tokens=1800,
            schema=Portfolio,
            stage="reading_portfolio",
        )
    payload = {
        "question_id": question.question_id,
        "method": "complementary_material_v1",
        "protocol_hash": tools.repo.protocol()["hash"],
        **validate(raw, docs, requirements),
    }
    if save:
        with tools.repo.transaction() as session:
            session.add(
                ResearchAsset(
                    run_id=tools.repo.run_id,
                    content_kind="reading_portfolio",
                    inline_payload=payload,
                    content_hash=digest(payload),
                    parse_status="parsed",
                )
            )
    return payload


def resolve(tools, choice):
    selected = tools.repo.asset(choice["asset_id"], readable=True)
    name = title_key((selected.inline_payload or {}).get("paper", {}).get("title", ""))
    bodies = [
        a
        for a in tools.documents()
        if (a.inline_payload or {}).get("source_kind") != "abstract"
        and (
            a.family_key == selected.family_key
            or title_key(a.inline_payload.get("paper", {}).get("title", "")) == name
        )
    ]
    return bodies[0] if bodies else selected


async def execute(tools, action):
    from ....models.research_core import ResearchQuestion

    question = next(
        q
        for q in tools.repo.rows(ResearchQuestion)
        if q.question_id == action.question_id
    )
    payload = await make(tools, question, save=False)
    return {
        "assets": [
            {
                "content_kind": "reading_portfolio",
                "inline_payload": payload,
                "content_hash": digest(payload),
                "parse_status": "parsed",
            }
        ]
    }
