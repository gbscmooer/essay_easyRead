"""Material-first scheduling: acquire context before spending calls on synthesis."""
from ....models.research_core import ResearchQuestion, ResearchTask
from ...research_exclusions import title_key
from .contracts import Action


def material_first(controller):
    tools, resources, repo = controller.tools, controller.resources, controller.repo
    pending = sorted((q for q in repo.rows(ResearchQuestion) if q.status != "supported"),
                     key=lambda q: (not q.required, q.priority))
    for question in pending:
        tasks = [t for t in repo.rows(ResearchTask) if t.subquestion_id == question.question_id]
        calls, tokens = resources.remaining("llm_calls"), resources.remaining("llm_tokens")
        evidence = tools.evidence(question.question_id)
        assessment = controller.assessment_action(question.question_id)
        v9 = (repo.protocol() or {}).get("execution_revision") in ("question_loop_v9", "question_loop_v10", "question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20")
        overview = v9 and any(r["question_id"] == question.question_id and r["role"] == "overview" for r in (tools.plan() or {}).get("reading_roles", []))
        targets = [t["name"] for t in (tools.plan() or {}).get("targets", []) if t["question_id"] == question.question_id]
        primary = {name for a in tools.documents() for name in (a.inline_payload or {}).get("paper", {}).get("primary_targets", [])}
        # Search complementary names first; a broad pool is cheap compared with repeated
        # synthesis from two uninformative abstracts. Frozen user questions remain unchanged.
        if not question.answer_revision_id and not resources.source_exhausted and calls > 5:
            queries = [{"question_id": question.question_id, "query": name} for name in targets if name not in primary]
            if not targets or not set(targets) <= primary:
                queries += (tools.plan() or {}).get("queries", [])
            for query in queries:
                if query["question_id"] != question.question_id:
                    continue
                action = Action(tool="search_papers", question_id=question.question_id, query=query["query"],
                                reason="召回互补主题材料，随后集中获取与阅读")
                if controller.available(action):
                    return action
        documents = tools.ranked_documents(question.question_id)
        if targets and set(targets) <= primary and not question.answer_revision_id:
            documents = [a for a in documents if set((a.inline_payload or {}).get("paper", {}).get("primary_targets", [])) & set(targets)]
        read_ids = [aid for t in tasks if t.kind in ("read_document", "read_documents") and t.state == "succeeded"
                    for aid in t.payload.get("asset_ids") or [t.payload.get("asset_id")]]
        read_titles = {title_key((repo.asset(aid, readable=True).inline_payload or {}).get("paper", {}).get("title"))
                       for aid in read_ids if aid}
        fulltexts = [a for a in documents if (a.inline_payload or {}).get("source_kind") != "abstract"
                     and (a.inline_payload or {}).get("text", "").strip()]
        fetches = [t for t in tasks if t.kind == "fetch_document"]
        can_read = calls >= 5 and tokens > 22000
        if (repo.protocol() or {}).get("execution_revision") in ("question_loop_v10", "question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20"):
            if (repo.protocol() or {}).get("execution_revision") in ("question_loop_v19", "question_loop_v20"):
                from .answer_budget_v19 import can_read as affordable_read
            elif (repo.protocol() or {}).get("execution_revision") in ("question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20"):
                from .answer_budget_v15 import can_read as affordable_read
            elif (repo.protocol() or {}).get("execution_revision") == "question_loop_v14":
                from .answer_budget_v14 import can_read as affordable_read
            elif (repo.protocol() or {}).get("execution_revision") in ("question_loop_v11", "question_loop_v12", "question_loop_v13"):
                from .answer_budget_v11 import can_read as affordable_read
            else:
                from .answer_budget_v10 import can_read as affordable_read
            can_read = can_read and affordable_read(tools, question.question_id, 1)
        # Two available full texts are enough to start; permit one failed alternate.
        # Prefer official arXiv links over publishers with no usable public body.
        body_target = min(3, len(targets)) if targets else (1 if overview and not question.answer_revision_id else 2)
        if can_read and not resources.source_exhausted and len(fetches) < 3 and len(fulltexts) < body_target:
            candidates = sorted(documents, key=lambda a: (
                not bool((a.inline_payload or {}).get("paper", {}).get("primary_targets")),
                ((a.inline_payload or {}).get("paper", {}).get("research_role") == "survey") if v9 and question.answer_revision_id else ((a.inline_payload or {}).get("paper", {}).get("research_role") != "survey"),
                not bool((a.inline_payload or {}).get("paper", {}).get("arxiv_id")),
                not bool((a.inline_payload or {}).get("paper", {}).get("pdf_url")),
                (a.inline_payload or {}).get("paper", {}).get("research_scope") != "overview",
                -((a.inline_payload or {}).get("paper", {}).get("year") or 0)))
            for asset in candidates:
                paper = (asset.inline_payload or {}).get("paper", {})
                if (asset.inline_payload or {}).get("source_kind") != "abstract" or not any(paper.get(k) for k in ("arxiv_id", "pdf_url", "doi")):
                    continue
                action = Action(tool="fetch_document", question_id=question.question_id, asset_id=asset.asset_id,
                                reason="先获得可解释方法与方向的正文，避免仅按综述摘要综合")
                if controller.available(action):
                    return action
        unread = []
        for asset in documents:
            action = Action(tool="read_document", question_id=question.question_id, asset_id=asset.asset_id,
                            query=question.text[:500])
            if controller.available(action):
                unread.append(asset)
        if overview and fulltexts:
            # Use the actual body before spending one checker per uninformative abstract.
            unread = [a for a in unread if (a.inline_payload or {}).get("source_kind") != "abstract"]
        last_assessment = max((t.id for t in tasks if t.kind == "assess_answer"), default=0)
        fresh = any(t.kind in ("read_document", "read_documents") and t.state == "succeeded" and t.id > last_assessment for t in tasks)
        policy = (tools.plan() or {}).get("feedback_policy")
        feedback = policy in ("gap_followup_v1", "gap_followup_v2")
        if feedback and question.answer_revision_id and question.gaps:
            # New facts are assessed before spending more on the old gap. A failed
            # or duplicate read cannot create another assessment of unchanged input.
            if policy == "gap_followup_v1" and evidence and controller.available(assessment):
                return assessment
            from .feedback import latest_plan, can_execute
            for spec in latest_plan(tools, question.question_id).get("steps", []):
                action = Action.model_validate(spec)
                if (repo.protocol() or {}).get("execution_revision") in ("question_loop_v10", "question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20"):
                    if (repo.protocol() or {}).get("execution_revision") in ("question_loop_v19", "question_loop_v20"):
                        from .scheduling_v19 import affordable
                    elif (repo.protocol() or {}).get("execution_revision") in ("question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20"):
                        from .scheduling_v15 import affordable
                    elif (repo.protocol() or {}).get("execution_revision") == "question_loop_v14":
                        from .scheduling_v14 import affordable
                    elif (repo.protocol() or {}).get("execution_revision") in ("question_loop_v11", "question_loop_v12", "question_loop_v13"):
                        from .scheduling_v11 import affordable
                    else:
                        from .scheduling_v10 import affordable
                    permitted = affordable(tools, action)
                else:
                    permitted = can_execute(resources, action)
                if permitted:
                    if action.tool == "search_papers" and resources.source_exhausted:
                        continue
                    if controller.available(action):
                        return action
            if evidence and controller.available(assessment):
                return assessment
        # A lite portfolio normally reads 4–6 distinct sources before the first answer;
        # bigger depths can keep acquiring through their own frozen resource envelope.
        target = (1 if overview and fulltexts else min(6, len(targets)) if targets and set(targets) <= primary else (6 if feedback or repo.run().depth == "lite" else 9))
        if evidence and (not can_read or not unread or len(read_titles) >= target or question.answer_revision_id):
            if controller.available(assessment):
                return assessment
        if can_read and unread:
            chosen, seen = [], set()
            for asset in unread:
                title = title_key((asset.inline_payload or {}).get("paper", {}).get("title"))
                if title in seen:
                    continue
                seen.add(title)
                chosen.append(asset.asset_id)
                if len(chosen) == 3:
                    break
            action = Action(tool="read_documents", question_id=question.question_id, asset_ids=chosen,
                            query=question.text[:500], reason="批量阅读互补资料，抽取具体方法、演进与方向并独立检查")
            if controller.available(action):
                return action
        if evidence and fresh and controller.available(assessment):
            return assessment
    return None
