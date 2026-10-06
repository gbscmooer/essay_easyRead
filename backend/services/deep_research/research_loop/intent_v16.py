"""Source-bound requirements without redundant translated mode/phase labels."""

from collections import Counter
from copy import deepcopy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .intent_v12 import (
    PlanCheck as LegacyPlanCheck,
    PlanningRejected,
    source_clauses,
    audit,
    rejected,
    bind_plan,
    check_context as legacy_check_context,
    evaluate as legacy_evaluate,
)


class LocalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clause_id: str = Field(description="选择本问题原句C编号，不猜编号")
    requirement: str = Field(
        min_length=2,
        max_length=500,
        description="原句已有请求；保留其模式、数据角色、训练/推理阶段和适用条件，不填答案",
    )
    subject: str = Field("", max_length=80)
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


class MissingRequest(LocalRequest):
    question_id: str


class PlanCheck(LegacyPlanCheck):
    missing_requests: list[MissingRequest] = Field(max_length=72)


PLAN_SYSTEM = (
    "仅将用户原句转换为检索计划，不回答领域事实。question_plans每个给定question_id恰好一个对象。"
    "reading_role单个字符串，overview/mechanism/implementation选一项；queries每问题1–2条2–8词英文关键词。"
    "overview第一query保留具体任务/模态/模式并检索survey或taxonomy；不预置方法名单，不串不同方法为AND。"
    "targets只保留用户原文或criterion逐字点名的具体方法/系统/数据集；没有就[]，通用主题不是点名目标。"
    "requests每项只用同问题C编号，subject仅对应targets或空；question_id由程序绑定。"
    "requirement完整保留用户模式、数据角色、训练/测试推理、条件与比较请求；可拆分，但不能删掉不易回答的部分。"
    "不输出mode或phase标签，阶段和模式都用requirement中的自然语言准确保留，不另行翻译成辅助枚举。"
    "response_kind：解释现有论文数据/训练/测试/评分机制是fact，新场景选择或迁移建议才是inference。"
    "只讨论某模态/不排名/截止等范围约束不列为文献请求，独立检查会冻结原句范围。"
    "answer_units最多4项展示提纲，reading_queries最多4条原文阅读导航；均不能新增用户未问的指标/方法/要求。"
    "原文与自定义criterion的每项请求均需覆盖；纠错仅按字段错误与原句重输出完整计划，不能猜C或删除困难需求。"
)
CHECK_SYSTEM = (
    "独立核查计划是否完整且仅来自用户原句，不回答领域事实。引用仅选给定C原句/R需求短编号。"
    "coverage每C恰好一次，kind=request/constraint/context；原句所有明确请求及限定正确保留才covered=true。"
    "逐个核查对象、模式、正常或异常数据角色、训练与测试/推理操作、判据、条件、比较及用户criterion，不能只覆盖主题。"
    "限定保存在requirement自然语言及原句；没有mode/phase标签，不以缺标签判断漏项，也不能以空兼容标签抹去限定。"
    "request的unit_ids须是同C的R，constraint/context须[]；范围/时间/不排名不作为需要文献回答的请求。"
    "unsupported_unit_ids列新增或误解的R，missing_requests只写原句确实漏掉的限定或请求并绑定同问题C；不输出mode/phase。"
    "response_kinds每R恰好一次；现有论文训练/测试机制是fact，用户新场景建议才inference。"
    "target_checks每targets项恰好一次，仅用户明确点名具体对象为explicit=true。允许同义表达但不删要求。"
    "repair仅纠正本次字段/引用错误，始终依据原句和当前计划，不把旧评审当事实。"
)


def check_context(raw, units, clauses, aliases):
    context, refs = legacy_check_context(raw, units, clauses, aliases)
    context["requirements"] = [
        {k: v for k, v in u.items() if k not in ("mode", "phase")}
        for u in context["requirements"]
    ]
    return context, refs


def evaluate(check, raw, units, clauses, aliases, refs, specs):
    # Legacy persisted ledger fields are neutral compatibility values. The source
    # and requirement text, independently checked above, carry their real scope.
    normalized = deepcopy(check)
    normalized["missing_requests"] = [
        {**u, "phase": "general", "mode": ""} for u in check["missing_requests"]
    ]
    return legacy_evaluate(normalized, raw, units, clauses, aliases, refs, specs)


