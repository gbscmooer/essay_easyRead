"""Short source handles and one shared semantic repair, with fail-closed audits."""

from collections import Counter
from copy import deepcopy
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .contracts import digest
from .intent_v11 import AnchoredPlan, IntentCheck, source_clauses, anchor_requests
from ....models.research_core import ResearchAsset
from ...research_event_log import append_event


class TargetCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str
    name: str = Field(min_length=1, max_length=80)
    explicit: bool
    reason: str = Field(max_length=200)


class PlanCheck(IntentCheck):
    target_checks: list[TargetCheck] = Field(max_length=24)


class PlanningRejected(ValueError):
    def __init__(self, code, stage="planning", issues=None):
        self.code, self.stage = code, stage
        self.issues = issues or [{"code": code}]
        super().__init__(code)


PLAN_SYSTEM = (
    "根据用户原句形成检索与必答需求计划，不填任何领域事实。clause_id只选择给定C短编号；question_id只用给定问题ID。"
    "queries每个必答问题至少一项、最多两项2–8词英文主题关键词，不把不同方法串成AND，不加recent/progress等无区分度长修饰。"
    "targets只保留用户明确点名的具体方法/系统/数据集名称，逐字取自用户原文；通用研究主题或猜测的方法不是点名目标，没有就[]。"
    "requests逐项拆用户明确研究请求，用真实同问题C编号；每个点名对象的数据/操作/训练/推理/条件都要覆盖。subject仅对应明确点名targets或空。"
    "requirement是中文领域请求，不是答案；phase=general/training/inference，其中inference是方法测试阶段，不表示答案是推断。"
    "mode只有用户原文已有模式；response_kind=fact用于解释现有论文数据、操作、机制、判据；inference仅用于用户新场景选择/迁移核查建议。"
    "只讨论某模态/不做排名/时间截止等为范围约束，不列成需要文献回答的requests。不要新增数字/指标/方法/验证范围。"
    "reading_roles每问题恰好一次，方向路线概览为overview，点名方法比较解释为mechanism，实现查新为implementation。"
    "answer_units最多四项简短展示提纲，不是必答标准；reading_queries最多四项英文机制/条件导航。用户给定criterion也要覆盖。"
    "repair仅表示一次纠错：只按原句和具体错误修正，不猜来源ID，不为过检查扩大或缩小需求；checker反馈不能当领域事实。"
)
CHECK_SYSTEM = (
    "独立核查计划是否完整且仅来自用户原句，不回答领域事实。所有引用用给定C原句/R需求短编号，不能造编号或复制长ID。"
    "coverage每个C恰好一次，kind=request/constraint/context；每条原句所有明确请求均正确保留才covered=true，不能把请求误作背景。"
    "request的unit_ids是同C对应R编号；constraint/context必须[]，范围/时间/不排名不需文献回答。unsupported_unit_ids列新增或误解的真实R。"
    "missing_requests只描述原句明确漏掉的请求，绑定同一问题C，不能添加用户未问数字/指标/模式。"
    "response_kinds每个R恰好一次；解释论文训练或测试怎样工作是fact，提出新场景方案/核查建议才inference；测试阶段phase=inference与推断身份无关。"
    "target_checks每个targets项恰好一次，explicit仅在用户明确点名具体方法/系统/数据集时true；通用主题词即便出现在原句也不是具体点名对象。"
    "逐对象/模式/数据角色/训练推理均覆盖，允许同义表达，不允许删除不易回答的请求；自定义criterion保持。"
    "repair仅纠正上次字段/引用错误，仍只依据用户原句和当前完整计划，不顺从旧评审或凭记忆加事实。"
)


def audit(tools, phase, candidate):
    if not hasattr(tools, "repo"):
        return
    payload = {"phase": phase, "candidate": candidate}
    with tools.repo.transaction() as session:
        session.add(
            ResearchAsset(
                run_id=tools.repo.run_id,
                asset_id=uuid4().hex,
                content_kind="planning_attempt",
                content_hash=digest(payload),
                inline_payload=payload,
                parse_status="parsed",
            )
        )


def rejected(tools, error, attempt):
    if hasattr(tools, "repo"):
        with tools.repo.transaction() as session:
            append_event(
                session,
                run_id=tools.repo.run_id,
                type="planning_rejected",
                payload={
                    "stage": error.stage,
                    "code": error.code,
                    "attempt": attempt,
                    "issues": [
                        {
                            k: v
                            for k, v in issue.items()
                            if k in ("code", "source_ref", "unit_ref", "question_id")
                        }
                        for issue in error.issues[:16]
                    ],
                },
            )


