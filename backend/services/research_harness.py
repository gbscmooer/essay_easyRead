"""Research-agent harness backed by LangGraph StateGraph.

Runs fixed research workflows over existing agent tools / workspace planners.
Public contract (runs API / CLI / journal + markdown artifacts) stays stable;
node orchestration is delegated to LangGraph instead of a hand-rolled loop.
"""

from __future__ import annotations

import inspect
import operator
import time
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Awaitable, Callable, Optional, TypedDict

from .agent_tools import AgentToolError, call_agent_tool


ToolCaller = Callable[[str, dict[str, Any]], Awaitable[Any] | Any]
GoalPlanner = Callable[[str, str], Awaitable[dict[str, Any]] | dict[str, Any]]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: Any) -> str:
    return str(value or "").strip()


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _require_langgraph():
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise AgentToolError(
            "未安装 langgraph。请执行: pip install langgraph",
            "DEPENDENCY_MISSING",
            500,
        ) from exc
    return END, StateGraph


class ResearchHarnessState(TypedDict, total=False):
    """Shared LangGraph state for research workflows."""

    run_id: str
    workflow: str
    goal: str
    workspace_id: int
    search_query: str
    goal_plan: Optional[dict[str, Any]]
    search_results: dict[str, Any]
    selected_paper_ids: list[int]
    paper_contexts: list[dict[str, Any]]
    evidence: Optional[dict[str, Any]]
    reading_plan: Optional[dict[str, Any]]
    survey: Optional[dict[str, Any]]
    markdown: str
    journal: Annotated[list[dict[str, Any]], operator.add]
    fatal_error: Optional[dict[str, Any]]


