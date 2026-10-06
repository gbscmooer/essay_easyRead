"""Read a frozen complementary portfolio before the first overview answer."""

from ....models.research_core import ResearchQuestion, ResearchTask
from .contracts import Action
from .portfolio_v10 import latest, resolve
from .answer_budget_v10 import can_read, envelope


def affordable(tools, action):
    if action.tool == "read_documents":
        return can_read(
            tools, action.question_id, len(action.asset_ids), action.asset_ids
        )
    budget = envelope(
        tools,
        action.question_id,
        future_sources=1 if action.tool == "fetch_document" else 0,
    )
    return (
        tools.resources.remaining("llm_calls") >= budget["llm_calls"] + 3
        and tools.resources.remaining("llm_tokens") >= budget["llm_tokens"] + 14000
        and tools.resources.remaining("actions") >= 2
    )


async def material_first(controller):
    tools, repo = controller.tools, controller.repo
    questions = sorted(
        (q for q in repo.rows(ResearchQuestion) if q.status != "supported"),
        key=lambda q: (not q.required, q.priority),
    )
    for question in questions:
        role = next(
            (
                r["role"]
                for r in (tools.plan() or {}).get("reading_roles", [])
                if r["question_id"] == question.question_id
            ),
            "mechanism",
        )
        if role != "overview":
            from .scheduling import material_first as previous

            return previous(controller)
        tasks = [
            t
            for t in repo.rows(ResearchTask)
            if t.subquestion_id == question.question_id
        ]
        if not question.answer_revision_id:
            for query in (tools.plan() or {}).get("queries", []):
                if (
                    query["question_id"] != question.question_id
                    or tools.resources.source_exhausted
                ):
                    continue
                action = Action(
                    tool="search_papers",
                    question_id=question.question_id,
                    query=query["query"],
                    reason="按原问题召回候选，再选择互补阅读组合",
                )
                if (
                    controller.available(action)
                    and tools.resources.remaining("llm_calls") > 5
                ):
                    return action
        portfolio = latest(tools, question.question_id)
        if portfolio is None:
            action = Action(
                tool="plan_reading",
                question_id=question.question_id,
                reason="根据已召回材料选择互补分类与代表原论文",
            )
            if controller.available(action):
                return action
            continue
        fetches = [t for t in tasks if t.kind == "fetch_document"]
        selected = [(choice, resolve(tools, choice)) for choice in portfolio["choices"]]
        # Bound downloads to actual selected assets. A failed body fetch leaves the
        # abstract readable and its limitations intact, instead of arbitrary fallback.
        for choice, asset in selected:
            action = Action(
                tool="fetch_document",
                question_id=question.question_id,
                asset_id=asset.asset_id,
                reason="读取组合中的原文：" + choice["reason"],
            )
            if (
                asset.inline_payload.get("source_kind") == "abstract"
                and len(fetches) < 4
                and not tools.resources.source_exhausted
                and controller.available(action)
                and affordable(tools, action)
            ):
                return action
        unread = []
        for choice, asset in selected:
            if controller.available(
                Action(
                    tool="read_document",
                    question_id=question.question_id,
                    asset_id=asset.asset_id,
                )
            ):
                unread.append((choice, asset))
        # Read before answering; reduce a batch only if its full final-answer envelope
        # cannot fit. This does not raise caps or mark unread material as evidence.
        for count in range(min(3, len(unread)), 0, -1):
            if can_read(
                tools,
                question.question_id,
                count,
                [a.asset_id for _, a in unread[:count]],
            ):
                chosen = unread[:count]
                queries = list(
                    dict.fromkeys(
                        q for choice, _ in chosen for q in choice["focus_queries"]
                    )
                )[:4]
                return Action(
                    tool="read_documents",
                    question_id=question.question_id,
                    asset_ids=[a.asset_id for _, a in chosen],
                    query=question.text[:500],
                    focus_queries=queries,
                    reason="先阅读互补分类与原论文，再集中综合；选材理由仅作导航",
                )
        assessment = controller.assessment_action(question.question_id)
        evidence = tools.evidence(question.question_id)
        if evidence and controller.available(assessment):
            return assessment
        if question.answer_revision_id and question.gaps:
            from .feedback import latest_plan

            for spec in latest_plan(tools, question.question_id).get("steps", []):
                action = Action.model_validate(spec)
                if controller.available(action) and affordable(tools, action):
                    return action
            # A feedback fetch/search may produce a new body after the answer. Read
            # that real asset before another assessment; never use the reason as data.
            last_check = max(
                (
                    t.id
                    for t in tasks
                    if t.kind == "assess_answer" and t.state == "succeeded"
                ),
                default=0,
            )
            acquired = {
                aid
                for t in tasks
                if t.id > last_check
                and t.kind in ("fetch_document", "search_papers")
                and t.state == "succeeded"
                for aid in t.result_asset_ids
            }
            for asset in tools.ranked_documents(question.question_id):
                if asset.asset_id not in acquired:
                    continue
                if asset.inline_payload.get("source_kind") != "abstract":
                    read = Action(
                        tool="read_documents",
                        question_id=question.question_id,
                        asset_ids=[asset.asset_id],
                        query=question.text[:500],
                        reason="读取缺口补充动作实际取得的新原文",
                    )
                    if controller.available(read) and affordable(tools, read):
                        return read
                elif (
                    asset.inline_payload.get("paper", {}).get("research_role")
                    != "survey"
                    and len(fetches) < 4
                ):
                    fetch = Action(
                        tool="fetch_document",
                        question_id=question.question_id,
                        asset_id=asset.asset_id,
                        reason="获取补检索返回的相关原论文",
                    )
                    if (
                        controller.available(fetch)
                        and affordable(tools, fetch)
                        and not tools.resources.source_exhausted
                    ):
                        return fetch
    return None
