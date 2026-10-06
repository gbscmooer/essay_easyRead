"""Preserve source requests first; produce search navigation independently.

Only the new revision imports this module. No model supplies question ownership,
requirement IDs, answer outlines, or a remembered list of papers or techniques.
"""

from collections import Counter
from copy import deepcopy
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from ... import llm

from .intent_v11 import anchor_requests
from .intent_v12 import PlanningRejected, audit, rejected, source_clauses


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


SourceText = Annotated[str, Field(min_length=2, max_length=500)]
TargetText = Annotated[str, Field(min_length=1, max_length=80)]


class ClauseDraft(StrictModel):
    clause_id: str = Field(description="从本次source_clauses逐字选择C短编号；问题归属由程序确定")
    kind: Literal["request", "constraint", "context"] = Field(
        description="需要文献回答为request；纯范围/截止/不排名为constraint；纯背景为context；请求和范围同句仍为request"
    )
    requests: list[SourceText] = Field(
        max_length=8,
        description="每项是本C原句的连续片段，保留限定，不改写、不补答案；不同事实解释与新场景建议分别保留；纯范围/背景填[]",
    )
    scope: list[SourceText] = Field(
        max_length=8,
        description="本C逐字范围片段，例如只讨论图像/截止日期/不做排名；混合句的限定也保留；没有填[]",
    )
    targets: list[TargetText] = Field(
        max_length=6,
        description="本C逐字点名的具体方法/系统/数据集，例如AlphaNet；通用主题如缺陷检测/队列/图像不是点名对象，没有填[]",
    )


class IntentDraft(StrictModel):
    clauses: list[ClauseDraft] = Field(
        min_length=1, max_length=120,
        description="每个expected_source_refs恰好一次；局部修补只输出指定C，其他C由程序保留",
    )


class KindReview(StrictModel):
    unit_id: str = Field(description="只选择本C给定的R短编号，不新增编号")
    response_kind: Literal["fact", "inference", "mixed"] = Field(
        description="已有论文的机制/训练/测试/实验为fact；用户新场景选择、迁移核查建议为inference；同R混合事实和建议时mixed，程序交回原C重拆"
    )


class ClauseReview(StrictModel):
    clause_id: str = Field(description="每个给定C恰好一次")
    requests_complete: bool = Field(description="只判断原句请求是否完整保留；当前尚未检索，完整复述原句即可，不要求事实答案")
    scope_complete: bool = Field(description="原句模式、数据角色、训练/推理限定及范围未丢失；纯范围应在scope保留")
    classification_correct: bool = Field(description="request/constraint/context身份正确，真正研究请求不可标成背景或范围")
    named_targets_complete: bool = Field(description="原句逐字点名具体对象均保留，没有点名则空列表正确")
    targets_explicit: bool = Field(description="候选targets全部是明确点名对象；通用主题即使逐字存在也不能当点名对象")
    unsupported_unit_ids: list[str] = Field(max_length=8, description="仅本C真实R，列新增/误解或不应当论文请求的单元；没有填[]")
    missing_details: list[SourceText] = Field(max_length=8, description="原句确实漏掉的连续片段；不填领域答案，完整则[]")
    response_kinds: list[KindReview] = Field(max_length=8, description="本C每R恰好一次；纯范围/背景没有R，填[]")
    reason: str = Field(max_length=300, description="解释保留/遗漏判断，不评价尚未形成的论文答案")


class IntentReview(StrictModel):
    clauses: list[ClauseReview] = Field(min_length=1, max_length=120)


class SearchQuery(StrictModel):
    query: str = Field(min_length=2, max_length=160, description="2–12词英文检索关键词，只导航用户所问主题，不填写研究答案或未点名方法名单")


class QuestionQueries(StrictModel):
    question_ref: str = Field(description="逐字选择本次给定Q编号；实际question_id由程序绑定")
    queries: list[SearchQuery] = Field(min_length=2, max_length=4)


class QueryDraft(StrictModel):
    questions: list[QuestionQueries] = Field(min_length=1, max_length=8)


