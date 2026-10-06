"""Per-question scalar navigation; immutable source checks and one shared repair."""

from collections import Counter
from copy import deepcopy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .intent_v12 import (
    PLAN_SYSTEM as OLD_SYSTEM,
    CHECK_SYSTEM,
    PlanCheck,
    PlanningRejected,
    source_clauses,
    audit,
    rejected,
    bind_plan,
    check_context,
    evaluate,
)


class LocalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clause_id: str = Field(description="选择本问题原句C编号，不猜编号")
    requirement: str = Field(min_length=2, max_length=500)
    subject: str = Field("", max_length=80)
    phase: Literal["general", "training", "inference"] = "general"
    mode: str = Field("", max_length=80)
    response_kind: Literal["fact", "inference"] = "fact"


class QuestionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str
    queries: list[Annotated[str, Field(min_length=2, max_length=160)]] = Field(
        min_length=1, max_length=2
    )
    reading_role: Literal["overview", "mechanism", "implementation"] = Field(
        description="单个字符串，方向路线概览选overview，具体方法解释比较选mechanism，实现查新选implementation"
    )
    targets: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        max_length=6
    )
    requests: list[LocalRequest] = Field(min_length=1, max_length=72)


class GroupedPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_plans: list[QuestionPlan] = Field(
        min_length=1,
        max_length=8,
        description="每个给定问题恰好一个对象，不按阅读角色拆成多个对象",
    )
    answer_units: list[Annotated[str, Field(min_length=2, max_length=120)]] = Field(
        min_length=1, max_length=4
    )
    reading_queries: list[Annotated[str, Field(min_length=2, max_length=160)]] = Field(
        max_length=4
    )


PLAN_SYSTEM = (
    "仅转换用户原句到检索计划，不回答领域事实。输出question_plans，每个给定question_id恰好一个对象。"
    "该对象的reading_role只能是一个字符串：overview/mechanism/implementation选一项，不列出所有枚举值。"
    "queries是1–2个英文关键词字符串，targets是用户明确点名的具体名称字符串列表，没有就[]。"
    "requests是该问题原句已有请求；question_id由程序绑定，不再在requests里输出。"
    "全局answer_units只是展示提纲，reading_queries只是英文原文阅读导航。"
    "原问句的只讨论某模态/不排名/截止时间属于范围约束，不放进requests。"
    "来源只使用当前问题的C编号，建议新场景是inference，解释论文操作/评分是fact。"
    "覆盖用户明确请求与自定义标准。修补时按字段错误和用户原句重新输出完整question_plans，禁止扩大需求。"
    + OLD_SYSTEM.replace("reading_roles每问题恰好一次", "reading_role每问题单个字符串")
)


def flatten(wire, specs):
    expected = {s["question_id"] for s in specs}
    if Counter(q["question_id"] for q in wire["question_plans"]) != Counter(
        {q: 1 for q in expected}
    ):
        raise PlanningRejected("PLAN_QUESTION_COVERAGE")
    raw = {
        "queries": [],
        "targets": [],
        "requests": [],
        "reading_roles": [],
        "answer_units": wire["answer_units"],
        "reading_queries": wire["reading_queries"],
    }
    for question in wire["question_plans"]:
        qid = question["question_id"]
        # Only navigation string duplicates are normalized, never source/subject IDs.
        seen = set()
        for query in question["queries"]:
            text = " ".join(query.split())
            if text and text.casefold() not in seen:
                raw["queries"].append({"question_id": qid, "query": text})
                seen.add(text.casefold())
        names = {}
        for name in question["targets"]:
            names.setdefault(name.casefold(), name)
        raw["targets"].extend(
            {"question_id": qid, "name": name} for name in names.values()
        )
        raw["requests"].extend({"question_id": qid, **r} for r in question["requests"])
        raw["reading_roles"].append(
            {"question_id": qid, "role": question["reading_role"]}
        )
    return raw


MESSAGES = {
    "PLAN_QUESTION_COVERAGE": "question_plans必须为每个expected_question_ids恰好保留一个对象，禁止重复或未知问题。",
    "PLAN_SOURCE_ALIAS": "requests.clause_id必须逐字选择source_clauses中属于当前问题的C编号，禁止猜测。",
    "PLAN_SOURCE_QUESTION": "需求C编号属于其他问题。请按当前question_id的原句重新锚定，不改变原句或猜测。",
    "PLAN_SUBJECT_ANCHOR": "subject只能是当前问题targets里且用户明确点名的名称，未点名的请求保持空subject。",
    "PLAN_MODE_ANCHOR": "mode必须来自对应问题用户原句，不可添加原句未给出的模式。",
    "PLAN_INVENTED_TARGET": "targets必须是当前问题原文或自定义标准中明确点名的具体对象，不能填通用主题或猜测方法。",
    "PLAN_QUERY_COVERAGE": "每问题queries必须有1–2项非空英文关键词；只可整理导航，不能删除用户需求。",
    "PLAN_INTENT_INCOMPLETE": "依据review和用户原句修正完整请求，覆盖漏项、移除新增项；范围约束不作为文献请求。",
    "PLAN_CHECK_CLAUSE_COVERAGE": "coverage必须每个给定C恰好一次，不增减或重复C。",
    "PLAN_CHECK_KIND_COVERAGE": "response_kinds必须每个给定R恰好一次，不增减或重复R。",
    "PLAN_CHECK_TARGET_COVERAGE": "target_checks必须每个给定问题和名称恰好一次。",
    "PLAN_CHECK_UNKNOWN_UNIT": "unit_ids和unsupported_unit_ids只能选择当前requirements里的R编号。",
    "PLAN_CHECK_CROSS_CLAUSE": "coverage.unit_ids只能选择绑定当前C的R编号，不能跨原句借用需求。",
    "PLAN_CHECK_CONSTRAINT_REFERENCES": "constraint/context的unit_ids必须[]，不把范围限制当作论文事实请求。",
}