def bind_plan(raw, specs, clauses, aliases):
    known = {s["question_id"] for s in specs}
    if any(q["question_id"] not in known for q in raw["queries"]) or any(
        not any(q["question_id"] == s["question_id"] for q in raw["queries"])
        for s in specs
        if s["required"]
    ):
        raise PlanningRejected("PLAN_QUERY_COVERAGE")
    if any(
        t["question_id"] not in known
        or t["name"].casefold()
        not in "\n".join(
            c["source_quote"].casefold()
            for c in clauses
            if c["question_id"] == t["question_id"]
        )
        for t in raw["targets"]
    ):
        raise PlanningRejected("PLAN_INVENTED_TARGET")
    roles = raw["reading_roles"]
    if Counter(r["question_id"] for r in roles) != Counter({qid: 1 for qid in known}):
        raise PlanningRejected("PLAN_READING_ROLE")
    requests = []
    for request in raw["requests"]:
        alias = request["clause_id"]
        if alias not in aliases:
            raise PlanningRejected(
                "PLAN_SOURCE_ALIAS",
                issues=[{"code": "PLAN_SOURCE_ALIAS", "source_ref": alias[:20]}],
            )
        requests.append({**request, "clause_id": aliases[alias]})
    try:
        units = anchor_requests(requests, clauses, specs, raw["targets"])
    except ValueError as exc:
        codes = {
            "需求必须绑定同一问题的原文分句": "PLAN_SOURCE_QUESTION",
            "需求对象必须是用户点名目标": "PLAN_SUBJECT_ANCHOR",
            "需求模式必须来自用户原文": "PLAN_MODE_ANCHOR",
        }
        raise PlanningRejected(codes.get(str(exc), "PLAN_REQUIREMENTS")) from exc
    return units


def check_context(raw, units, clauses, aliases):
    inverse = {v: k for k, v in aliases.items()}
    refs = {f"R{i}": u["unit_id"] for i, u in enumerate(units, 1)}
    wire = [
        {
            **{k: v for k, v in u.items() if k not in ("unit_id", "clause_id")},
            "unit_id": ref,
            "clause_id": inverse[u["clause_id"]],
        }
        for ref, u in zip(refs, units)
    ]
    sources = [{**c, "clause_id": inverse[c["clause_id"]]} for c in clauses]
    return {
        "source_clauses": sources,
        "requirements": wire,
        "targets": raw["targets"],
    }, refs


