"""Background ownership, heartbeat and safe boundaries; no HTTP dependencies."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import suppress
from uuid import uuid4

from sqlmodel import Session, select

from ....models.exploration import ExplorationRun
from ....models.research_core import ResearchAsset, ResearchDossierRevision
from ... import llm
from ...ai_config import get_llm_config
from ...database import get_engine
from ...research_exclusions import active_exclusions
from ...research_sources import load_seeds
from ...research_event_log import append_event
from .contracts import SUPPORTED_LOOP_VERSIONS
from .controller import Controller
from .dossier import freeze
from .questions import initial_questions
from .repository import Repository, LeaseLost
from .resources import Resources, ResourceExhausted, limits_for
from .tools import Tools
from .writer import build_report, save_report

logger = logging.getLogger(__name__)
_workers: dict[int, asyncio.Task] = {}


def enabled():
    return os.environ.get("RESEARCH_LOOP_ENABLED", "true").lower() not in ("0", "false", "off")


def supported(run):
    return enabled() and ((run.options or {}).get("workflow") or {}).get("loop_revision") in SUPPORTED_LOOP_VERSIONS


def leased(run):
    from datetime import datetime, timezone
    stamp = run.worker_lease_expires_at
    return bool(run.worker_owner and stamp and stamp.replace(tzinfo=stamp.tzinfo or timezone.utc) > datetime.now(timezone.utc))


def active(run_id: int) -> bool:
    return run_id in _workers and not _workers[run_id].done()


def start(run_id: int, *, report_only=False):
    if active(run_id):
        return
    task = asyncio.create_task(run(run_id, report_only=report_only))
    _workers[run_id] = task
    task.add_done_callback(lambda finished: _workers.pop(run_id, None) if _workers.get(run_id) is finished else None)


async def run(run_id: int, *, engine=None, tools_factory=Tools, report_only=False):
    engine = engine or get_engine()
    repo = Repository(engine, run_id, uuid4().hex)
    if not repo.acquire():
        return
    try:
        limits = (repo.protocol() or {}).get("budget") or limits_for(
            repo.run().depth, ((repo.run().options or {}).get("workflow") or {}).get("budget"),
            revision=((repo.run().options or {}).get("workflow") or {}).get("loop_revision"))
        resources = Resources(repo, limits)
        config = get_llm_config()
        tools = tools_factory(repo, resources, config)
    except Exception:
        logger.exception("question loop %s initialization failed", run_id)
        with suppress(LeaseLost):
            with repo.transaction() as session:
                row = session.get(ExplorationRun, run_id)
                row.status, row.error_message = "failed", "研究初始化未完成，可从断点继续"
                row.state_version += 1
                session.add(row)
                append_event(session, run_id=run_id, type="run_state_changed", payload={"status": "failed", "state_version": row.state_version})
        repo.release()
        return
    previous_budget, previous_deadline = llm.llm_budget(), llm.llm_deadline()
    llm.set_llm_budget(resources)
    llm.set_llm_scope("exploration", str(run_id), run_instance_id=repo.run().run_instance_id,
                      operation="report_regenerate" if report_only else "research")
    llm.set_llm_stage("research")
    exclusions_token = active_exclusions.set(repo.run().options or {})
    last_tick = time.monotonic()

    async def heartbeat():
        nonlocal last_tick
        while True:
            await asyncio.sleep(10)
            tick = time.monotonic()
            repo.heartbeat(tick - last_tick if repo.run().status == "running" else 0)
            last_tick = tick
            if repo.run().status != "running":
                return

    pulse = asyncio.create_task(heartbeat())
    try:
        initial_stop_reason = None
        remaining = max(1, limits["wall_clock_minutes"] * 60 - repo.run().active_seconds)
        llm.set_llm_deadline(time.monotonic() + remaining)
        if not repo.protocol():
            workflow = (repo.run().options or {}).get("workflow") or {}
            specs = initial_questions(repo.run().question, workflow.get("protocol_options"))
            plan = None
            if repo.run().options.get("search_online", True) or workflow.get("revision") or workflow.get("loop_revision") in ("question_loop_v7", "question_loop_v8", "question_loop_v9", "question_loop_v10", "question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20"):
                try:
                    plan = await tools.make_plan(specs)
                    if workflow.get("loop_revision") not in ("question_loop_v8", "question_loop_v9", "question_loop_v10", "question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20") and not (workflow.get("protocol_options") or {}).get("answer_criteria"):
                        specs[0]["answer_criteria"] = [*plan["answer_units"], *specs[0]["answer_criteria"]]
                except ResourceExhausted:
                    initial_stop_reason = "resource_limit"
                except llm.LLMError as exc:
                    initial_stop_reason = "resource_limit" if exc.code.startswith("BUDGET_") else "model_error"
                except ValueError:
                    initial_stop_reason = "planning_failed"
            repo.initialize(specs, limits)
            if plan:
                tools.save_plan(plan)
        # Reuse creation-time membership, but freeze the text into immutable run assets once.
        if not repo.rows(ResearchAsset) or not any(a.content_kind == "document" for a in repo.rows(ResearchAsset)):
            with Session(engine) as session:
                seeds, _ = load_seeds(session, repo.run().options or {})
            if seeds:
                with repo.transaction() as session:
                    for paper in seeds:
                        if tools.allowed(paper):
                            session.add(ResearchAsset(run_id=run_id, **tools.document_spec(paper)))
        if not report_only and not initial_stop_reason:
            from .revisions import admit_reused_materials
            try:
                await admit_reused_materials(tools)
            except ResourceExhausted:
                initial_stop_reason = "resource_limit"
            except llm.LLMError as exc:
                initial_stop_reason = "resource_limit" if exc.code.startswith("BUDGET_") else "model_error"
            except ValueError:
                initial_stop_reason = "material_admission_failed"
        if report_only:
            dossiers = repo.rows(ResearchDossierRevision)
            if not dossiers:
                raise ValueError("尚未冻结可重生成的研究材料")
            dossier = dossiers[-1]
        else:
            try:
                remaining = max(0.01, limits["wall_clock_minutes"] * 60 - repo.run().active_seconds)
                stop_reason = initial_stop_reason or await asyncio.wait_for(Controller(tools).run(), timeout=remaining)
            except (ResourceExhausted, asyncio.TimeoutError):
                stop_reason = "resource_limit"
                with repo.transaction() as session:
                    append_event(session, run_id=run_id, type="budget_stop", payload={"reason": stop_reason})
            except llm.LLMError as exc:
                stop_reason = "resource_limit" if exc.code.startswith("BUDGET_") else "model_error"
            except ValueError:
                stop_reason = "execution_failed"
            if repo.run().status != "running":
                raise LeaseLost("研究已暂停或取消")
            dossier = freeze(tools, stop_reason)
        resources.account = "writing"
        # Writing projects already supported answers. It needs no model call to restate the same facts.
        report = build_report(tools, dossier)
        save_report(tools, dossier, report)
    except (LeaseLost, asyncio.CancelledError):
        # The persisted state controls pause/cancel; stale output is never committed.
        pass
    except Exception:
        logger.exception("question loop %s failed", run_id)
        with suppress(LeaseLost):
            with repo.transaction() as session:
                row = session.get(ExplorationRun, run_id)
                row.status, row.error_message = "failed", "研究执行未完成，已保留任务与材料，可从断点继续"
                row.state_version += 1
                session.add(row)
                append_event(session, run_id=run_id, type="run_state_changed", payload={"status": "failed", "state_version": row.state_version})
    finally:
        pulse.cancel()
        with suppress(asyncio.CancelledError, LeaseLost):
            await pulse
        with suppress(LeaseLost):
            repo.heartbeat(time.monotonic() - last_tick if repo.run().status in ("running", "completed", "partial") else 0)
        repo.release()
        if hasattr(tools, "close"):
            with suppress(Exception):
                await tools.close()
        active_exclusions.reset(exclusions_token)
        llm.set_llm_budget(previous_budget)
        llm.set_llm_deadline(previous_deadline)
