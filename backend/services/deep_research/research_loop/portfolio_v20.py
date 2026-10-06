"""Offer every readable candidate to bounded batches before complementary reading."""

from copy import deepcopy
from types import SimpleNamespace

from ....models.research_core import ResearchAsset, ResearchQuestion
from .contracts import digest
from .portfolio_v10 import latest, resolve
from .portfolio_v19 import (
    SYSTEMS as OLD_SYSTEMS, SCHEMAS as OLD_SCHEMAS, abstract_view, prepare as previous_prepare,
)
from .selection_v19 import bind_choices
from .navigation_v20 import SYSTEMS as NAV_SYSTEMS, SCHEMAS as NAV_SCHEMAS, source_context, task_context, bind_hint
from .selection_v20 import review_choices, ScopeChecks as ChoiceChecks, SYSTEM as SELECTION_SYSTEM, context_for, output_tokens
from .resources import ResourceExhausted
from ...llm import LLMError
from .diagnostics_v20 import model_error

BATCH_SIZE = 24
SYSTEMS = {**OLD_SYSTEMS, **NAV_SYSTEMS}
SCHEMAS = {**OLD_SCHEMAS, **NAV_SCHEMAS}
# M2.x includes mandatory reasoning in its output limit. Keep the explicit v20
# envelope shared by actual requests and prospective reservation estimates.
OUTPUT_LIMITS = {
    "initial": 4000,
    "after_coverage_search": 6000,
    "coverage_refresh": 4000,
    "task_recall": 4000,
}
BATCH_SYSTEM = (
    "你正在分批审视全部准入候选。本批所有candidates都应比较后再有限提名；"
    "不能因候选位置靠后而跳过。只安排后续阅读，不判原文支持或研究结论。"
    "空提名需具体解释本批材料不足；不能用记忆补充候选。"
)
MERGE_SYSTEM = (
    "这是全部成功候选批的提名汇总。比较每批提名，选择总槽位内最互补的阅读组合，"
    "不能优先第一批或把每批第一项机械拼接。其余保留为候补，未成功审视的批不能说已覆盖。"
    "提名理由仍是阅读导航，不是事实证据；只从当前candidates选择。"
)


def prepare(tools, question, stage):
    context, _, units, sources, frozen, frozen_docs, requirements = previous_prepare(tools, question, stage)
    by_family = {}
    for asset in tools.ranked_documents(question.question_id):
        payload = asset.inline_payload or {}
        if not payload.get("text", "").strip():
            continue
        family = asset.family_key or asset.asset_id
        prior = by_family.get(family)
        if prior is None or (prior.inline_payload.get("source_kind") == "abstract" and payload.get("source_kind") != "abstract"):
            by_family[family] = asset
    docs = {f"D{i}": asset for i, asset in enumerate(by_family.values(), 1)}
    navigation = " ".join(context["task_navigation"])
    if stage not in ("coverage_refresh", "task_recall"):
        context["candidates"] = [
            {"document_ref": ref, "title": asset.inline_payload["paper"].get("title"),
             "role": asset.inline_payload["paper"].get("research_role"),
             **abstract_view(asset.inline_payload["paper"].get("abstract") or asset.inline_payload.get("text", ""), navigation, 1400)}
            for ref, asset in docs.items()
        ]
    elif stage == "task_recall":
        # A task query depends on the user requirement, not a truncated paper list.
        context.pop("candidates", None)
    context["material_state"]["candidate_count"] = len(docs)
    context["material_state"]["candidate_batch_count"] = (len(docs) + BATCH_SIZE - 1) // BATCH_SIZE
    if stage == "coverage_refresh":
        context, sources = source_context(context, sources, tools.repo.asset)
    elif stage == "task_recall":
        context = task_context(context)
    return context, docs, units, sources, frozen, frozen_docs, requirements