PLAN_SYSTEM = (
    "当前是检索前的需求保留，不是回答问题。根输出必须是JSON对象{clauses:[...]}，禁止只输出数组。只逐原句C分类并摘取连续原句片段，不输出question_id或R编号。"
    "requests只保留需要文献回答的请求，同句的事实解释和新场景建议必须拆为不同连续原句片段（至少两个requests项），不能删数据角色、模式、训练/推理、条件、比较或用户标准。"
    "scope保留纯范围、截止、模态、不排名；请求和范围同句kind仍为request。纯constraint/context的requests=[]。"
    "targets仅逐字点名的具体方法/系统/数据集，普通主题即使出现在原句也不能当targets；没有点名填[]。"
    "不生成论文、领域路线、结论、展示提纲或阅读导航。局部repair只修给定C，合法其他C由程序保留。"
)
CHECK_SYSTEM = (
    "根输出必须是JSON对象{clauses:[...]}。独立检查检索前的需求保留，不是答案质量或证据充分性检查。当前没有检索或阅读论文。"
    "requests完整保留原句就可complete=true；不能要求它已提供路线事实、代表论文或机制结论。"
    "核对原句每项要求及限定是否保留，新增/误解须列真实同C的unsupported R，遗漏写原句片段missing_details。"
    "核对请求/范围/背景身份与点名对象，不能把难回答请求当背景；通用主题不是明确点名对象。"
    "targets是逐C字面点名提取；同问题其他C已经点名的对象可被后句‘两者/该方法’指代，"
    "后句保留代词与请求即可，不要求把其他C名称再填进本C targets；named_targets_complete只查本C字面点名。"
    "每个给定R都恰好一个fact/inference身份，包括用户要求区分结果或给出材料依据的R；不能因它是输出要求就遗漏编号。解释已有论文训练/测试机制、查找某条件的既有实现并区分完整/部分实现是fact；用户新场景选择或迁移建议是inference。若一个给定R确实混合两种身份，填mixed交回原C拆分，不发明R2a/R2b或其他编号。"
    "仅使用给定C与同C的R，未知或跨句引用会被程序拒绝；修补仍依原句，不把旧检查当论文事实。"
)
QUERY_SYSTEM = (
    "根输出必须是JSON对象{questions:[...]}。仅将已冻结的用户请求转为2–12词的简洁英文检索导航，不回答领域事实。每给定Q恰好一个对象。"
    "overview输出3–4条互补导航，涵盖任务综述/原研究机制/用户条件；其他角色2–4条，以需求决定条数，不凑重复词。"
    "每条query保留具体任务领域与模态锚，用同义导航提高召回；不能只写representative papers、evidence gaps或domain adaptation这类通用词。保留模式及数据角色，但不要所有限定串AND。"
    "只生成每个给定Q的query关键词；程序将检索导航归属于本Q的全部真实原始请求，不输出C/R/source_ref，范围/背景不构成新研究任务。"
    "除用户已明确点名targets外，不凭记忆填具体方法/论文/数据集；不预置领域技术路线名单。"
    "repair只修指定Q查询；需求、范围、对象、事实/推断身份已经冻结，不可重写。"
)