WORKFLOW_SPECS: dict[str, dict[str, Any]] = {
    "literature_brief": {
        "name": "literature_brief",
        "version": "0.3.0",
        "engine": "langgraph",
        "description": (
            "Search workspace papers, load structured profiles, "
            "optionally retrieve evidence, export a reading brief."
        ),
        "requires_workspace": True,
        "goal_planner": "optional_llm_with_deterministic_fallback",
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
        "version": "0.3.0",
        "engine": "langgraph",
        "description": (
            "Build reading plan + survey matrix for a workspace "
            "and export a cited draft package."
        ),
        "requires_workspace": True,
        "goal_planner": "optional_llm_with_deterministic_fallback",
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


def _journal_entry(
    step: str,
    *,
    status: str,
    started_at: str,
    finished_at: str,
    elapsed_ms: int,
    tool: Optional[str] = None,
    arguments: Optional[dict[str, Any]] = None,
    summary: str = "",
    error: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "step": step,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "tool": tool,
        "arguments": arguments or {},
        "summary": summary,
        "error": error,
        "elapsed_ms": elapsed_ms,
    }


def _default_summary(result: Any) -> str:
    if result is None:
        return "ok"
    if isinstance(result, dict):
        keys = ", ".join(list(result.keys())[:6])
        return f"dict[{keys}]"
    if isinstance(result, list):
        return f"list(len={len(result)})"
    return type(result).__name__


async def _traced(
    step: str,
    *,
    tool: Optional[str] = None,
    arguments: Optional[dict[str, Any]] = None,
    soft_fail: bool = False,
    summary_fn: Optional[Callable[[Any], str]] = None,
    body: Callable[[], Awaitable[Any] | Any],
) -> tuple[Any, list[dict[str, Any]], Optional[dict[str, Any]]]:
    """Run a node body and return (result, journal_delta, fatal_error)."""

    started = time.perf_counter()
    started_at = _utcnow()
    try:
        result = await _maybe_await(body())
        entry = _journal_entry(
            step,
            status="ok",
            started_at=started_at,
            finished_at=_utcnow(),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            tool=tool,
            arguments=arguments,
            summary=summary_fn(result) if summary_fn else _default_summary(result),
        )
        return result, [entry], None
    except Exception as exc:  # noqa: BLE001 - journal must capture failures
        entry = _journal_entry(
            step,
            status="soft_fail" if soft_fail else "error",
            started_at=started_at,
            finished_at=_utcnow(),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            tool=tool,
            arguments=arguments,
            summary=f"{'soft-fail' if soft_fail else 'error'}: {exc}",
            error=str(exc),
        )
        if soft_fail:
            return None, [entry], None
        if isinstance(exc, AgentToolError):
            fatal = {"detail": exc.message, "code": exc.code}
        else:
            fatal = {"detail": str(exc), "code": "INTERNAL_ERROR"}
        return None, [entry], fatal


async def _run_goal_planner(
    *,
    goal: str,
    workflow: str,
    plan_goal: Optional[GoalPlanner],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the optional LLM planner and safely fall back to the original goal."""

    from .research_goal_planner import fallback_goal_plan, plan_research_goal

    planner = plan_goal or plan_research_goal
    plan, journal, _fatal = await _traced(
        "parse_goal",
        tool="research_goal_planner",
        arguments={"workflow": workflow, "goal_chars": len(goal)},
        soft_fail=True,
        summary_fn=lambda data: (
            f"planner={(data or {}).get('planner', 'unknown')} "
            f"query_chars={len(str((data or {}).get('search_query') or ''))}"
        ),
        body=lambda: planner(goal, workflow),
    )
    if not isinstance(plan, dict) or not _safe(plan.get("search_query")):
        plan = fallback_goal_plan(goal, workflow)
    return plan, journal


def build_literature_brief_graph(
    call_tool: ToolCaller,
    plan_goal: Optional[GoalPlanner] = None,
):
    """Compile LangGraph for literature_brief."""

    END, StateGraph = _require_langgraph()

    async def parse_goal(state: ResearchHarnessState) -> ResearchHarnessState:
        goal = _safe(state.get("goal"))
        plan, journal = await _run_goal_planner(
            goal=goal,
            workflow="literature_brief",
            plan_goal=plan_goal,
        )
        return {
            "search_query": _safe(plan.get("search_query")) or goal,
            "goal_plan": plan,
            "journal": journal,
        }

    async def search_papers(state: ResearchHarnessState) -> ResearchHarnessState:
        if state.get("fatal_error"):
            return {}
        goal = _safe(state.get("search_query")) or state["goal"]
        workspace_id = state["workspace_id"]
        try:
            # Default 8 matches the pre-planner literature_brief hard limit.
            target_count = int((state.get("goal_plan") or {}).get("target_paper_count") or 8)
        except (TypeError, ValueError):
            target_count = 8
        target_count = min(max(target_count, 1), 8)
        args = {
            "query": goal,
            "workspace_id": workspace_id,
            "limit": target_count,
            "include_expanded": True,
        }

        async def _body():
            search = await _maybe_await(call_tool("paper_search", args))
            selected = [
                int(item["paper_id"])
                for item in (search or {}).get("items", [])
                if item.get("paper_id") is not None
            ]
            journal_extra: list[dict[str, Any]] = []
            if not selected:
                listed, list_journal, list_fatal = await _traced(
                    "fallback_list_workspace_papers",
                    tool="list_papers_in_workspace",
                    arguments={"workspace_id": workspace_id},
                    summary_fn=lambda data: f"workspace_papers={len(data or [])}",
                    body=lambda: call_tool(
                        "list_papers_in_workspace",
                        {"workspace_id": workspace_id},
                    ),
                )
                journal_extra.extend(list_journal)
                if list_fatal:
                    raise AgentToolError(
                        list_fatal["detail"],
                        list_fatal.get("code", "INTERNAL_ERROR"),
                        500,
                    )
                selected = [
                    int(item["paper_id"])
                    for item in (listed or [])
                    if item.get("paper_id") is not None
                ][:target_count]
            if not selected:
                raise AgentToolError(
                    "工作区内没有可用论文，请先导入或关联论文",
                    "NO_PAPERS",
                    404,
                )
            return {
                "search_results": search,
                "selected_paper_ids": selected[:target_count],
                "_journal_extra": journal_extra,
            }

        result, journal, fatal = await _traced(
            "search_papers",
            tool="paper_search",
            arguments=args,
            summary_fn=lambda data: (
                f"hits={len((data or {}).get('selected_paper_ids') or [])}"
            ),
            body=_body,
        )
        update: ResearchHarnessState = {"journal": journal}
        if fatal:
            update["fatal_error"] = fatal
            return update
        assert result is not None
        extra = result.pop("_journal_extra", [])
        if extra:
            update["journal"] = journal + extra
        update["search_results"] = result.get("search_results") or {}
        update["selected_paper_ids"] = result.get("selected_paper_ids") or []
        return update

    async def load_paper_contexts(state: ResearchHarnessState) -> ResearchHarnessState:
        if state.get("fatal_error"):
            return {}

        async def _body():
            loaded: list[dict[str, Any]] = []
            for paper_id in state.get("selected_paper_ids", []):
                loaded.append(
                    await _maybe_await(call_tool("paper_get", {"paper_id": paper_id}))
                )
            return loaded

        contexts, journal, fatal = await _traced(
            "load_paper_contexts",
            summary_fn=lambda data: f"loaded={len(data or [])}",
            body=_body,
        )
        update: ResearchHarnessState = {"journal": journal}
        if fatal:
            update["fatal_error"] = fatal
            return update
        update["paper_contexts"] = contexts or []
        return update

    async def retrieve_evidence(state: ResearchHarnessState) -> ResearchHarnessState:
        if state.get("fatal_error"):
            return {}
        args = {
            "workspace_id": state["workspace_id"],
            "query": _safe(state.get("search_query")) or state["goal"],
            "top_k": 8,
            "paper_ids": state.get("selected_paper_ids"),
        }
        evidence, journal, _fatal = await _traced(
            "retrieve_evidence",
            tool="paper_retrieve_evidence",
            arguments=args,
            soft_fail=True,
            summary_fn=lambda data: (
                f"evidence={len((data or {}).get('evidence', []))}"
                if data
                else "skipped/unavailable"
            ),
            body=lambda: call_tool("paper_retrieve_evidence", args),
        )
        return {"evidence": evidence, "journal": journal}

    async def export_brief(state: ResearchHarnessState) -> ResearchHarnessState:
        if state.get("fatal_error"):
            return {}
        markdown = _render_literature_brief(
            goal=state["goal"],
            workspace_id=state["workspace_id"],
            contexts=state.get("paper_contexts") or [],
            evidence=state.get("evidence"),
            goal_plan=state.get("goal_plan"),
        )
        _, journal, fatal = await _traced(
            "export_brief",
            summary_fn=lambda _: f"markdown_chars={len(markdown)}",
            body=lambda: {"markdown": markdown},
        )
        update: ResearchHarnessState = {"markdown": markdown, "journal": journal}
        if fatal:
            update["fatal_error"] = fatal
        return update

    graph = StateGraph(ResearchHarnessState)
    graph.add_node("parse_goal", parse_goal)
    graph.add_node("search_papers", search_papers)
    graph.add_node("load_paper_contexts", load_paper_contexts)
    graph.add_node("retrieve_evidence", retrieve_evidence)
    graph.add_node("export_brief", export_brief)
    graph.set_entry_point("parse_goal")
    graph.add_edge("parse_goal", "search_papers")
    graph.add_edge("search_papers", "load_paper_contexts")
    graph.add_edge("load_paper_contexts", "retrieve_evidence")
    graph.add_edge("retrieve_evidence", "export_brief")
    graph.add_edge("export_brief", END)
    return graph.compile()


def build_survey_path_graph(
    call_tool: ToolCaller,
    plan_goal: Optional[GoalPlanner] = None,
):
    """Compile LangGraph for survey_path."""

    END, StateGraph = _require_langgraph()

    async def parse_goal(state: ResearchHarnessState) -> ResearchHarnessState:
        goal = _safe(state.get("goal"))
        plan, journal = await _run_goal_planner(
            goal=goal,
            workflow="survey_path",
            plan_goal=plan_goal,
        )
        return {
            "search_query": _safe(plan.get("search_query")) or goal,
            "goal_plan": plan,
            "journal": journal,
        }

    async def reading_plan_build(state: ResearchHarnessState) -> ResearchHarnessState:
        if state.get("fatal_error"):
            return {}
        args = {"workspace_id": state["workspace_id"]}
        plan, journal, fatal = await _traced(
            "reading_plan_build",
            tool="reading_plan_build",
            arguments=args,
            summary_fn=lambda data: (
                f"stages={len((data or {}).get('stages', []))} "
                f"papers={sum(len(s.get('papers', [])) for s in (data or {}).get('stages', []))}"
            ),
            body=lambda: call_tool("reading_plan_build", args),
        )
        update: ResearchHarnessState = {"journal": journal}
        if fatal:
            update["fatal_error"] = fatal
            return update
        selected: list[int] = []
        if plan:
            selected = [
                int(p["paper_id"])
                for stage in plan.get("stages", [])
                for p in stage.get("papers", [])
                if p.get("paper_id") is not None
            ]
        update["reading_plan"] = plan
        update["selected_paper_ids"] = selected
        return update

    async def survey_build(state: ResearchHarnessState) -> ResearchHarnessState:
        if state.get("fatal_error"):
            return {}
        args = {"workspace_id": state["workspace_id"]}
        survey, journal, fatal = await _traced(
            "survey_build",
            tool="survey_build",
            arguments=args,
            summary_fn=lambda data: f"matrix_rows={len((data or {}).get('matrix', []))}",
            body=lambda: call_tool("survey_build", args),
        )
        update: ResearchHarnessState = {"journal": journal}
        if fatal:
            update["fatal_error"] = fatal
            return update
        update["survey"] = survey
        return update

    async def export_survey_package(state: ResearchHarnessState) -> ResearchHarnessState:
        if state.get("fatal_error"):
            return {}
        markdown = _render_survey_package(
            goal=state["goal"],
            workspace_id=state["workspace_id"],
            plan=state.get("reading_plan"),
            survey=state.get("survey"),
            goal_plan=state.get("goal_plan"),
        )
        _, journal, fatal = await _traced(
            "export_survey_package",
            summary_fn=lambda _: f"markdown_chars={len(markdown)}",
            body=lambda: {"markdown": markdown},
        )
        update: ResearchHarnessState = {"markdown": markdown, "journal": journal}
        if fatal:
            update["fatal_error"] = fatal
        return update

    graph = StateGraph(ResearchHarnessState)
    graph.add_node("parse_goal", parse_goal)
    graph.add_node("reading_plan_build", reading_plan_build)
    graph.add_node("survey_build", survey_build)
    graph.add_node("export_survey_package", export_survey_package)
    graph.set_entry_point("parse_goal")
    graph.add_edge("parse_goal", "reading_plan_build")
    graph.add_edge("reading_plan_build", "survey_build")
    graph.add_edge("survey_build", "export_survey_package")
    graph.add_edge("export_survey_package", END)
    return graph.compile()


def build_research_graph(
    workflow: str,
    call_tool: ToolCaller,
    plan_goal: Optional[GoalPlanner] = None,
):
    if workflow == "literature_brief":
        return build_literature_brief_graph(call_tool, plan_goal=plan_goal)
    if workflow == "survey_path":
        return build_survey_path_graph(call_tool, plan_goal=plan_goal)
    raise AgentToolError(f"未知 workflow: {workflow}", "NOT_FOUND", 404)


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
        "method_overview": (
            (profile.get("method") or {}).get("overview")
            if isinstance(profile.get("method"), dict)
            else profile.get("method_overview") or ""
        ),
        "limitations": profile.get("limitations") or "",
        "experiments": profile.get("experiments") or {},
    }


def _cell(value: Any, limit: int = 80) -> str:
    text = _safe(value).replace("|", "/")
    text = " ".join(text.split())
    if not text:
        return "待核实"
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _render_literature_brief(
    *,
    goal: str,
    workspace_id: int,
    contexts: list[dict[str, Any]],
    evidence: Optional[dict[str, Any]],
    goal_plan: Optional[dict[str, Any]],
) -> str:
    version = WORKFLOW_SPECS["literature_brief"]["version"]
    lines = [
        "# Literature Brief",
        "",
        f"- Goal: {goal}",
        f"- Workspace: {workspace_id}",
        f"- Planned query: {_safe((goal_plan or {}).get('search_query')) or goal}",
        f"- Planner: {_safe((goal_plan or {}).get('planner')) or 'deterministic'}",
        f"- Generated: {_utcnow()}",
        f"- Engine: langgraph/literature_brief@{version}",
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

    planner_note = (
        f"- parse_goal 使用 {_safe((goal_plan or {}).get('model')) or 'configured LLM'} 生成检索计划。"
        if (goal_plan or {}).get("planner") == "llm"
        else "- parse_goal 使用确定性回退，直接保留原始 goal 作为检索词。"
    )
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
            planner_note,
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
    goal_plan: Optional[dict[str, Any]],
) -> str:
    version = WORKFLOW_SPECS["survey_path"]["version"]
    lines = [
        "# Survey + Reading Path Package",
        "",
        f"- Goal: {goal}",
        f"- Workspace: {workspace_id}",
        f"- Planner: {_safe((goal_plan or {}).get('planner')) or 'deterministic'}",
        f"- Generated: {_utcnow()}",
        f"- Engine: langgraph/survey_path@{version}",
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

    lines.extend(
        [
            "## Comparison Matrix",
            "",
            "| Paper | Task/Problem | Method | Data/Benchmark | Result | Limitation |",
            "|---|---|---|---|---|---|",
        ]
    )
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


async def run_research_workflow(
    *,
    workflow: str,
    goal: str,
    workspace_id: int,
    call_tool: Optional[ToolCaller] = None,
    plan_goal: Optional[GoalPlanner] = None,
) -> dict[str, Any]:
    from .llm import set_llm_scope, set_llm_stage

    set_llm_scope("workspace_research")
    set_llm_stage("agent_workflow")
    if workflow not in WORKFLOW_SPECS:
        raise AgentToolError(f"未知 workflow: {workflow}", "NOT_FOUND", 404)
    goal = _safe(goal)
    if not goal:
        raise AgentToolError("goal 不能为空", "BAD_REQUEST", 400)
    if not isinstance(workspace_id, int) or workspace_id <= 0:
        raise AgentToolError("workspace_id 无效", "BAD_REQUEST", 400)

    tool_caller = call_tool or call_agent_tool
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    started_at = _utcnow()
    initial: ResearchHarnessState = {
        "run_id": run_id,
        "workflow": workflow,
        "goal": goal,
        "workspace_id": workspace_id,
        "journal": [],
        "selected_paper_ids": [],
        "paper_contexts": [],
        "goal_plan": None,
        "markdown": "",
        "fatal_error": None,
    }

    try:
        graph = build_research_graph(workflow, tool_caller, plan_goal=plan_goal)
        final_state = await graph.ainvoke(initial)
    except AgentToolError as exc:
        return {
            "run_id": run_id,
            "workflow": workflow,
            "goal": goal,
            "workspace_id": workspace_id,
            "status": "failed",
            "started_at": started_at,
            "finished_at": _utcnow(),
            "journal": [],
            "artifacts": {
                "markdown": "",
                "selected_paper_ids": [],
                "reading_plan": None,
                "survey": None,
                "evidence": None,
                "paper_contexts": [],
                "goal_plan": None,
            },
            "error": {"detail": exc.message, "code": exc.code},
            "harness": {
                "engine": "langgraph",
                "llm_required": False,
                "note": "LangGraph dependency missing or workflow build failed.",
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "run_id": run_id,
            "workflow": workflow,
            "goal": goal,
            "workspace_id": workspace_id,
            "status": "failed",
            "started_at": started_at,
            "finished_at": _utcnow(),
            "journal": [],
            "artifacts": {
                "markdown": "",
                "selected_paper_ids": [],
                "reading_plan": None,
                "survey": None,
                "evidence": None,
                "paper_contexts": [],
                "goal_plan": None,
            },
            "error": {"detail": str(exc), "code": "INTERNAL_ERROR"},
            "harness": {
                "engine": "langgraph",
                "llm_required": False,
                "note": "LangGraph invoke failed.",
            },
        }

    fatal = final_state.get("fatal_error")
    status = "failed" if fatal else "completed"
    return {
        "run_id": run_id,
        "workflow": workflow,
        "goal": goal,
        "workspace_id": workspace_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _utcnow(),
        "journal": list(final_state.get("journal") or []),
        "artifacts": {
            "markdown": final_state.get("markdown") or "",
            "selected_paper_ids": final_state.get("selected_paper_ids") or [],
            "reading_plan": final_state.get("reading_plan"),
            "survey": final_state.get("survey"),
            "evidence": final_state.get("evidence"),
            "paper_contexts": final_state.get("paper_contexts") or [],
            "goal_plan": final_state.get("goal_plan"),
        },
        "error": fatal,
        "harness": {
            "engine": "langgraph",
            "llm_required": False,
            "workflow_version": WORKFLOW_SPECS[workflow]["version"],
            "planner": (final_state.get("goal_plan") or {}).get("planner", "deterministic"),
            "note": (
                "Orchestration uses LangGraph StateGraph; "
                "parse_goal uses an optional bounded LLM planner with deterministic fallback."
            ),
        },
    }
