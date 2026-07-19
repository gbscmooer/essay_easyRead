"""Deterministic research-agent harness (no LLM required for MVP).

Runs fixed workflows over existing agent tools / workspace planners.
Goal is accepted as-is (search query / report title); model routing can be
plugged in later without changing the journal/artifact contract.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from .agent_tools import AgentToolError, call_agent_tool


ToolCaller = Callable[[str, dict[str, Any]], Awaitable[Any] | Any]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: Any) -> str:
    return str(value or "").strip()


@dataclass
class JournalEntry:
    step: str
    status: str
    started_at: str
    finished_at: str = ""
    tool: Optional[str] = None
    arguments: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    error: Optional[str] = None
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "tool": self.tool,
            "arguments": self.arguments,
            "summary": self.summary,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
        }


WORKFLOW_SPECS: dict[str, dict[str, Any]] = {
    "literature_brief": {
        "name": "literature_brief",
        "version": "0.1.0",
        "description": "Search workspace papers, load structured profiles, optionally retrieve evidence, export a reading brief.",
        "requires_workspace": True,
        "steps": [
            "parse_goal",
            "search_papers",
            "load_paper_contexts",
            "retrieve_evidence",
            "export_brief",
        ],
        "maps_to_eval_task": "T1",
    },
    "survey_path": {
        "name": "survey_path",
        "version": "0.1.0",
        "description": "Build reading plan + survey matrix for a workspace and export a cited draft package.",
        "requires_workspace": True,
        "steps": [
            "parse_goal",
            "reading_plan_build",
            "survey_build",
            "export_survey_package",
        ],
        "maps_to_eval_task": "T2+T3",
    },
}


def list_research_workflows() -> list[dict[str, Any]]:
    return [dict(spec) for spec in WORKFLOW_SPECS.values()]


def get_research_workflow_spec(name: str) -> dict[str, Any]:
    if name not in WORKFLOW_SPECS:
        raise AgentToolError(f"未知 workflow: {name}", "NOT_FOUND", 404)
    return dict(WORKFLOW_SPECS[name])


async def _maybe_await(value: Any) -> Any:
    import inspect

    if inspect.isawaitable(value):
        return await value
    return value


class ResearchHarnessRunner:
    """Thin state-machine runner with journal + markdown artifacts."""

    def __init__(self, call_tool: Optional[ToolCaller] = None):
        self.call_tool = call_tool or call_agent_tool
        self.journal: list[JournalEntry] = []
        self.state: dict[str, Any] = {}

    async def _step(
        self,
        step: str,
        *,
        tool: Optional[str] = None,
        arguments: Optional[dict[str, Any]] = None,
        soft_fail: bool = False,
        summary_fn: Optional[Callable[[Any], str]] = None,
        body: Optional[Callable[[], Awaitable[Any] | Any]] = None,
    ) -> Any:
        started = time.perf_counter()
        entry = JournalEntry(
            step=step,
            status="running",
            started_at=_utcnow(),
            tool=tool,
            arguments=arguments or {},
        )
        try:
            if body is not None:
                result = await _maybe_await(body())
            elif tool is not None:
                result = await _maybe_await(self.call_tool(tool, arguments or {}))
            else:
                result = None
            entry.status = "ok"
            entry.summary = summary_fn(result) if summary_fn else _default_summary(result)
            entry.finished_at = _utcnow()
            entry.elapsed_ms = int((time.perf_counter() - started) * 1000)
            self.journal.append(entry)
            return result
        except Exception as exc:  # noqa: BLE001 - journal must capture tool failures
            entry.status = "soft_fail" if soft_fail else "error"
            entry.error = str(exc)
            entry.summary = f"{'soft-fail' if soft_fail else 'error'}: {exc}"
            entry.finished_at = _utcnow()
            entry.elapsed_ms = int((time.perf_counter() - started) * 1000)
            self.journal.append(entry)
            if soft_fail:
                return None
            raise

    async def run(
        self,
        *,
        workflow: str,
        goal: str,
        workspace_id: int,
    ) -> dict[str, Any]:
        if workflow not in WORKFLOW_SPECS:
            raise AgentToolError(f"未知 workflow: {workflow}", "NOT_FOUND", 404)
        goal = _safe(goal)
        if not goal:
            raise AgentToolError("goal 不能为空", "BAD_REQUEST", 400)
        if not isinstance(workspace_id, int) or workspace_id <= 0:
            raise AgentToolError("workspace_id 无效", "BAD_REQUEST", 400)

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        started_at = _utcnow()
        self.journal = []
        self.state = {
            "run_id": run_id,
            "workflow": workflow,
            "goal": goal,
            "workspace_id": workspace_id,
        }

        try:
            if workflow == "literature_brief":
                await self._run_literature_brief()
            else:
                await self._run_survey_path()
            status = "completed"
            error = None
        except AgentToolError as exc:
            status = "failed"
            error = {"detail": exc.message, "code": exc.code}
        except Exception as exc:  # noqa: BLE001
            status = "failed"
            error = {"detail": str(exc), "code": "INTERNAL_ERROR"}

        artifacts = {
            "markdown": self.state.get("markdown") or "",
            "selected_paper_ids": self.state.get("selected_paper_ids") or [],
            "reading_plan": self.state.get("reading_plan"),
            "survey": self.state.get("survey"),
            "evidence": self.state.get("evidence"),
            "paper_contexts": self.state.get("paper_contexts") or [],
        }
        return {
            "run_id": run_id,
            "workflow": workflow,
            "goal": goal,
            "workspace_id": workspace_id,
            "status": status,
            "started_at": started_at,
            "finished_at": _utcnow(),
            "journal": [item.to_dict() for item in self.journal],
            "artifacts": artifacts,
            "error": error,
            "harness": {
                "engine": "deterministic_state_machine",
                "llm_required": False,
                "note": "MVP uses goal text as search query; model routing can replace parse_goal later.",
            },
        }

    async def _run_literature_brief(self) -> None:
        goal = self.state["goal"]
        workspace_id = self.state["workspace_id"]

        await self._step(
            "parse_goal",
            summary_fn=lambda _: "use goal as search_query",
            body=lambda: {"search_query": goal},
        )
        self.state["search_query"] = goal

        search = await self._step(
            "search_papers",
            tool="paper_search",
            arguments={
                "query": goal,
                "workspace_id": workspace_id,
                "limit": 8,
                "include_expanded": True,
            },
            summary_fn=lambda data: f"hits={data.get('total', 0)}",
        )
        selected = [
            int(item["paper_id"])
            for item in (search or {}).get("items", [])
            if item.get("paper_id") is not None
        ]
        if not selected:
            listed = await self._step(
                "fallback_list_workspace_papers",
                tool="list_papers_in_workspace",
                arguments={"workspace_id": workspace_id},
                summary_fn=lambda data: f"workspace_papers={len(data or [])}",
            )
            selected = [
                int(item["paper_id"])
                for item in (listed or [])
                if item.get("paper_id") is not None
            ][:8]
        if not selected:
            raise AgentToolError(
                "工作区内没有可用论文，请先导入或关联论文",
                "NO_PAPERS",
                404,
            )
        self.state["selected_paper_ids"] = selected[:8]
        self.state["search_results"] = search

        contexts: list[dict[str, Any]] = []

        async def _load_contexts() -> list[dict[str, Any]]:
            loaded: list[dict[str, Any]] = []
            for paper_id in self.state["selected_paper_ids"]:
                loaded.append(await _maybe_await(self.call_tool("paper_get", {"paper_id": paper_id})))
            return loaded

        contexts = await self._step(
            "load_paper_contexts",
            summary_fn=lambda data: f"loaded={len(data or [])}",
            body=_load_contexts,
        )
        self.state["paper_contexts"] = contexts or []

        evidence = await self._step(
            "retrieve_evidence",
            tool="paper_retrieve_evidence",
            arguments={
                "workspace_id": workspace_id,
                "query": goal,
                "top_k": 8,
                "paper_ids": self.state["selected_paper_ids"],
            },
            soft_fail=True,
            summary_fn=lambda data: (
                f"evidence={len((data or {}).get('evidence', []))}" if data else "skipped/unavailable"
            ),
        )
        self.state["evidence"] = evidence

        markdown = _render_literature_brief(
            goal=goal,
            workspace_id=workspace_id,
            contexts=self.state["paper_contexts"],
            evidence=evidence,
        )
        await self._step(
            "export_brief",
            summary_fn=lambda _: f"markdown_chars={len(markdown)}",
            body=lambda: {"markdown": markdown},
        )
        self.state["markdown"] = markdown

    async def _run_survey_path(self) -> None:
        goal = self.state["goal"]
        workspace_id = self.state["workspace_id"]

        await self._step(
            "parse_goal",
            summary_fn=lambda _: "goal recorded as report title",
            body=lambda: {"report_title": goal},
        )

        plan = await self._step(
            "reading_plan_build",
            tool="reading_plan_build",
            arguments={"workspace_id": workspace_id},
            summary_fn=lambda data: (
                f"stages={len((data or {}).get('stages', []))} "
                f"papers={sum(len(s.get('papers', [])) for s in (data or {}).get('stages', []))}"
            ),
        )
        self.state["reading_plan"] = plan
        if plan:
            self.state["selected_paper_ids"] = [
                int(p["paper_id"])
                for stage in plan.get("stages", [])
                for p in stage.get("papers", [])
                if p.get("paper_id") is not None
            ]

        survey = await self._step(
            "survey_build",
            tool="survey_build",
            arguments={"workspace_id": workspace_id},
            summary_fn=lambda data: f"matrix_rows={len((data or {}).get('matrix', []))}",
        )
        self.state["survey"] = survey

        markdown = _render_survey_package(goal=goal, workspace_id=workspace_id, plan=plan, survey=survey)
        await self._step(
            "export_survey_package",
            summary_fn=lambda _: f"markdown_chars={len(markdown)}",
            body=lambda: {"markdown": markdown},
        )
        self.state["markdown"] = markdown


def _default_summary(result: Any) -> str:
    if result is None:
        return "ok"
    if isinstance(result, dict):
        keys = ", ".join(list(result.keys())[:6])
        return f"dict[{keys}]"
    if isinstance(result, list):
        return f"list(len={len(result)})"
    return type(result).__name__


def _profile_bits(ctx: dict[str, Any]) -> dict[str, Any]:
    profile = ctx.get("profile") or {}
    return {
        "paper_id": ctx.get("paper_id") or profile.get("paper_id"),
        "title": profile.get("title") or ctx.get("title") or "Untitled",
        "year": profile.get("year"),
        "venue": profile.get("venue") or "",
        "one_sentence": profile.get("one_sentence") or "",
        "problem": profile.get("problem") or "",
        "key_contributions": profile.get("key_contributions") or [],
        "method_overview": (profile.get("method") or {}).get("overview")
        if isinstance(profile.get("method"), dict)
        else profile.get("method_overview") or "",
        "limitations": profile.get("limitations") or "",
        "experiments": profile.get("experiments") or {},
    }


def _render_literature_brief(
    *,
    goal: str,
    workspace_id: int,
    contexts: list[dict[str, Any]],
    evidence: Optional[dict[str, Any]],
) -> str:
    lines = [
        f"# Literature Brief",
        "",
        f"- Goal: {goal}",
        f"- Workspace: {workspace_id}",
        f"- Generated: {_utcnow()}",
        f"- Engine: research_harness/literature_brief@0.1.0",
        "",
        "## Selected Papers",
        "",
    ]
    for idx, ctx in enumerate(contexts, start=1):
        bits = _profile_bits(ctx)
        contrib = bits["key_contributions"]
        if isinstance(contrib, list):
            contrib_text = "; ".join(str(x) for x in contrib[:5]) or "（结构化字段缺失）"
        else:
            contrib_text = str(contrib) or "（结构化字段缺失）"
        experiments = bits["experiments"] if isinstance(bits["experiments"], dict) else {}
        datasets = experiments.get("datasets") or []
        if isinstance(datasets, list):
            exp_text = ", ".join(str(x) for x in datasets[:4]) or "（未见数据集字段）"
        else:
            exp_text = str(datasets)
        lines.extend(
            [
                f"### {idx}. {bits['title']} ({bits['year'] or 'n.d.'})",
                "",
                f"- Paper ID: `{bits['paper_id']}`",
                f"- Venue: {bits['venue'] or 'n/a'}",
                f"- One-sentence: {bits['one_sentence'] or '不确定/未找到'}",
                f"- Problem: {bits['problem'] or '不确定/未找到'}",
                f"- Contributions: {contrib_text}",
                f"- Method: {bits['method_overview'] or '不确定/未找到'}",
                f"- Experiments/Datasets: {exp_text}",
                f"- Limitations: {bits['limitations'] or '不确定/未找到'}",
                "",
            ]
        )

    lines.extend(["## Evidence Snippets", ""])
    ev_items = (evidence or {}).get("evidence") or []
    if not ev_items:
        lines.append("（向量索引不可用或无命中；以下结论仅基于结构化精读字段。）")
        lines.append("")
    else:
        for item in ev_items[:8]:
            lines.append(
                f"- [e{item.get('rank')}] paper:{item.get('paper_id')} "
                f"{item.get('paper_title') or ''} / {item.get('section_name') or ''} — "
                f"{_safe(item.get('snippet'))[:220]}"
            )
        lines.append("")

    lines.extend(
        [
            "## Follow-up Reading Suggestions",
            "",
            "1. 精读贡献字段最完整的 1–2 篇种子论文方法章节。",
            "2. 对照 limitations 字段列出可复现风险。",
            "3. 用图谱邻域扩展同方法/同数据集相关工作。",
            "4. 若证据片段不足，先构建工作区向量索引再重跑本 workflow。",
            "5. 将本 brief 输入 survey_path workflow 生成对比矩阵与综述草稿。",
            "",
            "## Unverified Assumptions",
            "",
            "- parse_goal 未使用 LLM，直接把 goal 当作检索词；语义改写可能提升召回。",
            "",
        ]
    )
    return "\n".join(lines)


def _render_survey_package(
    *,
    goal: str,
    workspace_id: int,
    plan: Optional[dict[str, Any]],
    survey: Optional[dict[str, Any]],
) -> str:
    lines = [
        "# Survey + Reading Path Package",
        "",
        f"- Goal: {goal}",
        f"- Workspace: {workspace_id}",
        f"- Generated: {_utcnow()}",
        f"- Engine: research_harness/survey_path@0.1.0",
        "",
        "## Reading Path",
        "",
    ]
    stages = (plan or {}).get("stages") or []
    if not stages:
        lines.append("（无阅读路径；工作区可能没有论文。）")
        lines.append("")
    for stage in stages:
        label = stage.get("label") or f"Stage {stage.get('stage')}"
        objective = stage.get("objective") or ""
        lines.append(f"### {label}")
        if objective:
            lines.append(f"Objective: {objective}")
        lines.append("")
        for paper in stage.get("papers") or []:
            reason = paper.get("reason") or "（无排序理由）"
            lines.append(
                f"- [{paper.get('paper_id')}] {paper.get('title')} "
                f"({paper.get('year') or 'n.d.'}) — {reason}"
            )
        lines.append("")

    lines.extend(["## Comparison Matrix", "", "| Paper | Task/Problem | Method | Data/Benchmark | Result | Limitation |", "|---|---|---|---|---|---|"])
    matrix = (survey or {}).get("matrix") or []
    if not matrix:
        lines.append("| — | — | — | — | — | — |")
    for row in matrix:
        datasets = row.get("datasets") or []
        if isinstance(datasets, list):
            data_text = ", ".join(str(x) for x in datasets[:4])
        else:
            data_text = str(datasets)
        contribs = row.get("contributions") or []
        if isinstance(contribs, list):
            result_text = "; ".join(str(x) for x in contribs[:3])
        else:
            result_text = str(contribs)
        lines.append(
            "| {title} | {task} | {method} | {data} | {result} | {limit} |".format(
                title=_cell(row.get("title") or row.get("paper_id")),
                task=_cell(row.get("task") or row.get("problem")),
                method=_cell(row.get("method")),
                data=_cell(data_text),
                result=_cell(result_text),
                limit=_cell(row.get("limitations") or row.get("limitation")),
            )
        )
    lines.append("")

    lines.extend(["## Draft Sections", ""])
    sections = (survey or {}).get("draft_sections") or []
    if not sections:
        lines.append("（无综述草稿段落；请确认工作区已有结构化分析。）")
        lines.append("")
    for section in sections:
        title = section.get("title") or "Section"
        body = section.get("content") or section.get("body") or ""
        refs = section.get("paper_ids") or section.get("refs") or []
        lines.append(f"### {title}")
        lines.append("")
        lines.append(body or "（空段落）")
        if refs:
            lines.append("")
            lines.append("Sources: " + ", ".join(f"paper:{r}" for r in refs))
        lines.append("")

    lines.extend(
        [
            "## Unverified Assumptions",
            "",
            "- 本包由结构化字段与规划器直接拼装，未做额外 LLM 润色。",
            "- 若矩阵单元格为空，表示源论文缺少对应分析字段，而非结论为否定。",
            "",
        ]
    )
    return "\n".join(lines)


def _cell(value: Any, limit: int = 80) -> str:
    text = _safe(value).replace("|", "/")
    text = " ".join(text.split())
    if not text:
        return "待核实"
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


async def run_research_workflow(
    *,
    workflow: str,
    goal: str,
    workspace_id: int,
    call_tool: Optional[ToolCaller] = None,
) -> dict[str, Any]:
    runner = ResearchHarnessRunner(call_tool=call_tool)
    return await runner.run(workflow=workflow, goal=goal, workspace_id=workspace_id)