EXAMPLES = [
    {
        "source": "解释队列如何保持顺序。给出新部署的核查建议。只讨论单线程，不做性能排名。",
        "correct": {"clauses": [
            {"clause_id": "C1", "kind": "request", "requests": ["解释队列如何保持顺序"], "scope": [], "targets": []},
            {"clause_id": "C2", "kind": "request", "requests": ["给出新部署的核查建议"], "scope": [], "targets": []},
            {"clause_id": "C3", "kind": "constraint", "requests": [], "scope": ["只讨论单线程，不做性能排名"], "targets": []},
        ]},
        "wrong": {"targets": ["队列"], "requests": ["队列利用环形数组达到O(1)性能"]},
        "why_wrong": "主题被当点名对象；新增原句没有的记忆事实及性能要求",
    },
    {
        "source": "Explain AlphaNet training from normal images and test-time scoring; propose checks for a new production line.",
        "correct": {"clauses": [
            {"clause_id": "C1", "kind": "request", "requests": ["Explain AlphaNet training from normal images and test-time scoring"], "scope": [], "targets": ["AlphaNet"]},
            {"clause_id": "C2", "kind": "request", "requests": ["propose checks for a new production line."], "scope": [], "targets": []},
        ]},
        "review": "第一个R为fact，第二个R为inference；不是因test-time就把机制解释标成inference",
    },
    {
        "source": "比较既有数据库事务隔离机制，只讨论单机部署。",
        "correct": {"clauses": [{"clause_id": "C1", "kind": "request", "requests": ["比较既有数据库事务隔离机制"], "scope": ["只讨论单机部署"], "targets": []}]},
        "review": "机制比较已保留即可requests_complete=true，尚未提供数据库事实答案不是遗漏；单机范围须保留",
    },
    {
        "source": "解释队列的顺序机制，并给出新部署的核查建议。",
        "correct": {"clauses": [{"clause_id": "C1", "kind": "request", "requests": ["解释队列的顺序机制", "给出新部署的核查建议"], "scope": [], "targets": []}]},
        "review": "程序给这两个requests分配R1/R2，各为fact/inference；若候选把整句合成唯一R1，检查只给R1=mixed，程序重拆本C。不发明R1a/R1b，也不删除这两个真实要求。",
    },
    {
        "source": "比较AlphaNet与BetaNet的正常参考构建。说明两者的推理评分差异。",
        "correct": {"clauses": [
            {"clause_id": "C1", "kind": "request", "requests": ["比较AlphaNet与BetaNet的正常参考构建"], "scope": [], "targets": ["AlphaNet", "BetaNet"]},
            {"clause_id": "C2", "kind": "request", "requests": ["说明两者的推理评分差异"], "scope": [], "targets": []},
        ]},
        "review": "C2的两者指代同问题C1点名的AlphaNet和BetaNet；requests保留代词原句即可。C2没有逐字点名，targets=[]正确；named_targets_by_question已保留两个对象，不要求C2重复跨句填写名称。",
    },
]


def _once(values, expected, code, stage):
    if Counter(values) != Counter({value: 1 for value in expected}):
        raise PlanningRejected(code, stage)


def _field_errors(error):
    if not isinstance(error, ValidationError):
        return []
    return [
        {"path": ".".join(map(str, row["loc"])), "type": row["type"],
         "limits": {k: v for k, v in (row.get("ctx") or {}).items() if k in ("min_length", "max_length", "expected")}}
        for row in error.errors(include_input=False, include_context=True)[:20]
    ]


class Calls:
    """Hard bound across independent phases, including their local repairs."""
    def __init__(self, tools):
        self.tools, self.used = tools, 0

    async def model(self, system, context, schema, stage, max_tokens):
        if self.used >= 8:
            raise PlanningRejected("PLAN_CALL_BUDGET", stage)
        self.used += 1
        result = await self.tools.model(system, context, schema=schema, stage=stage, max_tokens=max_tokens)
        # Preserve the original returned object before program normalization.
        audit(self.tools, stage, result)
        return schema.model_validate(result).model_dump()


def _source_bound(candidate, sources, expected):
    rows = candidate["clauses"]
    _once([row["clause_id"] for row in rows], expected, "PLAN_SOURCE_ALIAS", "planning")
    normalized = []
    for row in rows:
        ref = row["clause_id"]
        quote = sources[ref]["source_quote"]
        for field in ("requests", "scope", "targets"):
            if any(text not in quote for text in row[field]):
                raise PlanningRejected("PLAN_SOURCE_TEXT", "planning", [{"code": "PLAN_SOURCE_TEXT", "source_ref": ref}])
        if (row["kind"] == "request") != bool(row["requests"]):
            raise PlanningRejected("PLAN_CLAUSE_CLASSIFICATION", "planning", [{"code": "PLAN_CLAUSE_CLASSIFICATION", "source_ref": ref}])
        if row["kind"] == "constraint" and not row["scope"]:
            raise PlanningRejected("PLAN_SCOPE_EMPTY", "planning", [{"code": "PLAN_SCOPE_EMPTY", "source_ref": ref}])
        normalized.append({**row, **{
            field: list(dict.fromkeys(row[field])) for field in ("requests", "scope", "targets")
        }})
    return {"clauses": normalized}


