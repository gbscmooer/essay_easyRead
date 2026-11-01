"""单循环：search → judge → expand（按层）→ refine → 概览 → 作答。一次研究只走这一条路径。

- ResearchLoop：纯循环（不碰数据库），状态全部在 LoopState，可被评测脚本直接调用（eval/crawl_recall.py）；
- run_research：后台任务外壳，负责读写 ExplorationRun、SSE 事件、暂停/取消/失败收尾。
每个动作之后 LoopState 可序列化进 ExplorationRun.checkpoint，暂停/崩溃后从同一动作继续。
阶段（current_stage）只是给前端看的进度标签：search / expand / landscape / answer。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

from sqlmodel import Session

from ....models.exploration import ExplorationRun
from ....models.schemas import StreamEvent
from ...database import get_engine
from ...research_exclusions import excluded
from .config import ANSWER_DEFAULTS, LoopConfig, config_for
from .crawler import Crawler
from .llm import configured, json_caller
from .papers import aliases, merge_into, public
from .runtime import cancel_pause_request, get_bus, pause_requested
from .selector import Selector
from .sources import Sources

logger = logging.getLogger(__name__)

CHECKPOINT_VERSION = "pasa_loop_v1"
SAVE_INTERVAL = 10.0
EXPAND_CONCURRENCY = 3
_REVIEW = re.compile(r"review|survey|overview|state of the art|systematic|meta-analy", re.I)
_DERIVED = ("label", "rerank", "score", "cluster", "cluster_label", "origin", "parent", "via", "depth")

Emit = Callable[..., Awaitable[None]]


class Paused(Exception):
    pass


@dataclass
class LoopState:
    phase: str = "search"                 # search → expand → landscape → answer → done
    layer: int = 0
    refine_done: int = 0
    seeded: bool = False
    queries_todo: list[str] = field(default_factory=list)
    queries_done: list[str] = field(default_factory=list)
    papers: dict[str, dict] = field(default_factory=dict)
    alias: dict[str, str] = field(default_factory=dict)
    expanded: list[str] = field(default_factory=list)
    frontier: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    excluded: int = 0
    stop_reason: str = ""
    selector: dict = field(default_factory=dict)
    timeline: list[dict] = field(default_factory=list)
    elapsed: float = 0.0                  # 已用的研究侧时长（暂停不计，续跑累加）

    @classmethod
    def restore(cls, data: dict | None) -> "LoopState":
        if not data or data.get("version") != CHECKPOINT_VERSION:
            return cls()
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

    def dump(self) -> dict:
        return {"version": CHECKPOINT_VERSION, **asdict(self)}


async def _noop_emit(event: str, **data) -> None:
    return None


class ResearchLoop:
    """检索 + 判定 + 展开的循环本体。llm_cfg 为 None 时不调模型（检索式退回原问题，判定只看交叉编码器）。"""

    def __init__(self, question: str, cfg: LoopConfig, *, llm_cfg: dict | None, options: dict | None = None,
                 state: LoopState | None = None, seeds: list[dict] | None = None, reused: list[dict] | None = None,
                 dead: set[str] | None = None, paper_filter=None, rerank=None,
                 emit: Emit | None = None, should_pause: Callable[[], bool] | None = None,
                 on_step: Callable[[], None] | None = None, search_one=None):
        self.question = question
        self.cfg = cfg
        self.options = dict(options or {})
        self.state = state or LoopState()
        self.seeds = list(seeds or [])
        self.reused = list(reused or [])
        self.filter = paper_filter
        self.emit = emit or _noop_emit
        self.should_pause = should_pause or (lambda: False)
        self.on_step = on_step or (lambda: None)
        self.has_llm = configured(llm_cfg)
        self.online = bool(self.options.get("search_online", True))
        conditions = list(self.options.get("revision_conditions") or [])
        self.sources = Sources(enabled=cfg.sources, dead=dead, online=self.online, search_one=search_one)
        criteria = question if not conditions else f"{question}\n补充条件：" + "；".join(conditions)
        self.selector = Selector(criteria, json_caller(llm_cfg, max_tokens=64 + 4 * cfg.judge_batch)
                                 if self.has_llm else None, cfg, rerank=rerank)
        self.selector.load(self.state.selector)
        self.crawler = Crawler(question, json_caller(llm_cfg, max_tokens=600) if self.has_llm else None,
                               self.sources, cfg, conditions=conditions)
        self.seed_keys = {p["key"] for p in self.seeds}
        self._t0 = time.monotonic()

    # ── 时间 ────────────────────────────────────────────────────────────────
    def elapsed(self) -> float:
        return self.state.elapsed + (time.monotonic() - self._t0)

    def freeze_clock(self) -> None:
        self.state.elapsed = self.elapsed()
        self._t0 = time.monotonic()

    def time_up(self) -> bool:
        return self.cfg.time_cap_seconds > 0 and self.elapsed() >= self.cfg.time_cap_seconds

    def check_pause(self) -> None:
        if self.should_pause():
            raise Paused()

    # ── 候选池 ──────────────────────────────────────────────────────────────
    def add(self, paper: dict, *, origin: str, parent: str | None = None, via: str = "", depth: int = 0) -> dict | None:
        """入池：跨数据源按身份合并；排除项与时间截断在这里生效。返回新入池的论文（重复的返回 None）。"""
        st = self.state
        if excluded(paper, self.options) or (self.filter is not None and not self.filter(paper)):
            st.excluded += 1
            return None
        names = aliases(paper)
        hit = next((st.alias[a] for a in names if a in st.alias), None)
        if hit is not None:
            merge_into(st.papers[hit], paper)
            for a in names:
                st.alias.setdefault(a, hit)
            return None
        row = {k: v for k, v in paper.items() if k not in _DERIVED}
        row.update(origin=paper.get("origin") or origin, parent=paper.get("parent") or parent,
                   via=paper.get("via") or via, depth=int(paper.get("depth") or depth))
        st.papers[row["key"]] = row
        for a in names:
            st.alias[a] = row["key"]
        return row

    def remove_excluded(self) -> int:
        st = self.state
        gone = [k for k, p in st.papers.items() if excluded(p, self.options)]
        for k in gone:
            st.papers.pop(k, None)
        st.alias = {a: k for a, k in st.alias.items() if k in st.papers}
        st.frontier = [k for k in st.frontier if k in st.papers]
        return len(gone)

    def selected(self) -> list[dict]:
        rows = [self.selector.apply(p) for p in self.state.papers.values() if self.selector.selected(p)]
        return sorted(rows, key=lambda p: -float(p.get("score") or 0.0))

    # ── 动作 ────────────────────────────────────────────────────────────────
    async def judge(self, new: list[dict]) -> dict:
        if new:
            await self.sources.fill_abstracts(new)
        return await self.selector.judge(new, forced=self.seed_keys)

    async def search(self, query: str, origin: str = "search") -> None:
        rows = await self.crawler.search(query)
        new = [p for p in (self.add(r, origin=origin, via=query) for r in rows) if p]
        got = await self.judge(new)
        self.state.queries_done.append(query)
        step = {"action": origin, "query": query, "hits": len(rows), "new": len(new),
                "selected": got["selected"], "llm_calls": got["llm_calls"]}
        self.state.trace.append(step)
        top = [{"key": p["key"], "title": p["title"], "label": p.get("label")}
               for p in sorted(new, key=lambda p: -float(p.get("score") or 0)) if self.selector.selected(p)][:5]
        await self.emit("crawl_step", **step, candidates=len(self.state.papers),
                        total_selected=len(self.selected()), top=top)

    async def expand(self, key: str) -> None:
        paper = self.state.papers.get(key)
        if paper is None:
            return
        rows, audit = await self.crawler.expand(paper)
        new = [p for p in (self.add(r, origin="expand", parent=key, via=r.get("via", ""), depth=r.get("depth", 1))
                           for r in rows) if p]
        got = await self.judge(new)
        self.state.expanded.append(key)
        step = {"action": "expand", "paper": key, "title": paper.get("title"), "layer": self.state.layer,
                **{k: v for k, v in audit.items() if k != "sections"}, "new": len(new),
                "selected": got["selected"], "llm_calls": got["llm_calls"]}
        self.state.trace.append(step)
        await self.emit("crawl_step", **step, candidates=len(self.state.papers), total_selected=len(self.selected()))

    def next_frontier(self) -> list[str]:
        done = set(self.state.expanded)
        rows = [p for p in self.selected() if p["key"] not in done]
        rows.sort(key=lambda p: -(float(p.get("score") or 0) + (0.1 if _REVIEW.search(p.get("title") or "") else 0)))
        return [p["key"] for p in rows[:self.cfg.expand_papers]]

    # ── 阶段 ────────────────────────────────────────────────────────────────
    async def run_search(self) -> None:
        st = self.state
        if not st.seeded:
            st.seeded = True
            for p in self.seeds:
                self.add(p, origin="seed")
            for p in self.reused:
                self.add({k: v for k, v in p.items() if k not in _DERIVED}, origin="reuse")
            if st.papers:
                await self.judge(list(st.papers.values()))
            if self.sources.enabled:
                st.queries_todo = await self.crawler.initial_queries(self.options.get("user_queries"))
            await self.emit("queries_ready", queries=st.queries_todo)
        while st.queries_todo:
            self.check_pause()
            if self.time_up():
                st.stop_reason = "time_cap"
                break
            await self.search(st.queries_todo.pop(0), "search")
            self.on_step()

    async def run_expand(self) -> None:
        st, cfg = self.state, self.cfg
        can_expand = self.online and bool(self.sources.enabled) and cfg.expand_layers > 0 and self.sources.custom is None
        while can_expand and st.layer < cfg.expand_layers and not st.stop_reason:
            if not st.frontier:
                st.frontier = self.next_frontier()
                if not st.frontier:
                    st.stop_reason = "no_frontier"
                    break
                await self.emit("layer_start", layer=st.layer, frontier=len(st.frontier))
            while st.frontier:
                self.check_pause()
                if self.time_up():
                    st.stop_reason = "time_cap"
                    break
                batch, st.frontier = st.frontier[:EXPAND_CONCURRENCY], st.frontier[EXPAND_CONCURRENCY:]
                await asyncio.gather(*(self.expand(k) for k in batch))
                self.on_step()
            if st.stop_reason:
                break
            if st.refine_done < cfg.refine_rounds and self.has_llm:
                self.check_pause()
                st.refine_done += 1
                for q in await self.crawler.refine_queries(self.selected(), st.queries_done):
                    self.check_pause()
                    if self.time_up():
                        st.stop_reason = "time_cap"
                        break
                    await self.search(q, "refine")
                    self.on_step()
            st.layer += 1
        if not st.stop_reason:
            st.stop_reason = "layers_done" if can_expand else "no_expansion"

    async def crawl(self) -> list[dict]:
        """评测入口：只跑检索与展开，返回选中论文。"""
        try:
            if self.state.phase == "search":
                await self.run_search()
                self.state.phase = "expand"
            if self.state.phase == "expand":
                await self.run_expand()
                self.state.phase = "landscape"
            return self.selected()
        finally:
            await self.sources.aclose()

    def crawl_report(self) -> dict:
        chosen = self.selected()
        return {"version": CHECKPOINT_VERSION, "config": self.cfg.to_dict(), "phase": self.state.phase,
                "layer": self.state.layer, "stop_reason": self.state.stop_reason,
                "candidates": len(self.state.papers), "selected": len(chosen), "excluded": self.state.excluded,
                "expanded": len(self.state.expanded), "trace": self.state.trace[-300:],
                "selector": self.selector.stats, "crawler": self.crawler.stats, "sources": self.sources.audit,
                "by_origin": _count(chosen, "origin"), "by_depth": _count(chosen, "depth"),
                "elapsed_s": round(self.elapsed(), 1)}

    def dump_state(self) -> dict:
        self.state.selector = self.selector.dump()
        return self.state.dump()


# ── 落库 ────────────────────────────────────────────────────────────────────

def _update_run(run_id: int, **fields) -> None:
    with Session(get_engine()) as s:
        run = s.get(ExplorationRun, run_id)
        if run is None:
            return
        for k, v in fields.items():
            setattr(run, k, v)
        run.updated_at = datetime.now(timezone.utc)
        s.add(run)
        s.commit()


def _run_status(run_id: int) -> str | None:
    with Session(get_engine()) as s:
        run = s.get(ExplorationRun, run_id)
        return run.status if run else None


def _count(rows: list[dict], field_name: str) -> dict:
    out: dict = {}
    for r in rows:
        k = str(r.get(field_name))
        out[k] = out.get(k, 0) + 1
    return out


def _parent_papers(options: dict) -> list[dict]:
    """修正：复用父研究选中的论文，按新问题重新判定（旧判定字段在入池时去掉）。"""
    parent_id = options.get("reuse_from_run_id")
    if not parent_id:
        return []
    with Session(get_engine()) as s:
        parent = s.get(ExplorationRun, int(parent_id))
        return list(((parent.result or {}).get("papers") or []) if parent else [])


def _consume_pending(run_id: int) -> list[str]:
    with Session(get_engine()) as s:
        run = s.get(ExplorationRun, run_id)
        if run is None:
            return []
        opts = dict(run.options or {})
        pending = list(opts.pop("pending_user_queries", None) or [])
        opts["user_queries"] = [*(opts.get("user_queries") or []), *pending]
        run.options = opts
        s.add(run)
        s.commit()
        return pending


async def _preflight(online: bool, warnings: list[str]) -> tuple[list[dict], set[str]]:
    if not online:
        return [], set()
    try:
        from ..preflight import dead_source_names, preflight_research_sources

        healths = await preflight_research_sources()
        return healths, dead_source_names(healths)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"数据源预检未完成（{type(exc).__name__}），按全部可用处理。")
        return [], set()


# ── 后台任务 ────────────────────────────────────────────────────────────────

async def run_research(run_id: int, *, resume: bool = False) -> None:
    from ... import llm as llm_core
    from ...ai_config import get_analysis_config
    from ...research_sources import load_seeds
    from ..reranker import rerank_scores
    from ..time_machine import make_filter

    bus = get_bus(run_id)
    cancel_pause_request(run_id)   # 上次会话可能残留未消费的暂停旗标

    async def emit(event: str, **data) -> None:
        data.setdefault("ts", time.time())
        await bus.publish(StreamEvent(event=event, data=data))

    warnings: list[str] = []
    with Session(get_engine()) as s:
        run = s.get(ExplorationRun, run_id)
        if run is None:
            return
        question = (run.question or "").strip()
        depth = run.depth or "standard"
        options = dict(run.options or {})
        instance_id = run.run_instance_id
        state = LoopState.restore(run.checkpoint if resume else None)
        result: dict = dict(run.result or {}) if resume else {}
        warnings = list(result.get("warnings") or [])
        try:
            seeds, _ = load_seeds(s, options)
        except Exception as exc:  # noqa: BLE001 —— 母论文已删除等：照常研究，记警告
            seeds = []
            warnings.append(f"母论文读取失败：{exc}")

    cfg: LoopConfig = config_for(depth, options.get("loop"))
    llm_cfg = get_analysis_config()
    online = bool(options.get("search_online", True))
    ledger = llm_core.start_token_ledger()
    llm_core.set_llm_scope("exploration", run_id=str(run_id), run_instance_id=instance_id, operation="research")
    preflight = list((result.get("sources") or {}).get("preflight") or [])
    if preflight:
        from ..preflight import dead_source_names

        dead = dead_source_names(preflight)
    else:
        preflight, dead = await _preflight(online, warnings)

    clock = {"saved": 0.0, "stage": None, "stage_t": 0.0}
    loop: ResearchLoop

    def save(force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - clock["saved"] < SAVE_INTERVAL:
            return
        clock["saved"] = now
        try:
            _update_run(run_id, checkpoint=loop.dump_state(), result=snapshot(),
                        decompose={"queries": loop.state.queries_done})
        except Exception:  # noqa: BLE001
            logger.warning("checkpoint save failed for run %s", run_id, exc_info=True)

    loop = ResearchLoop(question, cfg, llm_cfg=llm_cfg, options=options, state=state, seeds=seeds,
                        reused=_parent_papers(options) if not state.seeded else [], dead=dead,
                        paper_filter=make_filter(question, options.get("question_year"),
                                                 str(options.get("time_machine") or "")),
                        rerank=rerank_scores, emit=emit, should_pause=lambda: pause_requested(run_id),
                        on_step=save)
    if not loop.has_llm:
        warnings.append("未配置模型：检索式退回原问题，相关性只按交叉编码器排序，无法作答。")
    if online and not loop.sources.enabled:
        warnings.append("没有可用的在线数据源（预检全部失败），只能使用母论文与复用材料。")

    def snapshot() -> dict:
        return {**result, "question": question, "queries": loop.state.queries_done,
                "papers": [public(p) for p in loop.selected()], "crawl": loop.crawl_report(),
                "sources": {**options, "preflight": preflight, "warnings": warnings,
                            "stage_timeline": loop.state.timeline,
                            "token_usage": llm_core.token_ledger_report(ledger), "elapsed_s": round(loop.elapsed(), 1)},
                "warnings": warnings}

    def stage_begin(stage: str) -> None:
        llm_core.set_llm_stage(stage)
        clock["stage"], clock["stage_t"] = stage, time.monotonic()
        loop.state.timeline.append({"stage": stage, "started_at": datetime.now(timezone.utc).isoformat(),
                                    "duration_s": None, "status": "running"})
        _update_run(run_id, current_stage=stage, status="running")

    def stage_end(status: str = "completed", **counts) -> None:
        for row in reversed(loop.state.timeline):
            if row["stage"] == clock["stage"] and row["duration_s"] is None:
                row.update(duration_s=round(time.monotonic() - clock["stage_t"], 2), status=status, **counts)
                break
        clock["stage"] = None

    st = loop.state
    try:
        _update_run(run_id, status="running", error_message=None)
        if resume:
            removed = loop.remove_excluded()
            if removed:
                warnings.append(f"暂停期间剔除了 {removed} 篇文献。")
            pending = [q for q in _consume_pending(run_id) if q]
            if pending:
                st.queries_todo = list(dict.fromkeys([*pending, *st.queries_todo]))
                if st.phase not in ("search", "expand"):
                    st.phase = "search"
        await emit("run_started", resume=resume, config=cfg.to_dict())

        if st.phase == "search":
            stage_begin("search")
            await emit("stage_start", stage="search")
            await loop.run_search()
            stage_end(candidates=len(st.papers), selected=len(loop.selected()))
            await emit("stage_done", stage="search", summary={"candidates": len(st.papers),
                                                              "selected": len(loop.selected())})
            st.phase = "expand"
            save(force=True)

        if st.phase == "expand":
            stage_begin("expand")
            await emit("stage_start", stage="expand")
            await loop.run_expand()
            stage_end(candidates=len(st.papers), selected=len(loop.selected()), expanded=len(st.expanded))
            await emit("stage_done", stage="expand", summary={"expanded": len(st.expanded),
                                                              "selected": len(loop.selected()),
                                                              "stop_reason": st.stop_reason})
            st.phase = "landscape"
            save(force=True)

        chosen = loop.selected()
        if st.phase == "landscape":
            loop.check_pause()
            stage_begin("landscape")
            await emit("stage_start", stage="landscape")
            from .landscape import build_landscape

            land = await build_landscape(chosen, now_year=datetime.now(timezone.utc).year,
                                         enrich=online and bool(options.get("enrich", True)))
            for p in chosen:   # 聚类与富化字段写回池中，checkpoint 与 result 一致
                st.papers[p["key"]].update({k: v for k, v in p.items() if k not in ("label", "rerank", "score")})
            result.update(metrics=land["metrics"], graph=land["graph"])
            stage_end(clusters=land["metrics"].get("clusters"))
            await emit("metrics_ready", **snapshot())
            st.phase = "answer"
            save(force=True)

        if st.phase == "answer":
            loop.check_pause()
            stage_begin("answer")
            await emit("stage_start", stage="answer")
            if loop.has_llm:
                result["answer"] = await answer_question(question, loop.selected()[:cfg.answer_papers], llm_cfg,
                                                         options, online, loop.filter)
            else:
                result["answer"] = {"status": "failed", "error": "no_llm", "text": "", "references": []}
            stage_end(status="failed" if result["answer"].get("status") == "failed" else "completed")
            await emit("answer_ready", **result["answer"])
            st.phase = "done"

        loop.freeze_clock()
        _update_run(run_id, status="done", current_stage="answer", result=snapshot(), checkpoint=None,
                    decompose={"queries": st.queries_done}, provider="+".join(loop.sources.enabled) or "offline")
        await emit("done", run_id=run_id, stop_reason=st.stop_reason)
    except (Paused, asyncio.CancelledError) as exc:
        stage_end(status="paused")
        loop.freeze_clock()
        final_status = "cancelled" if _run_status(run_id) == "cancelled" else "paused"
        _update_run(run_id, status=final_status, checkpoint=loop.dump_state(), result=snapshot())
        cancel_pause_request(run_id)
        await emit(final_status, run_id=run_id, stage=st.phase, forced=isinstance(exc, asyncio.CancelledError))
    except Exception as exc:  # noqa: BLE001
        logger.exception("research run %s failed", run_id)
        stage_end(status="failed")
        loop.freeze_clock()
        _update_run(run_id, status="failed", error_message=f"{type(exc).__name__}: {str(exc)[:300]}",
                    checkpoint=loop.dump_state(), result=snapshot())
        await emit("error", message=f"研究失败（{type(exc).__name__}），可从断点继续", stage=st.phase)
    finally:
        await loop.sources.aclose()


async def answer_question(question: str, papers: list[dict], llm_cfg: dict, options: dict, online: bool,
                          paper_filter=None) -> dict:
    """选中论文 → 指针式作答；失败时退回只依据选中论文摘要作答。评测脚本也调用这里。"""
    from .. import answer_writer as grounded
    from ..arxiv_mirror import default_mirror
    from .answer import write_answer

    opts = {**ANSWER_DEFAULTS, **(options.get("answer") or {})}
    web = None
    if opts.pop("web_search", None) == "scopus" and online:
        from ..evidence_search import ScopusSearch

        web = ScopusSearch()
    answer_json = json_caller(llm_cfg, max_tokens=6000, thinking=None, temperature=0.15)
    try:
        out = await write_answer(question, papers, llm_json=answer_json, mirror=default_mirror() if online else None,
                                 web_search=web, paper_filter=paper_filter,
                                 **{k: v for k, v in opts.items() if k in ANSWER_DEFAULTS})
    except Exception as exc:  # noqa: BLE001
        logger.warning("pointer answer crashed: %s", exc)
        out = {"status": "failed", "error": type(exc).__name__}
    if out.get("status") != "failed":
        return out
    fallback = await grounded.write_answer(question, papers, [], llm_json=answer_json)
    fallback["fallback_from"] = {"writer": "pointer", "error": out.get("error")}
    return fallback
