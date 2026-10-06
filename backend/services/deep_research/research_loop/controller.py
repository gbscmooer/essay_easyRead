"""Observe gaps, propose one action, validate it and persist its result."""
from __future__ import annotations

import asyncio

from ....models.research_core import ResearchQuestion, ResearchTask
from sqlmodel import Session
from ...research_event_log import budget_snapshot
from ...llm import LLMError
from .contracts import Action, digest
from .repository import LeaseLost
from .resources import ResourceExhausted
from ... import llm

NO_PROGRESS_LIMIT = 6


class Controller:
    def __init__(self, tools):
        self.tools, self.repo, self.resources = tools, tools.repo, tools.resources

    def observation(self):
        questions = self.repo.rows(ResearchQuestion)
        documents = self.tools.documents()
        tasks = self.repo.rows(ResearchTask)
        with Session(self.repo.engine) as session:
            resources = budget_snapshot(session, self.repo.run_id, self.resources.limits)
        allowed_evidence = {e["evidence_id"]: e for q in questions for e in self.tools.evidence(q.question_id)}
        return {"questions": [q.model_dump(exclude={"id", "run_id"}) for q in questions],
                "documents": [{"asset_id": a.asset_id, "title": (a.inline_payload or {}).get("paper", {}).get("title"),
                               "source_kind": (a.inline_payload or {}).get("source_kind")} for a in documents][:60],
                "recent_actions": [{"tool": t.kind, "question_id": t.subquestion_id,
                                    "input": t.payload, "state": t.state, "error": t.error_code} for t in tasks[-12:]],
                "recent_evidence": [{key: e[key] for key in ("evidence_id", "question_ids", "fact", "conditions", "missing_fields")}
                    for e in list(allowed_evidence.values())[-12:]], "resources": resources,
                "evidence_count": len(allowed_evidence), "active_seconds": self.repo.run().active_seconds,
                "remaining_seconds": max(0, self.resources.limits["wall_clock_minutes"] * 60 - self.repo.run().active_seconds)}

    def available(self, action: Action) -> bool:
        if action.question_id not in {q.question_id for q in self.repo.rows(ResearchQuestion)}:
            return False
        if action.tool == "plan_reading" and (self.repo.protocol() or {}).get("execution_revision") not in ("question_loop_v10", "question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20"):
            return False
        if action.gap_key:
            from .feedback import authorized
            if not authorized(self.tools, action):
                return False
        if action.tool == "search_papers" and (not action.query.strip() or not self.repo.run().options.get("search_online", True)):
            return False
        if action.tool == "expand_citations" and not self.repo.run().options.get("search_online", True):
            return False
        if action.tool == "fetch_document" and not self.repo.run().options.get("search_online", True):
            return False
        if action.tool == "read_documents":
            if not action.asset_ids or len(set(action.asset_ids)) != len(action.asset_ids):
                return False
            from ...research_exclusions import title_key
            try:
                titles = [title_key((self.repo.asset(aid).inline_payload or {}).get("paper", {}).get("title")) for aid in action.asset_ids]
            except ValueError:
                return False
            if len(set(titles)) != len(titles):
                return False
            if not all(self.available(Action(tool="read_document", question_id=action.question_id,
                                            asset_id=aid, query=action.query)) for aid in action.asset_ids) and not action.gap_key:
                return False
            if action.gap_key and any(not (self.repo.asset(aid).inline_payload or {}).get("text", "").strip()
                                      for aid in action.asset_ids):
                return False
        if action.tool in ("fetch_document", "read_document", "expand_citations"):
            documents = {a.asset_id: a for a in self.tools.ranked_documents(action.question_id)}
            if action.asset_id not in documents:
                return False
            if action.tool == "read_document" and not (documents[action.asset_id].inline_payload or {}).get("text", "").strip():
                return False
            if action.tool == "read_document":
                from ...research_exclusions import title_key
                selected = documents[action.asset_id].inline_payload or {}
                for task in self.repo.rows(ResearchTask):
                    if task.kind not in ("read_document", "read_documents") or task.state != "succeeded" or task.subquestion_id != action.question_id:
                        continue
                    for aid in task.payload.get("asset_ids") or [task.payload["asset_id"]]:
                        prior = self.repo.asset(aid, readable=True).inline_payload or {}
                        # Exact normalized titles group reading slots only; no cross-version identity is asserted.
                        if title_key(prior.get("paper", {}).get("title")) == title_key(selected.get("paper", {}).get("title")) and prior.get("source_kind") == selected.get("source_kind"):
                            return False
        if action.tool == "propose_stop":
            return all(q.status == "supported" for q in self.repo.rows(ResearchQuestion) if q.required)
        key = action.key(self.repo.protocol()["hash"])
        tasks = self.repo.rows(ResearchTask)
        existing = next((t for t in tasks if t.idempotency_key == key), None)
        if existing and existing.state not in ("pending", "ready"):
            return False
        if action.tool == "assess_answer":
            q = next(q for q in self.repo.rows(ResearchQuestion) if q.question_id == action.question_id)
            if not self.tools.evidence(q.question_id):
                return False
        return True

    def assessment_action(self, question_id: str) -> Action:
        evidence = self.tools.evidence(question_id)
        # New evidence permits a new assessment; unchanged input is idempotent.
        return Action(tool="assess_answer", question_id=question_id, query=digest(sorted(e["evidence_id"] for e in evidence)),
                      reason="用当前证据评估问题答案与剩余缺口")

    def fallback(self):
        pending = sorted([q for q in self.repo.rows(ResearchQuestion) if q.status != "supported"], key=lambda q: (not q.required, q.priority))
        for q in pending:
            def planned_search():
                for query in (self.tools.plan() or {}).get("queries", []):
                    if query["question_id"] == q.question_id and not self.resources.source_exhausted and self.resources.remaining("llm_calls") > 3:
                        action = Action(tool="search_papers", question_id=q.question_id, query=query["query"], reason="按冻结英文检索计划补充材料")
                        if self.available(action):
                            return action
                return None
            documents = self.tools.ranked_documents(q.question_id)
            unread = []
            for asset in documents:
                action = Action(tool="read_document", question_id=q.question_id, asset_id=asset.asset_id,
                                query=q.text[:500], reason="优先阅读相关且已有文本的材料")
                if self.available(action):
                    unread.append(action)
            tasks = [t for t in self.repo.rows(ResearchTask) if t.subquestion_id == q.question_id]
            last_assessment = max((t.id for t in tasks if t.kind == "assess_answer"), default=0)
            reads = [t for t in tasks if t.kind == "read_document" and t.state == "succeeded" and t.id > last_assessment]
            new_fulltext = any((self.repo.asset(t.payload["asset_id"], readable=True).inline_payload or {}).get("source_kind") != "abstract" for t in reads)
            if reads and not self.tools.evidence(q.question_id) and not any(t.kind == "fetch_document" for t in tasks) and not self.resources.source_exhausted:
                for task in reads:
                    material = self.repo.asset(task.payload["asset_id"], readable=True)
                    if (material.inline_payload or {}).get("source_kind") == "abstract" and self.resources.remaining("llm_calls") > 3 and self.resources.remaining("llm_tokens") > 14000:
                        action = Action(tool="fetch_document", question_id=q.question_id, asset_id=material.asset_id,
                                        reason="摘要未提供能回答问题的具体事实，获取正文后补读")
                        if self.available(action):
                            return action
            # Assess after a small batch, whenever the call reserve is near, or before trying more network work.
            if self.tools.evidence(q.question_id) and (len(reads) >= 2 or (q.answer_revision_id and new_fulltext) or not unread or self.resources.remaining("llm_calls") <= 4):
                action = self.assessment_action(q.question_id)
                if self.available(action):
                    return action
            last_acquisition = max((t.id for t in tasks if t.kind in ("search_papers", "fetch_document", "expand_citations")), default=0)
            if q.answer_revision_id and q.gaps and not reads and last_acquisition <= last_assessment:
                # When abstracts leave gaps, deepen one promising source before expanding the pool again.
                evidence = self.tools.evidence(q.question_id)
                fetches = [t for t in tasks if t.kind == "fetch_document"]
                if evidence and all(e["source_kind"] == "abstract" for e in evidence) and not fetches and not self.resources.source_exhausted and self.resources.remaining("llm_calls") > 3 and self.resources.remaining("llm_tokens") > 14000:
                    candidates = sorted(documents, key=lambda a: not bool((a.inline_payload or {}).get("paper", {}).get("arxiv_id") or (a.inline_payload or {}).get("paper", {}).get("pdf_url")))
                    for asset in candidates:
                        if not any((asset.inline_payload or {}).get("paper", {}).get(k) for k in ("arxiv_id", "pdf_url", "doi")):
                            continue
                        action = Action(tool="fetch_document", question_id=q.question_id, asset_id=asset.asset_id,
                                        reason="摘要仍不能补足答案，优先补读一份相关正文")
                        if self.available(action):
                            return action
                action = planned_search()
                if action:
                    return action
            if unread and self.resources.remaining("llm_calls") > 3 and self.resources.remaining("llm_tokens") > 14000:
                return unread[0]
            action = planned_search()
            if action:
                return action
            fetches = [t for t in self.repo.rows(ResearchTask) if t.kind == "fetch_document" and t.subquestion_id == q.question_id]
            for asset in documents:
                if (asset.inline_payload or {}).get("source_kind") == "abstract" and len(fetches) < 2 and not self.resources.source_exhausted and self.resources.remaining("llm_calls") > 3 and self.resources.remaining("llm_tokens") > 14000:
                    action = Action(tool="fetch_document", question_id=q.question_id, asset_id=asset.asset_id,
                                    reason="补足当前问题所需全文")
                    if self.available(action):
                        return action
            for asset in documents[:2] if not self.resources.source_exhausted and self.resources.remaining("llm_calls") > 3 else []:
                action = Action(tool="expand_citations", question_id=q.question_id, asset_id=asset.asset_id,
                                reason="沿已有线索寻找补充与相反证据")
                if self.available(action):
                    return action
        return None

    async def choose(self):
        # Mechanical scheduling is deterministic. Models work on research decisions and materials.
        if self.repo.run().options.get("search_online", True) and not self.tools.plan():
            specs = [q.model_dump(exclude={"id", "run_id"}) for q in self.repo.rows(ResearchQuestion)]
            self.tools.save_plan(await self.tools.make_plan(specs))
        if (self.repo.protocol() or {}).get("execution_revision") in ("question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20"):
            if (self.repo.protocol() or {}).get("execution_revision") == "question_loop_v20":
                from .scheduling_v20 import material_first
            elif (self.repo.protocol() or {}).get("execution_revision") == "question_loop_v19":
                from .scheduling_v19 import material_first
            elif (self.repo.protocol() or {}).get("execution_revision") == "question_loop_v18":
                from .scheduling_v18 import material_first
            elif (self.repo.protocol() or {}).get("execution_revision") == "question_loop_v17":
                from .scheduling_v17 import material_first
            elif (self.repo.protocol() or {}).get("execution_revision") in ("question_loop_v15", "question_loop_v16"):
                from .scheduling_v15 import material_first
            elif (self.repo.protocol() or {}).get("execution_revision") == "question_loop_v14":
                from .scheduling_v14 import material_first
            else:
                from .scheduling_v11 import material_first
            return await material_first(self)
        if (self.repo.protocol() or {}).get("execution_revision") == "question_loop_v10":
            from .scheduling_v10 import material_first
            return await material_first(self)
        if (self.tools.plan() or {}).get("strategy") in ("material_first_v1", "material_first_v2", "material_first_v3"):
            from .scheduling import material_first
            return material_first(self)
        return self.fallback()

    async def run(self):
        no_progress = (self.repo.run().checkpoint or {}).get("no_progress", 0)
        for _ in range(self.resources.limits["actions"]):
            if self.repo.run().status != "running":
                raise LeaseLost("研究已暂停或取消")
            questions = self.repo.rows(ResearchQuestion)
            if all(q.status == "supported" for q in questions if q.required):
                return "required_questions_supported"
            queued = next((t for t in self.repo.rows(ResearchTask) if t.state in ("pending", "ready")), None)
            if no_progress >= NO_PROGRESS_LIMIT and queued is None:
                return "no_progress"
            # Explicit retries execute the frozen failed task payload, not a new arbitrary action.
            action = Action.model_validate(queued.payload) if queued else await self.choose()
            if queued:
                if not self.available(action):
                    self.repo.skip_task(queued.task_id, "ACTION_NO_LONGER_AVAILABLE")
                    continue
                revision = self.repo.protocol().get("execution_revision", "")
                if revision in ("question_loop_v10", "question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20"):
                    import importlib
                    version = int(revision.rsplit("v", 1)[1])
                    version = 11 if version in (12, 13) else 15 if version == 16 else version
                    affordable = importlib.import_module(f"{__package__}.scheduling_v{version}").affordable
                    if not affordable(self.tools, action):
                        return "resource_limit"
            if action is None:
                if (self.repo.protocol() or {}).get("execution_revision") == "question_loop_v20":
                    failed_checks = [t for t in self.repo.rows(ResearchTask)
                                     if t.kind == "assess_answer" and t.state == "failed"]
                    if failed_checks and any(q.required and q.status != "supported" for q in questions):
                        with self.repo.transaction() as session:
                            run = session.get(type(self.repo.run()), self.repo.run_id)
                            run.checkpoint = {**(run.checkpoint or {}), "stop_details": [{
                                "tool": t.kind, "question_id": t.subquestion_id, "code": t.error_code
                            } for t in failed_checks[-4:]]}
                        return "model_error"
                if (self.repo.protocol() or {}).get("execution_revision") in ("question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20"):
                    failed = [t for t in self.repo.rows(ResearchTask) if t.kind == "plan_reading" and t.state == "failed"]
                    if failed:
                        with self.repo.transaction() as session:
                            run = session.get(type(self.repo.run()), self.repo.run_id)
                            run.checkpoint = {**(run.checkpoint or {}), "stop_details": [{"code":"portfolio_tool_failed", "tool":"plan_reading", "question_id":t.subquestion_id} for t in failed[:8]]}
                        return "material_selection_failed"
                if (self.repo.protocol() or {}).get("execution_revision") in ("question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20"):
                    blocked = getattr(self.resources, "navigation_blockers", [])
                    if blocked:
                        with self.repo.transaction() as session:
                            run = session.get(type(self.repo.run()), self.repo.run_id)
                            run.checkpoint = {**(run.checkpoint or {}), "stop_details": blocked[:12]}
                        return "resource_limit"
                if (self.repo.protocol() or {}).get("execution_revision") in ("question_loop_v18", "question_loop_v19", "question_loop_v20"):
                    from .portfolio_v18 import latest

                    pending = [q for q in questions if q.required and not q.answer_revision_id]
                    empty = [q for q in pending if (latest(self.tools, q.question_id) or {}).get("selection_status") in ("insufficient", "no_candidates")]
                    if empty:
                        return "material_selection_empty"
                return "resource_limit" if self.resources.source_exhausted or self.resources.remaining("llm_calls") <= 3 or self.resources.remaining("llm_tokens") <= 14000 else "no_actionable_gap"
            if action.tool == "propose_stop":
                return "required_questions_supported"
            claim = self.repo.claim_task(action)
            if claim is None:
                no_progress += 1
                continue
            task_id, attempt_id = claim
            self.resources.task_id, self.resources.attempt_id = task_id, attempt_id
            self.resources.current_question_id = action.question_id
            self.resources.account = "answer" if action.tool == "assess_answer" else "reading" if action.tool in ("fetch_document", "read_document", "read_documents") else "research"
            llm.set_llm_stage(action.tool)
            try:
                self.resources.charge("actions")
                from .timeout_v20 import tool_timeout
                timeout = tool_timeout(self.tools, action)
                result = await asyncio.wait_for(self.tools.execute(action), timeout=timeout)
                self.repo.complete_task(task_id, attempt_id, result)
            except (ResourceExhausted, LeaseLost):
                self.repo.abandon_task(task_id)
                raise
            except asyncio.CancelledError:
                self.repo.abandon_task(task_id)
                raise
            except Exception as exc:
                if isinstance(exc, LLMError) and exc.code == "BUDGET_EXHAUSTED":
                    self.repo.abandon_task(task_id)
                    raise ResourceExhausted("研究模型额度已用尽") from exc
                self.tools.diagnostic(action.tool, exc)
                details = {"error_code": type(exc).__name__}
                if (self.repo.protocol() or {}).get("execution_revision") == "question_loop_v20":
                    from .diagnostics_v20 import model_error
                    details = model_error(exc)
                self.repo.complete_task(task_id, attempt_id, {**details,
                    "error_detail": "工具调用未完成，详情见故障分类；未将失败视为无命中"})
            no_progress = (self.repo.run().checkpoint or {}).get("no_progress", 0)
            # Exhausted HTTP only disables further acquisition; reading/synthesis can continue locally.
        return "action_limit"