def _flatten(candidate, sources, aliases, specs):
    requests, targets, scope, numbered = [], [], {}, []
    for row in candidate["clauses"]:
        ref = row["clause_id"]
        qid = sources[ref]["question_id"]
        rows = []
        for text in row["requests"]:
            named_subjects = [name for name in row["targets"] if name.casefold() in text.casefold()]
            requests.append({"question_id": qid, "clause_id": aliases[ref], "requirement": text,
                             "subject": named_subjects[0] if len(named_subjects) == 1 else "",
                             "phase": "general", "mode": "", "response_kind": "fact"})
            rows.append({"unit_id": f"R{len(requests)}", "requirement": text})
        targets.extend({"question_id": qid, "name": text} for text in row["targets"])
        scope.setdefault(qid, []).extend(row["scope"])
        numbered.append({**row, "question_id": qid, "requests": rows})
    targets = list({(row["question_id"], row["name"].casefold()): row for row in targets}.values())
    scope = {qid: list(dict.fromkeys(items)) for qid, items in scope.items()}
    try:
        units = anchor_requests(requests, list(sources.values()), specs, targets)
    except ValueError as error:
        raise PlanningRejected("PLAN_REQUIREMENTS", "planning") from error
    if len(units) != len(requests):
        raise PlanningRejected("PLAN_DUPLICATE_REQUIREMENT", "planning")
    refs = {f"R{i}": unit["unit_id"] for i, unit in enumerate(units, 1)}
    return units, targets, scope, numbered, refs


def _review(review, numbered, sources):
    stage = "planning_check"
    expected = {row["clause_id"]: row for row in numbered}
    _once([row["clause_id"] for row in review["clauses"]], expected, "PLAN_CHECK_CLAUSE_COVERAGE", stage)
    all_refs = {r["unit_id"] for row in numbered for r in row["requests"]}
    rejected_sources, issues, kinds = [], [], {}
    for row in review["clauses"]:
        ref = row["clause_id"]
        units = {r["unit_id"] for r in expected[ref]["requests"]}
        supplied = [r["unit_id"] for r in row["response_kinds"]]
        cited = [*supplied, *row["unsupported_unit_ids"]]
        if any(unit not in all_refs for unit in cited):
            raise PlanningRejected("PLAN_CHECK_UNKNOWN_UNIT", stage, [{"source_ref": ref, "field": "response_kinds/unsupported_unit_ids", "allowed_unit_refs": sorted(units), "instruction": "恰好使用本C给定编号；不得新造a/b子编号，混合事实/建议的给定R填mixed"}])
        if any(unit not in units for unit in cited):
            raise PlanningRejected("PLAN_CHECK_CROSS_CLAUSE", stage)
        if Counter(supplied) != Counter({unit: 1 for unit in units}):
            raise PlanningRejected("PLAN_CHECK_KIND_COVERAGE", stage, [{"source_ref": ref, "field": "response_kinds", "allowed_unit_refs": sorted(units), "instruction": "每个给定R恰好一次；无点名对象或输出要求不意味着可以漏R"}])
        if any(text not in sources[ref]["source_quote"] for text in row["missing_details"]):
            raise PlanningRejected("PLAN_CHECK_SOURCE_TEXT", stage, [{"source_ref": ref,
                "field": "missing_details", "instruction": "只摘取当前C真正漏掉的连续原句片段；不写理由或修复建议，无遗漏填[]"}])
        mixed = any(r["response_kind"] == "mixed" for r in row["response_kinds"])
        if mixed:
            rejected_sources.append(ref)
            issues.append({"code": "PLAN_REQUEST_UNIT_MIXED", "source_ref": ref, "question_id": sources[ref]["question_id"]})
        if not all(row[key] is True for key in ("requests_complete", "scope_complete", "classification_correct", "named_targets_complete", "targets_explicit")) or row["unsupported_unit_ids"] or row["missing_details"]:
            rejected_sources.append(ref)
            issues.append({"code": "PLAN_CLAUSE_INCOMPLETE", "source_ref": ref, "question_id": sources[ref]["question_id"]})
        kinds.update({r["unit_id"]: r["response_kind"] for r in row["response_kinds"]})
    return list(dict.fromkeys(rejected_sources)), kinds, issues


