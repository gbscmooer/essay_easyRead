"""目标量驱动的多源并行召回引擎（探索流水线 recall 段升级）。

与旧 recall_papers（主备 failover + 固定 cap）的差异：
- 三源真并行：arXiv / Scopus / OpenAlex 各自独立发射，互为主备不再串行切换；
  单源错误/跳过/零命中分别记账，不把故障吞成 0 命中；
- 目标量驱动：快览 200 / 标准 500 / 深挖 1000（数量预算），不足则检索规划 LLM
  复盘语料后换词续搜；质量筛选后确认量不足时在预算内补搜；
- 零命中关键词不消耗轮次预算（上限 5 轮），绝对关键词尝试上限（15）兜底防死循环；
- 排除规则（用户盲测黑名单）在去重后立即生效：被排除论文不进入规划员输入、
  摘要筛选提示词、目标计数与裁剪；
- Scopus 搜索不返回摘要（个人 Key 权限）→ DOI 批量反查 OpenAlex（50/请求）补齐；
- 相关性三级判定：confirmed（摘要级通过）/ title_confirmed（标题级通过）/
  unknown（筛选失败待判）；unknown 保留但不挤占 confirmed 的目标配额；
- 超量裁剪走双轨配额：被引经典轨 + 年均被引近年轨各半——纯引用降序会系统性
  饿死新工作（引用需要时间累积），双轨让「奠基 + 前沿」都进语料。

引擎依赖全部可注入（search_one / llm_json / backfill_abstracts），便于单测离线运行。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# 深度档位 → 目标召回量（确认相关论文的规模，作为数量预算而非唯一停止条件）
RECALL_TARGETS: dict[str, int] = {"quick": 200, "standard": 500, "deep": 1000}
# 摘要/标题批量筛选每批篇数（快览批次小、换判断精度；标准/深挖用大批省指令开销）
FILTER_BATCH: dict[str, int] = {"quick": 5, "standard": 10, "deep": 10}
# 单源单检索式的取回上限（沿用旧档位 per_query）
PER_SOURCE_LIMIT: dict[str, int] = {"quick": 25, "standard": 40, "deep": 50}

MAX_ROUNDS = 5               # 非零命中轮的上限（零命中轮不计数）
MAX_KEYWORD_ATTEMPTS = 15    # 绝对关键词尝试上限（含零命中），防坏词死循环
MAX_KEYWORDS_PER_ROUND = 4   # 规划员每轮最多给出的新检索词数
CLASSIC_ROUNDS = 2           # 被引经典通道只在最初 N 轮发射（每轮一次，不随关键词重复）
TOPUP_ROUNDS = 2             # 筛选后确认量不足时的补搜轮上限

SearchOne = Callable[[str, str, int, str], Awaitable[list]]
LlmJson = Callable[[str, str], Awaitable[dict]]
Backfill = Callable[[list[str]], Awaitable[dict[str, str]]]


def _sanitize_keyword(q: str) -> str:
    """复用探索器的检索式消毒：剥引号/括号/字段标签/布尔算符。"""
    from .explorer import _sanitize_query

    return _sanitize_query(q)


def cited_per_year(paper: dict, now_year: int) -> float:
    """年均被引：总被引 / 论文年龄，消除纯引用排序对老论文的系统性偏好。"""
    try:
        year = int(paper.get("year") or now_year)
    except (TypeError, ValueError):
        year = now_year
    age = max(1, now_year - year + 1)
    return (paper.get("citation_count") or 0) / age


def dual_track_trim(papers: list[dict], keep: int, now_year: int) -> list[dict]:
    """双轨配额裁剪：被引经典轨 + 年均被引近年轨各半。

    经典轨保高被引奠基工作，近年轨（年均被引）保上升期新工作；两轨并集不足
    keep 时按被引轨顺序补齐。不去重破坏：输入应已按 key 去重。
    """
    if keep <= 0:
        return []
    by_cited = sorted(papers, key=lambda p: -(p.get("citation_count") or 0))
    by_cpy = sorted(papers, key=lambda p: -cited_per_year(p, now_year))
    if len(papers) <= keep:
        chosen: list[dict] = []
        seen: set[str] = set()
        for track in (by_cited, by_cpy):
            for p in track:
                if p["key"] in seen:
                    continue
                seen.add(p["key"])
                chosen.append(p)
        return chosen

    half = keep // 2
    chosen, seen = [], set()

    def _fill(candidates: list[dict], quota: int) -> None:
        for p in candidates:
            if len(chosen) >= quota:
                return
            if p["key"] in seen:
                continue
            seen.add(p["key"])
            chosen.append(p)

    _fill(by_cited, half)
    _fill(by_cpy, keep)
    _fill(by_cited, keep)  # 两轨并集仍不足（论文池太小/重合高）时按被引补齐
    return chosen[:keep]


async def _default_search_one(source: str, keyword: str, limit: int, order: str,
                              *, preflight_dead: set[str] | None = None,
                              _state: dict | None = None) -> list:
    """真实三源发射；单源失败抛异常由调用方记账（不吞成零命中）。"""
    state = _state if _state is not None else {}
    if source == "arxiv":
        from .arxiv_client import search_topic
        from ...models.recall import RecallSource, build_recall_candidate

        rows = await search_topic(keyword, limit=limit)
        out = []
        for row in rows:
            cand = build_recall_candidate(
                title=row["title"], arxiv_id=row["arxiv_id"], doi=row.get("doi"),
                year=row.get("year"), authors=row.get("authors"), abstract=row.get("abstract"),
                pdf_url=row.get("pdf_url"), citation_count=row.get("citation_count") or 0,
                source=RecallSource.SEARCH, provenance=["provider:arxiv", f"method:{order}"],
            )
            if cand:
                out.append(cand)
        return out

    if source not in ("scopus", "openalex"):
        raise ValueError(f"unknown source: {source}")
    if preflight_dead and source in preflight_dead:
        return []
    if state.get(source) is None:
        from .providers import OpenAlexProvider, ScopusProvider, _elsevier_key
        from ..ai_config import get_research_config

        cfg = dict(get_research_config())
        if source == "scopus":
            state[source] = ScopusProvider(api_key=_elsevier_key(cfg))
        else:
            state[source] = OpenAlexProvider(email=str(cfg.get("email") or ""))
    provider = state[source]
    return await provider.search_works(keyword, limit=limit, academic_only=True, order=order)


async def _default_backfill(dois: list[str], _state: dict | None = None) -> dict[str, str]:
    """DOI 批量反查 OpenAlex 摘要（Scopus 搜索不返回摘要）。"""
    from .providers import OpenAlexProvider
    from ..ai_config import get_research_config

    cfg = dict(get_research_config())
    provider = OpenAlexProvider(email=str(cfg.get("email") or ""))
    try:
        return await provider.get_abstracts_by_dois(dois)
    finally:
        aclose = getattr(provider, "aclose", None)
        if aclose:
            try:
                await aclose()
            except Exception:
                pass


def _planner_prompt(question: str, *, target: int, corpus: list[dict],
                    keyword_stats: list[dict]) -> tuple[str, str]:
    by_year: dict[str, int] = defaultdict(int)
    for p in corpus:
        by_year[str(p.get("year") or "unknown")] += 1
    recent_titles = [p.get("title") or "" for p in
                     sorted(corpus, key=lambda p: -(p.get("citation_count") or 0))[:20]]
    system = (
        "你是学术检索规划员。任务：查看当前召回语料的覆盖情况，决定下一轮检索关键词。"
        "只输出一个 JSON 对象，顶层键为 next_keywords、done、reason，键名必须完全一致：\n"
        "- next_keywords：字符串数组，最多 4 条，每条是纯英文学术关键词串"
        "（空格分隔，无引号、无布尔算符、无中文），不得与已试关键词重复；\n"
        "- done：布尔值，语料已达目标或没有值得尝试的新方向时为 true；\n"
        "- reason：一句话决策依据。\n"
        "规则：优先补覆盖缺口（缺失的年份段、缺失的技术路线、缺失的同义词）；"
        "已试关键词零命中说明该表述在数据库中不存在，必须换学界同义表述而不是原样重试。"
    )
    import json

    user = {
        "research_question": question,
        "target_corpus_size": target,
        "current_corpus_size": len(corpus),
        "by_year": dict(sorted(by_year.items())),
        "tried_keywords": [{"keyword": k["keyword"], "hits": k["raw_hits"]} for k in keyword_stats],
        "top_titles_sample": recent_titles,
    }
    return system, json.dumps(user, ensure_ascii=False)


_FILTER_SYSTEM = (
    "你是文献召回筛选员。逐篇判断论文摘要与研究问题的相关性：明确相关 → 保留不输出；"
    "跨域无关、仅字面重叠、主题明显偏离 → 列入 rejected 并给一词原因。边界样本宁可保留。"
    '只输出一个 JSON 对象：{"rejected": [{"key": "论文key", "reason": "一词原因"}]}，'
    "键名必须完全一致；key 必须原样取自输入的 papers 列表。没有需要拒绝的就输出空数组。"
)

_TITLE_FILTER_SYSTEM = (
    "你是文献召回筛选员。部分候选只有标题（无摘要）。仅依据标题判断其与研究问题的"
    "领域相关性：明显跨域/无关（如研究问题之外的学科、社会、生物主题）→ 列入 rejected；"
    "标题信息不足以判断 → 保留（不要因信息不足而拒绝）。"
    '只输出一个 JSON 对象：{"rejected": [{"key": "论文key", "reason": "一词原因"}]}，'
    "键名必须完全一致；key 必须原样取自输入。"
)


def _chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


async def _filter_pool(papers: list[dict], question: str, *, batch_size: int,
                       llm_json: LlmJson, verdict_cache: dict[str, str]) -> tuple[list[dict], dict]:
    """三级相关性筛选：摘要级（confirmed）/ 标题级（title_confirmed）/ 待判（unknown）。

    只信显式拒绝；未提及即通过；LLM 调用失败标 unknown 保留（fail-open）。
    verdict_cache 按 key 复用判定，补搜时只筛新增论文。
    """
    import json

    audit: dict = {"batches": 0, "kept": 0, "rejected": 0, "fail_open_batches": 0,
                   "confirmed": 0, "title_confirmed": 0, "unknown": 0,
                   "cache_hits": 0, "rejections": []}
    by_key = {p["key"]: p for p in papers}
    pending = [p for p in papers if p["key"] not in verdict_cache]
    audit["cache_hits"] = len(papers) - len(pending)
    with_abstract = [p for p in pending if (p.get("abstract") or "").strip()]
    title_only = [p for p in pending if not (p.get("abstract") or "").strip()]

    async def _run(batch: list[dict], system: str, passed_status: str) -> None:
        for chunk in _chunks(batch, batch_size):
            items = [{
                "key": p["key"], "title": p.get("title") or "",
                "year": p.get("year"), "citation_count": p.get("citation_count") or 0,
                **({"abstract": (p.get("abstract") or "")[:1200]}
                   if system is _FILTER_SYSTEM else {}),
            } for p in chunk]
            audit["batches"] += 1
            try:
                data = await llm_json(system, json.dumps(
                    {"research_question": question, "papers": items}, ensure_ascii=False))
                rejected = data.get("rejected") if isinstance(data, dict) and isinstance(
                    data.get("rejected"), list) else []
                reject_keys: set[str] = set()
                for item in rejected:
                    key = str(item.get("key")) if isinstance(item, dict) else ""
                    if key in by_key:
                        reject_keys.add(key)
                        audit["rejections"].append({"key": key,
                                                    "reason": str(item.get("reason") or "")[:80]})
                for p in chunk:
                    verdict_cache[p["key"]] = "rejected" if p["key"] in reject_keys else passed_status
            except Exception as exc:
                logger.warning("relevance filter batch failed (fail-open as unknown): %s", exc)
                for p in chunk:
                    verdict_cache[p["key"]] = "unknown"
                audit["fail_open_batches"] += 1

    await _run(with_abstract, _FILTER_SYSTEM, "confirmed")
    await _run(title_only, _TITLE_FILTER_SYSTEM, "title_confirmed")

    kept = [p for p in papers if verdict_cache.get(p["key"]) != "rejected"]
    for p in kept:
        status = verdict_cache.get(p["key"]) or "unknown"
        p["relevance_status"] = status
        audit[status] = audit.get(status, 0) + 1
    audit["rejected"] = len(audit["rejections"])
    audit["kept"] = len(kept)
    return kept, audit


async def run_target_recall(
    question: str,
    queries: list[str],
    *,
    depth: str,
    now_year: int,
    llm_cfg: dict | None = None,
    progress=None,
    on_event=None,
    preflight_dead: set[str] | None = None,
    search_one: SearchOne | None = None,
    llm_json: LlmJson | None = None,
    backfill_abstracts: Backfill | None = None,
) -> tuple[list[dict], dict]:
    """执行目标量驱动的多源并行召回，返回（论文清单, 审计）。

    search_one(source, keyword, limit, order) / llm_json(system, user) /
    backfill_abstracts(dois) 均可注入，缺省接真实三源与统一 LLM Core。
    """
    from .explorer import _dedupe_candidates, _usable_english_queries
    from ..research_exclusions import apply_exclusions, active_exclusions

    target = RECALL_TARGETS.get(depth, RECALL_TARGETS["standard"])
    per_source = PER_SOURCE_LIMIT.get(depth, PER_SOURCE_LIMIT["standard"])
    batch_size = FILTER_BATCH.get(depth, FILTER_BATCH["standard"])

    has_llm = bool(llm_cfg and llm_cfg.get("api_key"))
    provider_state: dict = {}
    if search_one is None:
        async def search_one(source: str, keyword: str, limit: int, order: str) -> list:
            return await _default_search_one(source, keyword, limit, order,
                                             preflight_dead=preflight_dead,
                                             _state=provider_state)
    if has_llm and llm_json is None:
        from .domain_synthesizer import _llm_json as _dj_json

        cfg = dict(llm_cfg or {})

        async def llm_json(system: str, user: str) -> dict:
            return await _dj_json(cfg, system, user, max_tokens=1500)

    async def _emit(payload: dict) -> None:
        if on_event:
            try:
                await on_event(payload)
            except Exception:
                pass

    async def _report(query: str, hit: int, total: int) -> None:
        if progress:
            try:
                await progress(query=query, hit_count=hit, total_so_far=total)
            except Exception:
                pass

    all_candidates: list = []
    keyword_stats: list[dict] = []
    classic_stats: list[dict] = []
    source_failures: dict[str, int] = defaultdict(int)
    excluded_count = 0
    tried: set[str] = set()
    attempts = 0
    effective_rounds = 0
    rounds_trace: list[dict] = []
    planner_trace: list[dict] = []
    stop_reason = "planner_unavailable"

    def _fresh_corpus() -> list[dict]:
        """去重后立即应用排除规则：被排除论文不进入任何模型输入与统计。"""
        nonlocal excluded_count
        corpus = _dedupe_candidates(list(all_candidates))
        corpus, removed_info = apply_exclusions(corpus, active_exclusions.get())
        excluded_count += int((removed_info or {}).get("count") or 0)
        return corpus

    def _next_keywords(raw: Any) -> list[str]:
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for q in raw:
            kw = _sanitize_keyword(str(q)).strip()
            if len(kw) < 3 or kw.lower() in tried:
                continue
            if re.search(r"[\u3400-\u9fff]", kw):
                continue
            out.append(kw)
        return out[:MAX_KEYWORDS_PER_ROUND]

    async def _search_keyword(kw: str) -> dict:
        """单关键词三源并行（relevance）；源错误单独记账，不吞成零命中。"""
        nonlocal attempts
        attempts += 1
        results = await asyncio.gather(
            *[search_one(src, kw, per_source, "relevance")
              for src in ("arxiv", "scopus", "openalex")],
            return_exceptions=True)
        raw: list = []
        per_source_hits: dict[str, int] = {}
        per_source_errors: dict[str, str] = {}
        for src, res in zip(("arxiv", "scopus", "openalex"), results):
            if isinstance(res, BaseException):
                per_source_hits[src] = 0
                per_source_errors[src] = type(res).__name__
                source_failures[f"{src}:{type(res).__name__}"] += 1
                logger.warning("recall engine %s failed for '%s': %s", src, kw, res)
                continue
            per_source_hits[src] = len(res)
            raw.extend(res)
        before = len(_fresh_corpus())
        all_candidates.extend(raw)
        new_papers = len(_fresh_corpus()) - before
        stat = {"keyword": kw, "raw_hits": len(raw), "new_papers": new_papers,
                "sources": per_source_hits, "source_errors": per_source_errors}
        keyword_stats.append(stat)
        await _report(kw, len(raw), len(_fresh_corpus()))
        return stat

    async def _classic_round(round_no: int, anchor: str) -> None:
        """被引经典通道：每轮对锚定词只发射一次（不随关键词数重复）。"""
        results = await asyncio.gather(
            search_one("scopus", anchor, max(12, per_source // 2), "cited"),
            search_one("openalex", anchor, max(12, per_source // 2), "cited"),
            return_exceptions=True)
        row = {"round": round_no, "keyword": anchor, "order": "cited",
               "sources": {}, "source_errors": {}}
        for src, res in zip(("scopus", "openalex"), results):
            if isinstance(res, BaseException):
                row["sources"][src] = 0
                row["source_errors"][src] = type(res).__name__
                source_failures[f"{src}:{type(res).__name__}"] += 1
                continue
            row["sources"][src] = len(res)
            all_candidates.extend(res)
        classic_stats.append(row)

    async def _planner(corpus: list[dict]) -> dict:
        """规划员调用；返回归一化后的 dict（非 dict/缺字段不致命）。"""
        system, user = _planner_prompt(question, target=target, corpus=corpus,
                                       keyword_stats=keyword_stats)
        try:
            plan = await llm_json(system, user)
        except Exception as exc:
            logger.warning("recall planner failed, stop expanding: %s", exc)
            return {"__error__": f"{type(exc).__name__}"}
        return plan if isinstance(plan, dict) else {}

    current_keywords = _next_keywords(list(queries))
    if not current_keywords:
        current_keywords = _usable_english_queries([str(q) for q in queries])[:MAX_KEYWORDS_PER_ROUND]
    tried.update(k.lower() for k in current_keywords)

    try:
        while current_keywords:
            round_no = len(rounds_trace) + 1
            round_stats = []
            for kw in current_keywords:
                if attempts >= MAX_KEYWORD_ATTEMPTS:
                    break
                round_stats.append(await _search_keyword(kw))
            if round_no <= CLASSIC_ROUNDS and round_stats:
                await _classic_round(round_no, round_stats[0]["keyword"])
            corpus = _fresh_corpus()
            rounds_trace.append({"round": round_no,
                                 "keywords": [s["keyword"] for s in round_stats],
                                 "stats": round_stats, "corpus_after": len(corpus)})
            await _emit({"phase": "round_done", "round": round_no,
                         "corpus": len(corpus), "target": target})
            if any(s["raw_hits"] > 0 for s in round_stats):
                effective_rounds += 1

            if len(corpus) >= target:
                stop_reason = "target_reached"
                break
            if attempts >= MAX_KEYWORD_ATTEMPTS:
                stop_reason = "attempt_budget"
                break
            if effective_rounds >= MAX_ROUNDS:
                stop_reason = "round_budget"
                break
            if not (has_llm and llm_json is not None):
                stop_reason = "planner_unavailable"
                break

            plan = await _planner(corpus)
            if "__error__" in plan:
                planner_trace.append({"round": round_no, "error": plan["__error__"]})
                stop_reason = "planner_error"
                break
            plan_keywords = _next_keywords(plan.get("next_keywords"))
            planner_trace.append({"round": round_no, "done": bool(plan.get("done")),
                                  "reason": str(plan.get("reason") or "")[:200],
                                  "next_keywords": plan_keywords})
            if plan.get("done") or not plan_keywords:
                stop_reason = "planner_stop"
                break
            tried.update(k.lower() for k in plan_keywords)
            current_keywords = plan_keywords

        corpus = _fresh_corpus()

        # 摘要补全：Scopus 来源无摘要 → DOI 批量反查 OpenAlex
        backfill_audit = {"needed": 0, "filled": 0}
        if backfill_abstracts is None:
            async def bf(dois: list[str]) -> dict[str, str]:
                return await _default_backfill(dois)
        else:
            async def bf(dois: list[str]) -> dict[str, str]:
                return await backfill_abstracts(dois)
        missing = [p for p in corpus if p.get("doi") and not (p.get("abstract") or "").strip()]
        backfill_audit["needed"] = len(missing)
        if missing:
            try:
                doi_map = {p["doi"].lower(): p for p in missing}
                for chunk in _chunks(missing, 50):
                    found = await bf([p["doi"] for p in chunk])
                    for doi, abstract in (found or {}).items():
                        p = doi_map.get(str(doi).lower())
                        if p and abstract:
                            p["abstract"] = abstract
                            backfill_audit["filled"] += 1
            except Exception as exc:
                logger.warning("abstract backfill failed (non-fatal): %s", exc)

        # 筛选 + 确认量驱动的预算内补搜：
        # 先按目标量 1.3 倍预裁 → 三级筛选 → 确认量不足且预算未尽时规划员补搜，
        # 补搜只筛新增论文（verdict_cache 复用），合并后按 confirmed 优先裁剪
        verdict_cache: dict[str, str] = {}
        pool = dual_track_trim(corpus, min(len(corpus), int(target * 1.3)), now_year)
        if has_llm and llm_json is not None and pool:
            kept, filter_audit = await _filter_pool(
                pool, question, batch_size=batch_size, llm_json=llm_json,
                verdict_cache=verdict_cache)
            confirmed = [p for p in kept if p.get("relevance_status") != "unknown"]
            unknown = [p for p in kept if p.get("relevance_status") == "unknown"]

            topups = 0
            while (len(confirmed) < target and topups < TOPUP_ROUNDS
                   and attempts < MAX_KEYWORD_ATTEMPTS and effective_rounds < MAX_ROUNDS
                   and stop_reason != "planner_error"):
                # 注意：搜索循环的 planner_stop 不阻止补搜——规划员的 done 针对
                # 原始候选量，质量筛选可能推翻它（筛后确认量不足就要补）
                plan = await _planner(confirmed)
                if "__error__" in plan or plan.get("done"):
                    planner_trace.append({"round": f"topup{topups + 1}",
                                          "error": plan.get("__error__") or "done"})
                    break
                plan_keywords = _next_keywords(plan.get("next_keywords"))
                if not plan_keywords:
                    stop_reason = "planner_stop"
                    break
                topups += 1
                tried.update(k.lower() for k in plan_keywords)
                round_stats = []
                for kw in plan_keywords:
                    if attempts >= MAX_KEYWORD_ATTEMPTS:
                        break
                    round_stats.append(await _search_keyword(kw))
                if any(s["raw_hits"] > 0 for s in round_stats):
                    effective_rounds += 1
                corpus = _fresh_corpus()
                rounds_trace.append({"round": f"topup{topups}",
                                     "keywords": [s["keyword"] for s in round_stats],
                                     "stats": round_stats, "corpus_after": len(corpus)})
                planner_trace.append({"round": f"topup{topups}", "done": False,
                                      "reason": f"确认量 {len(confirmed)} < 目标 {target}",
                                      "next_keywords": plan_keywords})
                known_keys = {p["key"] for p in confirmed} | {p["key"] for p in unknown}
                new_pool = [p for p in corpus if p["key"] not in known_keys]
                new_pool = dual_track_trim(new_pool, min(len(new_pool), target), now_year)
                if new_pool:
                    new_kept, new_audit = await _filter_pool(
                        new_pool, question, batch_size=batch_size, llm_json=llm_json,
                        verdict_cache=verdict_cache)
                    confirmed.extend(p for p in new_kept if p.get("relevance_status") != "unknown")
                    unknown.extend(p for p in new_kept if p.get("relevance_status") == "unknown")
                    for k in ("batches", "rejected", "fail_open_batches", "confirmed",
                              "title_confirmed", "unknown", "cache_hits"):
                        filter_audit[k] += new_audit.get(k, 0)
                    filter_audit["rejections"].extend(new_audit.get("rejections") or [])
                await _emit({"phase": "topup_done", "topup": topups,
                             "confirmed": len(confirmed), "target": target})

            if len(confirmed) >= target:
                stop_reason = "target_reached"
            filter_audit["kept"] = len(confirmed) + len(unknown)
            await _emit({"phase": "filter_done", "confirmed": len(confirmed),
                         "unknown": len(unknown), "rejected": filter_audit["rejected"]})
        else:
            confirmed, unknown = list(pool), []
            filter_audit = {"skipped": True, "batches": 0, "kept": len(pool),
                            "rejected": 0, "confirmed": len(pool), "title_confirmed": 0,
                            "unknown": 0, "fail_open_batches": 0, "cache_hits": 0,
                            "rejections": []}
            for p in confirmed:
                p["relevance_status"] = "unfiltered"

        # 终裁：confirmed 优先占满目标配额，unknown 只用剩余额度补位
        final = dual_track_trim(confirmed, target, now_year)
        if len(final) < target:
            final.extend(dual_track_trim(unknown, target - len(final), now_year))

        audit = {
            "engine": "target_recall_v1",
            "target": target,
            "depth": depth,
            "stop_reason": stop_reason,
            "effective_rounds": effective_rounds,
            "attempts": attempts,
            "rounds": rounds_trace,
            "classic": classic_stats,
            "planner": planner_trace,
            "excluded": excluded_count,
            "source_failures": dict(source_failures),
            "corpus_after_search": len(corpus),
            "abstract_backfill": backfill_audit,
            "filter": filter_audit,
            "relevance": {"confirmed": len(confirmed), "unknown": len(unknown),
                          "final_count": len(final)},
            "final_count": len(final),
            "keyword_stats": keyword_stats,
        }
        for p in final:
            p.setdefault("provenance", ["engine:target_recall_v1"])
        return final, audit
    finally:
        for provider in provider_state.values():
            aclose = getattr(provider, "aclose", None)
            if aclose:
                try:
                    await aclose()
                except Exception:
                    pass