def normalize_constraint_references(check, units, aliases, refs):
    """Recover same-clause references on independently classified scope rows.

    Scope was already excluded from the literature-request ledger when the
    checker supplied an empty reference list. Keep that behavior, validating
    every supplied reference before removing this redundant list.
    """
    normalized = deepcopy(check)
    by_id = {u["unit_id"]: u for u in units}
    diagnostics = []
    for row in normalized["coverage"]:
        if row["kind"] != "constraint" or row["covered"] is not True or not row["unit_ids"]:
            continue
        if row["clause_id"] not in aliases:
            raise PlanningRejected("PLAN_CHECK_CLAUSE_COVERAGE", "planning_check")
        if any(ref not in refs for ref in row["unit_ids"]):
            raise PlanningRejected("PLAN_CHECK_UNKNOWN_UNIT", "planning_check")
        cid = aliases[row["clause_id"]]
        if any(by_id[refs[ref]]["clause_id"] != cid for ref in row["unit_ids"]):
            raise PlanningRejected("PLAN_CHECK_CROSS_CLAUSE", "planning_check")
        diagnostics.append({"source_ref": row["clause_id"], "removed_unit_refs": list(row["unit_ids"])})
        row["unit_ids"] = []
    return normalized, diagnostics


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
        raw["requests"].extend(
            {"question_id": qid, **r, "phase": "general", "mode": ""}
            for r in question["requests"]
        )
        raw["reading_roles"].append(
            {"question_id": qid, "role": question["reading_role"]}
        )
    return raw


MESSAGES = {
    "PLAN_QUESTION_COVERAGE": "question_plans必须为每个expected_question_ids恰好保留一个对象，禁止重复或未知问题。",
    "PLAN_SOURCE_ALIAS": "requests.clause_id必须逐字选择source_clauses中属于当前问题的C编号，禁止猜测。",
    "PLAN_SOURCE_QUESTION": "需求C编号属于其他问题。请按当前question_id的原句重新锚定，不改变原句或猜测。",
    "PLAN_SUBJECT_ANCHOR": "subject只能是当前问题targets里且用户明确点名的名称，未点名的请求保持空subject。",
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


async def make_anchored_plan(tools, specs, *, normalize_constraint_refs=False):
    clauses = source_clauses(specs)
    aliases = {f"C{i}": c["clause_id"] for i, c in enumerate(clauses, 1)}
    short = [{**c, "clause_id": ref} for ref, c in zip(aliases, clauses)]
    wire, raw, repair, check_only, refs = None, None, None, False, {}
    phase_context = ({
        "workflow_phase": "before_retrieval_requirement_validation",
        "review_target": "用户请求与范围是否完整保留在待答计划；尚未检索或形成文献答案",
        "coverage_means": "需求保留，不是答案完成；逐字保留原句可以覆盖请求，无需提供论文或领域事实",
    } if normalize_constraint_refs else {})
    plan_system = PLAN_SYSTEM
    check_system = CHECK_SYSTEM
    if normalize_constraint_refs:
        plan_system += (
            "当前尚未检索，输出待解决的问题及导航，不是领域答案。answer_units是待答提纲，禁止填机制结论、代表论文或记忆方法名单。"
            "同句包含多个请求时分别保留，现有论文机制解释为fact，新场景迁移核查建议为inference。"
        )
        check_system += (
            "当前是检索前的需求保留检查，不是最终答案或证据充分性检查。"
            "requirement完整复述用户原句即保留该请求；不能因为尚未给出路线、代表论文、机制事实或建议答案而判covered=false。"
            "只检查遗漏、增加、范围改变、锚定及回答身份；真正缺失的限定仍拒绝，不要求从记忆补事实。"
        )
    for attempt in range(2):
        check = None
        phase = "planning_check" if check_only else "planning"
        try:
            if not check_only:
                wire = await tools.model(
                    plan_system,
                    {
                        **phase_context,
                        "questions": specs,
                        "source_clauses": short,
                        "output_contract": {
                            "expected_question_ids": [s["question_id"] for s in specs],
                            "one_object_per_question": True,
                            "queries_per_question": "1–2 strings",
                            "reading_role": "one scalar enum value; never an array",
                            "request_scope": "preserve all mode/data/training/test qualifiers in requirement text; no mode/phase fields",
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
                check_system,
                {
                    **phase_context,
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
            normalizations = []
            checked = check
            if normalize_constraint_refs:
                checked, normalizations = normalize_constraint_references(check, units, aliases, refs)
            kept, scope, decoded = evaluate(
                checked, raw, units, clauses, aliases, refs, specs
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
                    "method": "source_text_scope_v5",
                    "source_clauses": clauses,
                    "reviews": [decoded],
                    "gaps_by_question": {},
                    "complete": True,
                    "repairs_used": attempt,
                    "source_aliases": aliases,
                    **({"constraint_reference_normalizations": normalizations} if normalize_constraint_refs else {}),
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