def batches(context):
    rows = context.get("candidates", [])
    return [rows[i:i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]


def batch_context(context, rows, index, count):
    return {**context, "candidates": rows,
            "candidate_batch": {"index": index, "count": count, "size": len(rows),
                                "all_candidate_count": context["material_state"]["candidate_count"]}}


def review_documents(docs, context):
    """Scope screening uses bounded actual abstract excerpts, never invented text."""
    result = {}
    for ref, asset in docs.items():
        payload = deepcopy(asset.inline_payload)
        original = payload["paper"].get("abstract") or payload.get("text", "")
        view = abstract_view(original, " ".join(context["task_navigation"]), 4000)
        payload["paper"]["abstract"] = view["abstract"]
        payload["scope_original_text"] = original
        payload["scope_original_spans"] = view["abstract_spans"]
        result[ref] = SimpleNamespace(asset_id=asset.asset_id, family_key=asset.family_key,
                                      content_hash=asset.content_hash, inline_payload=payload)
    return result


def navigation_calls(tools, question_id, stage):
    """Maximum LLM Core attempts, including its one JSON-format recovery."""
    if stage in ("coverage_refresh", "task_recall"):
        return 4
    question = next(q for q in tools.repo.rows(ResearchQuestion) if q.question_id == question_id)
    context, *_ = prepare(tools, question, stage)
    count = len(batches(context))
    return 2 * (count + int(count > 1) + int(stage == "after_coverage_search" and count > 0))


def navigation_cost(tools, question_id, stage):
    from .answer_budget_v19 import model_cost
    question = next(q for q in tools.repo.rows(ResearchQuestion) if q.question_id == question_id)
    context, docs, *_ = prepare(tools, question, stage)
    output = OUTPUT_LIMITS[stage]
    if stage in ("coverage_refresh", "task_recall"):
        return 4 * model_cost(SYSTEMS[stage], context, SCHEMAS[stage], output) + 2500
    groups = batches(context)
    cost = sum(2 * model_cost(BATCH_SYSTEM + SYSTEMS[stage], batch_context(context, rows, i, len(groups)), SCHEMAS[stage], output)
               for i, rows in enumerate(groups, 1))
    if len(groups) > 1:
        # Bound the actual merge by at most one/six nominations per batch. Retain
        # original metadata, rather than pretending the summary is checked text.
        max_rows = 1 if stage == "initial" else 6
        pool = [row for rows in groups for row in sorted(rows, key=lambda row: len(str(row).encode()), reverse=True)[:max_rows]]
        pool_context = {**context, "candidates": pool,
                        "batch_nominations": [{"document_ref": row["document_ref"], "reason": "材" * 240} for row in pool]}
        cost += 2 * model_cost(MERGE_SYSTEM + SYSTEMS[stage], pool_context, SCHEMAS[stage], output)
    if stage == "after_coverage_search" and groups:
        # Independent prospective scope check is separate from later fact support.
        bounded = review_documents(docs, context)
        prospective = [{"asset_id": asset.asset_id} for asset in sorted(
            bounded.values(), key=lambda a: len(str(context_for(question, context, bounded,
                [{"asset_id": a.asset_id}])[0]).encode()), reverse=True)[:6]]
        check_data, _ = context_for(question, context, bounded, prospective)
        cost += 2 * model_cost(SELECTION_SYSTEM, check_data, ChoiceChecks, output_tokens(tools))
    return cost + 2500


def audit(tools, stage, context, aliases, candidate=None, rejected=(), code=None):
    payload = {"question_id": tools.resources.current_question_id, "stage": stage,
               "method": "complete_candidate_batches_v20", "input": deepcopy(context),
               "candidate": candidate, "rejections": list(rejected), "aliases": aliases, "error_code": code}
    with tools.repo.transaction() as session:
        session.add(ResearchAsset(run_id=tools.repo.run_id, content_kind="portfolio_attempt",
                                  inline_payload=payload, content_hash=digest(payload), parse_status="parsed"))


async def navigate(tools, stage, context, docs, units, sources, requirements):
    """Bind selected IDs without requiring the model to retype the source."""
    navigation, rejected, gaps = [], [], []
    aliases = {"documents": {r: a.asset_id for r, a in docs.items()}, "requirements": units,
               "source_bindings": sources if stage == "coverage_refresh" else {}}
    for attempt in range(2):
        try:
            raw = await tools.model(SYSTEMS[stage], context, max_tokens=OUTPUT_LIMITS[stage], schema=SCHEMAS[stage],
                                    stage="reading_portfolio" if not attempt else "reading_portfolio_repair")
        except (LLMError, ResourceExhausted, ValueError) as exc:
            if not navigation:
                raise
            rejected.append({"code": "PORTFOLIO_NAVIGATION_REPAIR_FAILED", **model_error(exc)})
            audit(tools, stage, context, aliases, rejected=rejected, code=model_error(exc)["error_code"])
            gaps.append("部分导航修补失败，先前合法查询已保留；不表示缺口已解决")
            break
        rejected, gaps = [], raw["gaps"]
        for idx, hint in enumerate(raw["next_searches"] if stage == "coverage_refresh" else raw["task_searches"]):
            try:
                bound = bind_hint(hint, context, units, requirements, sources, stage)
                if any(h["query"].casefold() == bound["query"].casefold() for h in navigation):
                    raise ValueError("PORTFOLIO_DUPLICATE_QUERY")
                if len(navigation) >= 2:
                    raise ValueError("PORTFOLIO_SEARCH_ALLOCATION")
                navigation.append(bound)
            except ValueError as exc:
                rejected.append({"row": idx, "code": str(exc)})
        audit(tools, stage, context, aliases, raw, rejected)
        if attempt or len(navigation) >= 2 or not rejected:
            break
        context = {**context, "repair": {"rejections": rejected, "previous_candidate": deepcopy(raw),
                                        "allowed_unit_refs": list(units), "accepted_queries": [h["query"] for h in navigation]}}
    return navigation, rejected, gaps


async def execute(tools, action):
    stage = action.query or "initial"
    if stage not in SCHEMAS:
        raise ValueError("PORTFOLIO_STAGE")
    question = next(q for q in tools.repo.rows(ResearchQuestion) if q.question_id == action.question_id)
    context, docs, units, sources, frozen, frozen_docs, requirements = prepare(tools, question, stage)
    choices, navigation, rejected, gaps, selection_batches = [], [], [], [], []
    reviewed_refs, nominations = set(), []
    if stage in ("coverage_refresh", "task_recall"):
        if stage == "task_recall" or sources:
            try:
                navigation, rejected, gaps = await navigate(tools, stage, context, docs, units, sources, requirements)
            except (LLMError, ResourceExhausted, ValueError) as exc:
                rejected.append({"code": "PORTFOLIO_NAVIGATION_FAILED", **model_error(exc)})
                audit(tools, stage, context, {"documents": {r: a.asset_id for r, a in docs.items()},
                                            "requirements": units}, code=model_error(exc)["error_code"])
        else:
            gaps = ["已读材料尚无可定位的原文检索导航"]
    else:
        groups = batches(context)
        for index, rows in enumerate(groups, 1):
            local = {row["document_ref"]: docs[row["document_ref"]] for row in rows}
            data = batch_context(context, rows, index, len(groups))
            aliases = {"documents": {r: a.asset_id for r, a in local.items()}, "requirements": units}
            record = {"batch_index": index, "document_refs": list(local), "candidate_count": len(local)}
            try:
                raw = await tools.model(BATCH_SYSTEM + SYSTEMS[stage], data,
                                        max_tokens=OUTPUT_LIMITS[stage],
                                        schema=SCHEMAS[stage], stage="reading_portfolio_batch")
                selected, failures = bind_choices(raw, local, units, stage, frozen_docs)
                nominations.extend(selected)
                reviewed_refs.update(local)
                rejected.extend(failures)
                gaps.extend(raw["gaps"])
                record.update(status="screened", nominations=len(selected))
                audit(tools, stage, data, aliases, raw, failures)
            except (LLMError, ValueError, ResourceExhausted) as exc:
                code = "PORTFOLIO_BATCH_RESOURCE" if isinstance(exc, ResourceExhausted) else "PORTFOLIO_BATCH_STRUCTURED_OUTPUT"
                details = model_error(exc)
                record.update(status="resource_exhausted" if isinstance(exc, ResourceExhausted) else "failed", **details)
                rejected.append({"batch_index": index, "code": code, **details})
                audit(tools, stage, data, aliases, code=details["error_code"])
                selection_batches.append(record)
                if isinstance(exc, (ResourceExhausted, LLMError)):
                    break
                continue
            selection_batches.append(record)
        if len(groups) <= 1:
            choices = nominations
        elif nominations:
            selected_ids = {c["asset_id"] for c in nominations}
            pool = {ref: asset for ref, asset in docs.items() if asset.asset_id in selected_ids}
            ref_by_id = {asset.asset_id: ref for ref, asset in pool.items()}
            data = {**context, "candidates": [row for row in context["candidates"] if row["document_ref"] in pool],
                    "batch_nominations": [{"document_ref": ref_by_id[c["asset_id"]], "reason": c["reason"]} for c in nominations],
                    "selection_batches": selection_batches, "unreviewed_count": len(docs) - len(reviewed_refs)}
            aliases = {"documents": {r: a.asset_id for r, a in pool.items()}, "requirements": units}
            try:
                raw = await tools.model(MERGE_SYSTEM + SYSTEMS[stage], data,
                                        max_tokens=OUTPUT_LIMITS[stage],
                                        schema=SCHEMAS[stage], stage="reading_portfolio_merge")
                choices, failures = bind_choices(raw, pool, units, stage, frozen_docs)
                rejected.extend(failures)
                gaps.extend(raw["gaps"])
                audit(tools, stage, data, aliases, raw, failures)
            except (LLMError, ValueError, ResourceExhausted) as exc:
                # Keep navigation recoverable, but do not silently take the first
                # batch as an allegedly complementary final portfolio.
                rejected.append({"code": "PORTFOLIO_MERGE_FAILED", **model_error(exc)})
                audit(tools, stage, data, aliases, code=model_error(exc)["error_code"])
        if choices and stage == "after_coverage_search":
            try:
                choices, failures = await review_choices(tools, question, context, review_documents(docs, context), choices)
                rejected.extend(failures)
            except (LLMError, ValueError, ResourceExhausted) as exc:
                choices = []
                rejected.append({"code": "PORTFOLIO_SCOPE_CHECK_FAILED", **model_error(exc)})
    all_choices = [*frozen, *choices]
    unreviewed = len(docs) - len(reviewed_refs) if stage in ("initial", "after_coverage_search") else 0
    if unreviewed:
        gaps.append(f"{unreviewed}项可读候选未完成本阶段分批审视；不能声明全部候选已比较")
    status = ("partial" if (rejected or unreviewed) and all_choices else "selected" if all_choices
              else "navigation_ready" if navigation else "insufficient" if docs else "no_candidates")
    if not all_choices and not navigation:
        gaps.insert(0, f"已保留{len(docs)}项可读候选，尚未选出阅读材料；没有受支持的研究结论" if docs else "没有可读准入候选；检索与来源记录已保留")
    if rejected:
        gaps.append("部分选材批或引用未完成核查，失败及未审视候选保留在审计记录")
    gaps = list(dict.fromkeys(gaps))[:8]
    payload = {"question_id": question.question_id, "method": "complete_candidate_batches_v20", "stage": stage,
               "protocol_hash": tools.repo.protocol()["hash"], "choices": all_choices, "gaps": gaps,
               "next_searches": navigation, "navigation_rejections": rejected, "selection_status": status,
               **context["material_state"], "candidate_reviewed_count": len(reviewed_refs),
               "candidate_unreviewed_count": unreviewed, "selection_batches": selection_batches,
               "batch_nominations": nominations,
               "candidate_unreviewed_refs": [ref for ref in docs if ref not in reviewed_refs] if unreviewed else []}
    return {"assets": [{"content_kind": "reading_portfolio", "inline_payload": payload,
                         "content_hash": digest(payload), "parse_status": "parsed"}],
            "payload": {"selected": len(all_choices), "gaps": gaps, "next_searches": len(navigation),
                        "stage": stage, "selection_status": status, "candidate_count": len(docs),
                        "candidate_reviewed_count": len(reviewed_refs), "candidate_unreviewed_count": unreviewed}}