def _role(text, targets):
    if re.search(r"实现了吗|是否.{0,24}(?:已有|存在|实现)|有没有.{0,24}(?:研究|方法|实现)|查新|idea|implemented|implementation|prior art", text, re.I):
        return "implementation"
    if re.search(r"技术路线|研究现状|主要路线|哪些主要|概览|综述|survey|taxonomy|overview|major.{0,12}(approach|route)", text, re.I):
        return "overview"
    if targets or re.search(r"解释|说明|比较|怎样|如何|explain|compare|how", text, re.I):
        return "mechanism"
    return "overview"


def _query_rows(candidate, specs, question_refs, numbered, roles, expected):
    rows = candidate["questions"]
    supplied = Counter(row["question_ref"] for row in rows)
    if supplied != Counter({ref: 1 for ref in expected}):
        bad = [ref for ref in expected if supplied[ref] != 1] or list(expected)
        raise PlanningRejected("PLAN_QUERY_COVERAGE", "planning_queries", [{"code": "PLAN_QUERY_COVERAGE", "question_id": question_refs[ref], "allowed_question_refs": list(expected)} for ref in bad])
    request_owners = {row["question_id"] for row in numbered if row["kind"] == "request"}
    queries = []
    by_question = {row["question_id"]: row["role"] for row in roles}
    for row in rows:
        qref = row["question_ref"]
        qid = question_refs[qref]
        minimum = 3 if by_question[qid] == "overview" else 2
        unique = []
        for query in row["queries"]:
            text = " ".join(query["query"].split())
            if qid not in request_owners:
                raise PlanningRejected("PLAN_QUERY_SOURCE", "planning_queries", [{"code": "PLAN_QUERY_SOURCE", "question_id": qid}])
            if not 2 <= len(re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*", text)) <= 12 or re.search(r"[\u3400-\u9fff]", text):
                raise PlanningRejected("PLAN_QUERY_FORMAT", "planning_queries", [{"code": "PLAN_QUERY_FORMAT", "question_id": qid}])
            if text.casefold() not in {value.casefold() for value in unique}:
                unique.append(text)
        if len(unique) < minimum:
            raise PlanningRejected("PLAN_QUERY_COVERAGE", "planning_queries", [{"code": "PLAN_QUERY_COVERAGE", "question_id": qid}])
        queries.extend({"question_id": qid, "query": text} for text in unique)
    return queries


async def make_anchored_plan(tools, specs):
    """Build a fail-closed source ledger with at most eight model-stage calls.

    Tools.model may itself use two structured-output attempts. Its actual paid
    attempts and token reservations remain governed by the run's frozen budget.
    """
    clauses = source_clauses(specs)
    if not clauses:
        raise PlanningRejected("PLAN_SOURCE_EMPTY")
    aliases = {f"C{i}": row["clause_id"] for i, row in enumerate(clauses, 1)}
    sources = {ref: row for ref, row in zip(aliases, clauses)}
    short = [{**row, "clause_id": ref} for ref, row in sources.items()]
    calls = Calls(tools)
    common = {"workflow_phase": "before_retrieval_requirement_validation", "coverage_means": "用户请求保留，不是领域答案已完成", "examples": EXAMPLES}
    repairs, original_reviews, repair_details = 0, [], []

    # Structural/source mistakes only retry this small extraction stage.
    candidate, feedback, retained_draft = None, None, None
    expected_sources = list(aliases)
    for attempt in range(2):
        result = None
        try:
            result = await calls.model(PLAN_SYSTEM, {**common,
                "source_clauses": [row for row in short if row["clause_id"] in expected_sources],
                "expected_source_refs": expected_sources, **({"repair": feedback} if feedback else {})},
                IntentDraft, "planning" if not attempt else "planning_repair", 4000)
            candidate = _source_bound(result, sources, expected_sources)
            if retained_draft:
                joined = {row["clause_id"]: row for row in [*retained_draft["clauses"], *candidate["clauses"]]}
                candidate = {"clauses": [joined[ref] for ref in aliases]}
            break
        except llm.LLMError:
            raise
        except ValueError as error:
            exc = error if isinstance(error, PlanningRejected) else PlanningRejected("PLAN_STRUCTURED_OUTPUT")
            rejected(tools, exc, calls.used)
            if attempt or exc.code == "PLAN_CALL_BUDGET":
                raise exc from error
            repairs += 1
            feedback = {"instruction": "只按字段错误与原句恢复本阶段完整JSON，不生成查询或事实答案", "code": exc.code,
                        "field_errors": _field_errors(error), "issues": exc.issues}
            # JSON-valid rows can be checked individually. An invalid request or
            # target on one C must not cause regeneration of legal other Cs.
            if result and Counter(row["clause_id"] for row in result["clauses"]) == Counter({ref: 1 for ref in aliases}):
                retained, bad_rows = [], []
                for row in result["clauses"]:
                    try:
                        retained.extend(_source_bound({"clauses": [row]}, sources, [row["clause_id"]])["clauses"])
                    except PlanningRejected:
                        bad_rows.append(row)
                if bad_rows:
                    retained_draft = {"clauses": retained}
                    expected_sources = [row["clause_id"] for row in bad_rows]
                    feedback.update(instruction="仅修指定C的字段/原句锚定，其他合法C由程序保留", previous_candidate=bad_rows)
                    repair_details.append({"stage": "planning_repair", "source_refs": expected_sources})

    # A format/reference repair retries only the review; semantic repair replaces
    # only the rejected source clauses. Previously legal clauses remain intact.
    review_format_repaired = False
    for semantic_attempt in range(2):
        units, targets, scope, numbered, refs = _flatten(candidate, sources, aliases, specs)
        review_feedback = None
        while True:
            try:
                review = await calls.model(CHECK_SYSTEM, {**common, "clauses": numbered,
                    "source_clauses": short,
                    "unit_refs_by_clause": {row["clause_id"]: [r["unit_id"] for r in row["requests"]] for row in numbered},
                    "named_targets_by_question": {s["question_id"]: [t["name"] for t in targets if t["question_id"] == s["question_id"]] for s in specs},
                    **({"repair": review_feedback} if review_feedback else {})},
                    IntentReview, "planning_check" if not semantic_attempt and not review_feedback else "planning_check_repair", 5000)
                original_reviews.append(deepcopy(review))
                bad, kinds, issues = _review(review, numbered, sources)
                break
            except llm.LLMError:
                raise
            except ValueError as error:
                exc = error if isinstance(error, PlanningRejected) else PlanningRejected("PLAN_STRUCTURED_OUTPUT", "planning_check")
                rejected(tools, exc, calls.used)
                if review_format_repaired or exc.code == "PLAN_CALL_BUDGET":
                    raise exc from error
                review_format_repaired, repairs = True, repairs + 1
                review_feedback = {"instruction": "只修本次检查JSON或给定C/R引用，不改已冻结原句需求，不要求答案事实",
                    "code": exc.code, "field_errors": _field_errors(error), "issues": exc.issues}
        empty = [spec["question_id"] for spec in specs if spec["required"] and not any(unit["question_id"] == spec["question_id"] for unit in units)]
        if empty:
            bad = list(dict.fromkeys([*bad, *(ref for ref, source in sources.items() if source["question_id"] in empty)]))
            issues.extend({"code": "PLAN_REQUIRED_QUESTION_EMPTY", "question_id": qid} for qid in empty)
        if not bad:
            units = [{**unit, "response_kind": kinds[ref]} for ref, unit in zip(refs, units)]
            break
        error = PlanningRejected("PLAN_INTENT_INCOMPLETE", "planning_semantic", issues)
        rejected(tools, error, calls.used)
        if semantic_attempt:
            raise error
        try:
            patch = await calls.model(PLAN_SYSTEM, {**common, "source_clauses": [row for row in short if row["clause_id"] in bad],
                "expected_source_refs": bad, "repair": {"instruction": "只重写指定C；混合事实和新场景建议的原句须拆成不同requests连续片段，不丢完整请求/范围/对象，其他C由程序保留",
                    "previous_candidate": [row for row in candidate["clauses"] if row["clause_id"] in bad],
                    "review": [row for row in review["clauses"] if row["clause_id"] in bad]}}, IntentDraft, "planning_clause_repair", 4000)
            patch = _source_bound(patch, sources, bad)
        except llm.LLMError:
            raise
        except ValueError as patch_error:
            exc = patch_error if isinstance(patch_error, PlanningRejected) else PlanningRejected("PLAN_STRUCTURED_OUTPUT", "planning_clause_repair")
            rejected(tools, exc, calls.used)
            raise exc from patch_error
        replacements = {row["clause_id"]: row for row in patch["clauses"]}
        candidate = {"clauses": [replacements.get(row["clause_id"], row) for row in candidate["clauses"]]}
        repair_details.append({"stage": "planning_clause_repair", "source_refs": bad})
        repairs += 1

    roles = [{"question_id": spec["question_id"], "role": _role(spec["text"], [target for target in targets if target["question_id"] == spec["question_id"]])} for spec in specs]
    qrefs = {f"Q{i}": spec["question_id"] for i, spec in enumerate(specs, 1)}
    query_context = [
        {"question_ref": qref, "text": next(spec["text"] for spec in specs if spec["question_id"] == qid),
         "reading_role": next(role["role"] for role in roles if role["question_id"] == qid),
         "requests": [row for row in numbered if row["question_id"] == qid and row["kind"] == "request"],
         "scope": scope.get(qid, []), "targets": [target["name"] for target in targets if target["question_id"] == qid]}
        for qref, qid in qrefs.items()
    ]
    query_candidate, query_feedback, expected = None, None, list(qrefs)
    for attempt in range(2):
        try:
            wire = await calls.model(QUERY_SYSTEM, {"questions": [row for row in query_context if row["question_ref"] in expected],
                "expected_question_refs": expected, **({"repair": query_feedback} if query_feedback else {})},
                QueryDraft, "planning_queries" if not attempt else "planning_queries_repair", 4000)
            generated = _query_rows(wire, specs, qrefs, numbered, roles, expected)
            if query_candidate:
                retained = [row for row in query_candidate["questions"] if row["question_ref"] in qrefs and row["question_ref"] not in expected]
                query_candidate = {"questions": [*retained, *wire["questions"]]}
                generated = _query_rows(query_candidate, specs, qrefs, numbered, roles, qrefs)
            queries = generated
            break
        except llm.LLMError:
            raise
        except ValueError as error:
            exc = error if isinstance(error, PlanningRejected) else PlanningRejected("PLAN_STRUCTURED_OUTPUT", "planning_queries")
            rejected(tools, exc, calls.used)
            if attempt or exc.code == "PLAN_CALL_BUDGET":
                raise exc from error
            repairs += 1
            if isinstance(error, PlanningRejected) and error.issues and "wire" in locals():
                qids = {issue.get("question_id") for issue in error.issues}
                bad_refs = [ref for ref, qid in qrefs.items() if qid in qids]
                if bad_refs:
                    query_candidate, expected = deepcopy(wire), bad_refs
            query_feedback = {"instruction": "仅修指定Q查询导航，不改需求/范围/对象，不生成事实答案", "code": exc.code,
                "field_errors": _field_errors(error), "issues": exc.issues}
            repair_details.append({"stage": "planning_queries_repair", "question_refs": list(expected)})

    requests = [{key: value for key, value in unit.items() if key in ("question_id", "clause_id", "requirement", "subject", "phase", "mode", "response_kind")} for unit in units]
    return {
        "queries": queries, "targets": targets, "requests": requests, "reading_roles": roles,
        # Original requests remain the compatibility display outline. Literature
        # reading navigation is deliberately deferred to retrieved materials.
        "answer_units": list(dict.fromkeys(unit["requirement"] for unit in units))[:4],
        "reading_queries": [], "strategy": "material_first_v3", "feedback_policy": "gap_followup_v2",
        "requirement_ledger": {spec["question_id"]: [unit for unit in units if unit["question_id"] == spec["question_id"]] for spec in specs},
        "constraints": {spec["question_id"]: scope.get(spec["question_id"], []) for spec in specs},
        "intent_coverage": {"method": "source_request_navigation_v6", "source_clauses": clauses,
            "reviews": original_reviews, "gaps_by_question": {}, "complete": True,
            "repairs_used": repairs, "source_aliases": aliases, "local_repairs": repair_details,
            "query_source_refs_by_question": {spec["question_id"]: [row["clause_id"] for row in clauses if row["question_id"] == spec["question_id"] and any(u["clause_id"] == row["clause_id"] for u in units)] for spec in specs},
            "model_stage_calls": calls.used, "maximum_model_stage_calls": 8},
    }