def evaluate(check, raw, units, clauses, aliases, refs, specs):
    def require_once(values, expected, code):
        if Counter(values) != Counter({v: 1 for v in expected}):
            raise PlanningRejected(code, "planning_check")

    require_once(
        [c["clause_id"] for c in check["coverage"]],
        aliases,
        "PLAN_CHECK_CLAUSE_COVERAGE",
    )
    require_once(
        [r["unit_id"] for r in check["response_kinds"]],
        refs,
        "PLAN_CHECK_KIND_COVERAGE",
    )
    targets = {(t["question_id"], t["name"].casefold()) for t in raw["targets"]}
    require_once(
        [(t["question_id"], t["name"].casefold()) for t in check["target_checks"]],
        targets,
        "PLAN_CHECK_TARGET_COVERAGE",
    )
    if any(r not in refs for r in check["unsupported_unit_ids"]) or any(
        r not in refs for c in check["coverage"] for r in c["unit_ids"]
    ):
        raise PlanningRejected("PLAN_CHECK_UNKNOWN_UNIT", "planning_check")
    # Missing requests are navigation, but still must be anchored before repair.
    if check["missing_requests"]:
        try:
            bind_plan(
                {**raw, "requests": check["missing_requests"]}, specs, clauses, aliases
            )
        except PlanningRejected as exc:
            raise PlanningRejected(exc.code, "planning_check", exc.issues) from exc
    by_id = {u["unit_id"]: u for u in units}
    reverse = {v: k for k, v in refs.items()}
    unsupported = {refs[r] for r in check["unsupported_unit_ids"]}
    kinds = {refs[r["unit_id"]]: r["response_kind"] for r in check["response_kinds"]}
    approved, scope, keep, issues = set(), {}, set(), []
    for row in check["coverage"]:
        cid = aliases[row["clause_id"]]
        source = next(c for c in clauses if c["clause_id"] == cid)
        covered_ids = [refs[r] for r in row["unit_ids"]]
        if any(by_id[uid]["clause_id"] != cid for uid in covered_ids):
            raise PlanningRejected("PLAN_CHECK_CROSS_CLAUSE", "planning_check")
        if row["kind"] != "request" and covered_ids:
            raise PlanningRejected("PLAN_CHECK_CONSTRAINT_REFERENCES", "planning_check")
        if row["covered"] is not True or (row["kind"] == "request" and not covered_ids):
            issues.append(
                {
                    "code": "PLAN_CLAUSE_INCOMPLETE",
                    "source_ref": row["clause_id"],
                    "question_id": source["question_id"],
                }
            )
        if row["kind"] == "request":
            keep.update(uid for uid, u in by_id.items() if u["clause_id"] == cid)
            if row["covered"] is True:
                approved.update(uid for uid in covered_ids if uid not in unsupported)
        elif row["kind"] == "constraint" and row["covered"] is True:
            scope.setdefault(source["question_id"], []).append(source["source_quote"])
    for uid in keep:
        if uid not in approved:
            issues.append(
                {"code": "PLAN_REQUIREMENT_UNSUPPORTED", "unit_ref": reverse[uid]}
            )
    for row in check["target_checks"]:
        if row["explicit"] is not True:
            issues.append(
                {"code": "PLAN_TARGET_NOT_EXPLICIT", "question_id": row["question_id"]}
            )
    if check["missing_requests"]:
        issues.append({"code": "PLAN_MISSING_REQUESTS"})
    kept = [
        {**u, "response_kind": kinds[u["unit_id"]]}
        for u in units
        if u["unit_id"] in keep
    ]
    for spec in specs:
        if spec["required"] and not any(
            u["question_id"] == spec["question_id"] for u in kept
        ):
            issues.append(
                {
                    "code": "PLAN_REQUIRED_QUESTION_EMPTY",
                    "question_id": spec["question_id"],
                }
            )
    if issues:
        raise PlanningRejected("PLAN_INTENT_INCOMPLETE", "planning_semantic", issues)
    decoded = {
        **check,
        "coverage": [
            {
                **c,
                "clause_id": aliases[c["clause_id"]],
                "unit_ids": [refs[r] for r in c["unit_ids"]],
            }
            for c in check["coverage"]
        ],
        "unsupported_unit_ids": [refs[r] for r in check["unsupported_unit_ids"]],
        "response_kinds": [
            {**r, "unit_id": refs[r["unit_id"]]} for r in check["response_kinds"]
        ],
    }
    return kept, scope, decoded


async def make_anchored_plan(tools, specs):
    clauses = source_clauses(specs)
    aliases = {f"C{i}": c["clause_id"] for i, c in enumerate(clauses, 1)}
    short = [{**c, "clause_id": ref} for ref, c in zip(aliases, clauses)]
    repair, raw, reviews, check_only = None, None, [], False
    for attempt in range(2):
        check = None
        phase = "planning_check" if check_only else "planning"
        try:
            if not check_only:
                raw = await tools.model(
                    PLAN_SYSTEM,
                    {
                        "questions": specs,
                        "source_clauses": short,
                        **({"repair": repair} if repair else {}),
                    },
                    max_tokens=4000,
                    schema=AnchoredPlan,
                    stage="planning" if attempt == 0 else "planning_repair",
                )
                raw = deepcopy(raw)
                raw["targets"] = list(
                    {
                        (t["question_id"], t["name"].casefold()): t
                        for t in raw["targets"]
                    }.values()
                )
                audit(tools, "planning" if attempt == 0 else "planning_repair", raw)
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
            reviews.append(decoded)
            queries, counts = [], {}
            for q in raw["queries"]:
                qid = q["question_id"]
                if counts.get(qid, 0) < 2 and q not in queries:
                    queries.append(q)
                    counts[qid] = counts.get(qid, 0) + 1
            raw.update(
                queries=queries,
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
                    "method": "short_source_bounded_repair_v3",
                    "source_clauses": clauses,
                    "reviews": reviews,
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
                else PlanningRejected(
                    "PLAN_STRUCTURED_OUTPUT",
                    phase,
                )
            )
            rejected(tools, exc, attempt + 1)
            if attempt:
                if exc is error:
                    raise
                raise exc from error
            check_only = exc.stage == "planning_check"
            repair = {
                "previous_candidate": check if check_only else raw,
                "issues": exc.issues,
                "review": check if not check_only else None,
            }
    raise PlanningRejected("PLAN_NOT_COMPLETED")