def repair_feedback(error, original_error, specs, aliases, refs):
    result = {
        "instruction": MESSAGES.get(
            error.code,
            "根据schema字段错误重新输出完整合法JSON；只依据用户原句，不删除困难请求。",
        ),
        "expected_question_ids": [s["question_id"] for s in specs],
        "allowed_source_refs": list(aliases),
        "allowed_unit_refs": list(refs),
        "issues": error.issues,
    }
    if isinstance(original_error, ValidationError):
        result["field_errors"] = [
            {
                "path": ".".join(map(str, e["loc"])),
                "type": e["type"],
                "limits": {
                    k: v
                    for k, v in (e.get("ctx") or {}).items()
                    if k in ("min_length", "max_length", "expected")
                },
            }
            for e in original_error.errors(include_input=False, include_context=True)[
                :16
            ]
        ]
    return result


async def make_anchored_plan(tools, specs):
    clauses = source_clauses(specs)
    aliases = {f"C{i}": c["clause_id"] for i, c in enumerate(clauses, 1)}
    short = [{**c, "clause_id": ref} for ref, c in zip(aliases, clauses)]
    wire, raw, repair, check_only, refs = None, None, None, False, {}
    for attempt in range(2):
        check = None
        phase = "planning_check" if check_only else "planning"
        try:
            if not check_only:
                wire = await tools.model(
                    PLAN_SYSTEM,
                    {
                        "questions": specs,
                        "source_clauses": short,
                        "output_contract": {
                            "expected_question_ids": [s["question_id"] for s in specs],
                            "one_object_per_question": True,
                            "queries_per_question": "1–2 strings",
                            "reading_role": "one scalar enum value; never an array",
                        },
                        **({"repair": repair} if repair else {}),
                    },
                    max_tokens=4000,
                    schema=GroupedPlan,
                    stage="planning" if attempt == 0 else "planning_repair",
                )
                wire = deepcopy(wire)
                audit(tools, "planning" if attempt == 0 else "planning_repair", wire)
                raw = flatten(wire, specs)
            units = bind_plan(raw, specs, clauses, aliases)
            context, refs = check_context(raw, units, clauses, aliases)
            phase = "planning_check"
            check = await tools.model(
                CHECK_SYSTEM,
                {
                    "questions": specs,
                    **context,
                    **({"repair": repair} if check_only else {}),
                },
                max_tokens=3500,
                schema=PlanCheck,
                stage="planning_check" if attempt == 0 else "planning_check_repair",
            )
            audit(
                tools,
                "planning_check" if attempt == 0 else "planning_check_repair",
                check,
            )
            kept, scope, decoded = evaluate(
                check, raw, units, clauses, aliases, refs, specs
            )
            raw.update(
                strategy="material_first_v3",
                feedback_policy="gap_followup_v2",
                requirement_ledger={
                    s["question_id"]: [
                        u for u in kept if u["question_id"] == s["question_id"]
                    ]
                    for s in specs
                },
                constraints={
                    s["question_id"]: scope.get(s["question_id"], []) for s in specs
                },
                intent_coverage={
                    "method": "per_question_short_source_v4",
                    "source_clauses": clauses,
                    "reviews": [decoded],
                    "gaps_by_question": {},
                    "complete": True,
                    "repairs_used": attempt,
                    "source_aliases": aliases,
                },
            )
            return raw
        except ValueError as error:
            exc = (
                error
                if isinstance(error, PlanningRejected)
                else PlanningRejected("PLAN_STRUCTURED_OUTPUT", phase)
            )
            rejected(tools, exc, attempt + 1)
            if attempt:
                if exc is error:
                    raise
                raise exc from error
            check_only = exc.stage == "planning_check"
            repair = {
                **repair_feedback(exc, error, specs, aliases, refs),
                "previous_candidate": check if check_only else wire,
                "review": check if not check_only else None,
            }
    raise PlanningRejected("PLAN_NOT_COMPLETED")
