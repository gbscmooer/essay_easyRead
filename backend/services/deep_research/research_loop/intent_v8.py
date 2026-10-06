"""User-source anchored requirements, with a bounded independent coverage repair."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import FocusedSearchPlan, digest
from .requirements import GENERIC_POLICY


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str
    clause_id: str
    requirement: str = Field(min_length=2, max_length=500)
    subject: str = Field("", max_length=80)
    phase: Literal["general", "training", "inference"] = "general"
    mode: str = Field("", max_length=80)


class AnchoredPlan(FocusedSearchPlan):
    requests: list[Request] = Field(min_length=1, max_length=72)


class ClauseCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clause_id: str
    kind: Literal["request", "constraint", "context"]
    covered: bool
    unit_ids: list[str] = Field(max_length=72)
    reason: str = Field(max_length=400)


class IntentCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    coverage: list[ClauseCheck] = Field(max_length=120)
    unsupported_unit_ids: list[str] = Field(max_length=72)
    missing_requests: list[Request] = Field(max_length=72)


def source_clauses(specs):
    clauses = []
    for spec in specs:
        inputs = [("question", spec["text"], None)] + [("criterion", c, i)
            for i, c in enumerate(spec["answer_criteria"]) if c != GENERIC_POLICY]
        for origin, text, criterion_index in inputs:
            for match in re.finditer(r"[^。！？;；\n]+", text):
                quote = match.group().strip()
                if not quote:
                    continue
                start = match.start() + len(match.group()) - len(match.group().lstrip())
                row = {"question_id": spec["question_id"], "origin": origin, "criterion_index": criterion_index,
                    "start": start, "end": start + len(quote), "source_quote": quote}
                clauses.append({"clause_id": "src_" + digest(row)[:20], **row})
    if len(clauses) > 120:
        raise ValueError("原问句分句超出可核查范围")
    return clauses


def anchor_requests(requests, clauses, specs, targets):
    sources = {c["clause_id"]: c for c in clauses}
    questions = {s["question_id"]: s for s in specs}
    names = {(t["question_id"], t["name"].casefold()) for t in targets}
    anchored = {}
    for request in requests:
        source = sources.get(request["clause_id"])
        spec = questions.get(request["question_id"])
        if not source or not spec or source["question_id"] != request["question_id"]:
            raise ValueError("需求必须绑定同一问题的原文分句")
        subject, mode = request.get("subject", ""), request.get("mode", "")
        if subject and (request["question_id"], subject.casefold()) not in names:
            raise ValueError("需求对象必须是用户点名目标")
        if mode and mode.casefold() not in spec["text"].casefold() and mode.casefold() not in source["source_quote"].casefold():
            raise ValueError("需求模式必须来自用户原文")
        index = source["criterion_index"]
        if index is None:
            index = spec["answer_criteria"].index(GENERIC_POLICY) if GENERIC_POLICY in spec["answer_criteria"] else 0
        key = {**request, "phase": request.get("phase", "general"), "mode": mode, "subject": subject}
        unit_id = "req_" + digest(key)[:20]
        anchored[unit_id] = {"unit_id": unit_id, **key, "criterion_index": index,
            "source_quote": source["source_quote"], "source_origin": source["origin"],
            "source_start": source["start"], "source_end": source["end"]}
    if len(anchored) > 72:
        raise ValueError("细分需求超出可核查范围")
    return list(anchored.values())


def coverage_gaps(check, clauses, units):
    by_clause, known = {}, {u["unit_id"]: u for u in units}
    for row in check["coverage"]:
        by_clause.setdefault(row["clause_id"], []).append(row)
    if any(uid not in known for uid in check["unsupported_unit_ids"]):
        raise ValueError("需求检查引用未知单元")
    if any(cid not in {c["clause_id"] for c in clauses} for cid in by_clause):
        raise ValueError("需求检查引用未知原文分句")
    gaps, approved = {}, set()
    for clause in clauses:
        checks = by_clause.get(clause["clause_id"], [])
        row = checks[0] if len(checks) == 1 else {}
        valid = bool(row) and row.get("covered") is True and all(
            uid in known and known[uid]["clause_id"] == clause["clause_id"] and uid not in check["unsupported_unit_ids"]
            for uid in row.get("unit_ids", []))
        if row.get("kind") == "request" and not row.get("unit_ids"):
            valid = False
        if not valid:
            gaps.setdefault(clause["question_id"], []).append("需求拆解尚未完整核查：" + clause["source_quote"])
        else:
            approved.update(row.get("unit_ids", []))
    for uid in check["unsupported_unit_ids"]:
        gaps.setdefault(known[uid]["question_id"], []).append("需求可能扩张或误解：" + known[uid]["requirement"])
    for uid, unit in known.items():
        if uid not in approved and uid not in check["unsupported_unit_ids"]:
            gaps.setdefault(unit["question_id"], []).append("需求尚未获原文覆盖检查确认：" + unit["requirement"])
    return gaps


async def make_anchored_plan(tools, specs):
    clauses = source_clauses(specs)
    raw = await tools.model(
        "从用户原问句制定英文检索与逐项需求计划，不填任何领域事实。"
        "queries每问题最多两条2–8词英文主题词，最多8条；不要用recent/progress等长AND修饰，也不串联不同方法名。"
        "targets仅用户逐字点名的名称；reading_queries最多4条英文机制/条件导航。answer_units仅简短展示提纲，最多4项。"
        "requests才是必答账本，每项绑定原文clause_id与question_id，requirement只拆该分句明确要求。"
        "逐对象、训练/推理、用户点名模式及是否条件拆开；subject仅targets的名称或空字符串，"
        "phase为general/training/inference，mode只有用户原文已有的模式名称；各方法共同要求分别列，不只给其中一个方法。"
        "不要把框定范围/时间截止/不做排名拆成领域研究要求；这些作为约束在阅读中遵守。"
        "研究概览要求代表路线及机制，不要求穷尽论文/全部数值；选择与验证依用户问题列，不能偷加参数数量/指标排名。"
        "每项通常一项可核查要求；原文有自定义criterion也须逐项保留。最多72项，有多少需求写多少，不凑数。",
        {"questions": specs, "source_clauses": clauses}, max_tokens=4000, schema=AnchoredPlan, stage="planning")
    known = {s["question_id"] for s in specs}
    if any(q["question_id"] not in known for q in raw["queries"]) or any(
        not any(q["question_id"] == s["question_id"] for q in raw["queries"]) for s in specs if s["required"]):
        raise ValueError("检索计划未覆盖必答问题")
    if any(t["question_id"] not in known or t["name"].casefold() not in next(
        s["text"].casefold() for s in specs if s["question_id"] == t["question_id"]) for t in raw["targets"]):
        raise ValueError("检索目标必须来自用户原文")
    raw["targets"] = list({(t["question_id"], t["name"].casefold()): t for t in raw["targets"]}.values())
    counts, queries = {}, []
    for query in raw["queries"]:
        key = query["question_id"]
        if counts.get(key, 0) < 2 and not any(q["question_id"] == key and q["query"].casefold() == query["query"].casefold() for q in queries):
            queries.append(query)
            counts[key] = counts.get(key, 0) + 1
    raw["queries"] = queries
    units = anchor_requests(raw["requests"], clauses, specs, raw["targets"])
    reviews = []
    for attempt in range(2):
        check = await tools.model(
            "独立逐分句检查需求计划是否完整且没有扩大用户需求，不填领域答案。"
            "coverage对每个source_clauses.clause_id恰好一项；kind=request/constraint/context，covered表示该分句所有明确请求/约束已正确保留。"
            "request须列同分句真实unit_ids；constraint/context可空，但不能把问题的是否条件或阅读/比较/选择要求误标为背景。"
            "每个方法的训练与推理/参考/参数更新要求分别核查，不能只给其中一个方法；同义表达允许合并。"
            "截止只限制材料，不要求该年新论文；用户没问的数字/组成/性能排名不得扩成必须回答。"
            "unsupported_unit_ids列新增未问需求/错误模式/误解对象的真实ID。missing_requests逐项给遗漏要求，绑定原文clause_id，"
            "subject/phase/mode沿原问句，不能用记忆填方法事实。没有遗漏时missing_requests=[]。修补后仍缺就covered=false。",
            {"questions": specs, "source_clauses": clauses, "requirements": units},
            max_tokens=3500, schema=IntentCheck, stage="planning_check" if attempt == 0 else "planning_check_repair")
        reviews.append(check)
        gaps = coverage_gaps(check, clauses, units)
        if not gaps and not check["missing_requests"]:
            break
        if attempt == 0:
            kept = [u for u in units if u["unit_id"] not in check["unsupported_unit_ids"]]
            extra = anchor_requests(check["missing_requests"], clauses, specs, raw["targets"])
            units = list({u["unit_id"]: u for u in [*kept, *extra]}.values())
            if len(units) > 72:
                raise ValueError("修补需求超出可核查范围")
    if reviews[-1]["missing_requests"]:
        for request in anchor_requests(reviews[-1]["missing_requests"], clauses, specs, raw["targets"]):
            gaps.setdefault(request["question_id"], []).append("需求拆解仍有遗漏：" + request["requirement"])
    for spec in specs:
        if spec["required"] and not any(u["question_id"] == spec["question_id"] for u in units):
            gaps.setdefault(spec["question_id"], []).append("必答问题尚无已保留的需求单元")
    raw.update(strategy="material_first_v3", feedback_policy="gap_followup_v2",
        requirement_ledger={s["question_id"]: [u for u in units if u["question_id"] == s["question_id"]] for s in specs},
        intent_coverage={"method": "user_clause_coverage_v1", "source_clauses": clauses, "reviews": reviews,
            "gaps_by_question": gaps, "complete": not gaps})
    raw["reading_queries"] = [q for q in raw["reading_queries"] if len(re.findall(r"[A-Za-z]{3,}", q)) >= 2] or [
        "method architecture mechanism", "training inference data parameters"]
    return raw
