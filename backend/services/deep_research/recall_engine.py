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
- 相关性三级判定：confirmed（摘要级显式接受）/ title_confirmed（标题级显式接受）/
  unknown（未确认或筛选失败待判）；unknown 保留但不挤占 confirmed 的目标配额；
- 超量候选按问题主题匹配裁剪，保留少量被引经典/近年论文作多样性配额，
  避免在内容筛选之前仅凭引用量丢弃低引关键论文。

引擎依赖全部可注入（search_one / llm_json / backfill_abstracts），便于单测离线运行。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from collections import defaultdict
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# 深度档位 → 目标召回量（确认相关论文的规模，同时是累计语料的硬顶：
# 轮中达标立即停发，只允许最后一个在发关键词的小幅超出）
RECALL_TARGETS: dict[str, int] = {"lite": 50, "quick": 200, "standard": 500, "deep": 1000}
# 摘要/标题批量筛选每批篇数（标准/深挖用大批省指令开销；快览曾用 5，
# 但 200 目标 ÷ 5 = 40 次筛选调用是快览耗时的主要来源，10 与标准档对齐）
FILTER_BATCH: dict[str, int] = {"lite": 10, "quick": 10, "standard": 10, "deep": 10}
# 筛选批次的并发 worker 数：串行时 40 批 × 单次调用延迟线性叠加（实测快览
# 档仅筛选就 5-8 分钟）；4 路 worker 把它压到 ~1/4。asyncio 单线程事件循环里
# 共享计数器/审计 dict 的读-改-写之间没有 await，天然原子，无需锁。
FILTER_CONCURRENCY = 4
# 单轮内多个关键词的发射并发：串行关键词 × Scopus 自适应限速退避是召回阶段的
# 主要墙钟来源；Scopus 侧有 AdaptiveThrottle（内部并发 4）兜底，外部 3 路安全
KEYWORD_CONCURRENCY = 3
# 单源单检索式的取回上限（沿用旧档位 per_query）
PER_SOURCE_LIMIT: dict[str, int] = {"lite": 15, "quick": 25, "standard": 40, "deep": 50}

MAX_ROUNDS = 5               # 非零命中轮的上限（零命中轮不计数）
MAX_KEYWORD_ATTEMPTS = 15    # 绝对关键词尝试上限（含零命中），防坏词死循环
MAX_KEYWORDS_PER_ROUND = 4   # 规划员每轮最多给出的新检索词数
CLASSIC_ROUNDS = 2           # 被引经典通道只在最初 N 轮发射（每轮一次，不随关键词重复）
TOPUP_ROUNDS = 2             # 筛选后确认量不足时的补搜轮上限
# 单源单次检索的总预算（含内部重试/限速等待）。兜底任何源把协程挂死在不可中断的
# 等待里（如 429 Retry-After 巨值、代理黑洞连接）——超时按该源失败记账，不拖垮整轮。
SEARCH_ONE_BUDGET_SECONDS = 120.0

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


def relevance_trim(papers: list[dict], keep: int, question: str,
                   queries: list[str], now_year: int) -> list[dict]:
    """Bound candidates by topic while reserving source order and citation diversity."""
    if keep <= 0:
        return []
    if len(papers) <= keep:
        return list(papers)
    from .literature_retrieval import lexical_scores

    texts = [f"{p.get('title') or ''} {(p.get('abstract') or '')[:1200]}" for p in papers]
    scores = lexical_scores(texts, " ".join([question, *queries]))
    ranked = sorted(range(len(papers)), key=lambda i: (-float(scores[i]), i))
    citation_reserve = max(1, keep // 10)
    source_reserve = max(1, keep // 10)
    chosen = {p["key"]: p for p in papers[:source_reserve]}
    for paper in dual_track_trim(papers, citation_reserve, now_year):
        chosen.setdefault(paper["key"], paper)
    for i in ranked:
        if len(chosen) >= keep:
            break
        chosen.setdefault(papers[i]["key"], papers[i])
    return list(chosen.values())[:keep]


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
                    keyword_stats: list[dict],
                    coverage: list[dict] | None = None) -> tuple[str, str]:
    by_year: dict[str, int] = defaultdict(int)
    for p in corpus:
        by_year[str(p.get("year") or "unknown")] += 1
    recent_titles = [p.get("title") or "" for p in
                     sorted(corpus, key=lambda p: -(p.get("citation_count") or 0))[:20]]
    coverage = coverage or []
    uncovered = [c["dimension"] for c in coverage if not c.get("covered")]
    system = (
        "你是学术检索规划员。任务：查看当前召回语料的覆盖情况，决定下一轮检索关键词。"
        "只输出一个 JSON 对象，顶层键为 next_keywords、done、reason，键名必须完全一致：\n"
        "- next_keywords：字符串数组，最多 4 条，每条是纯英文学术关键词串"
        "（空格分隔，无引号、无布尔算符、无中文），不得与已试关键词重复；\n"
        "- done：布尔值，语料已达目标且覆盖维度均有证据、或没有值得尝试的新方向时为 true；\n"
        "- reason：一句话决策依据。\n"
        "规则：优先补覆盖缺口——未覆盖的证据维度（如缺失的任务、缺失的对比指标、"
        "缺失的年份段、缺失的技术路线与同义词）优先于数量；"
        "领域公认的高被引奠基工作（如综述必引的经典）缺席时优先补其名称检索；"
        "已试关键词零命中说明该表述在数据库中不存在，必须换学界同义表述而不是原样重试。"
        "user 载荷里的 uncovered_dimensions 非空时，next_keywords 应至少一半瞄准这些维度。"
    )
    import json

    # system 保持逐字节恒定（轮间前缀缓存共享）；逐轮变化的未覆盖维度
    # 放在 user 载荷末尾（易变区殿后），不拼进 system。
    user = {
        "research_question": question,
        "target_corpus_size": target,
        "by_year": dict(sorted(by_year.items())),
        "tried_keywords": [{"keyword": k["keyword"], "hits": k["raw_hits"]} for k in keyword_stats],
        "coverage_checklist": coverage or [],
        "top_titles_sample": recent_titles,
        "current_corpus_size": len(corpus),
        "uncovered_dimensions": uncovered,
    }
    return system, json.dumps(user, ensure_ascii=False)


_FILTER_SYSTEM = (
    "你是文献召回筛选员。逐篇依据标题和摘要判断其能否回答研究问题。"
    "明确相关的论文列入 accepted；明显无关或只有字面重叠的列入 rejected；"
    "信息不足的列入 uncertain。每篇必须恰好出现一次，不可因为未列入 rejected 就默认相关。"
    '只输出 JSON：{"accepted":["论文key"],"rejected":[{"key":"论文key","reason":"原因"}],'
    '"uncertain":["论文key"]}。key 必须原样取自 papers。'
)

_TITLE_FILTER_SYSTEM = (
    "你是文献召回筛选员。候选只有标题，没有摘要。逐篇判断：标题明确相关列入 accepted；"
    "明显跨域或无关列入 rejected；其余列入 uncertain。每篇必须恰好出现一次。"
    '只输出 JSON：{"accepted":["论文key"],"rejected":[{"key":"论文key","reason":"原因"}],'
    '"uncertain":["论文key"]}。key 必须原样取自 papers。'
)


def _chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


# 问题 → 证据覆盖维度的双语映射（程序化推导，不加 LLM 调用）：
# 研究问题中命中左侧任一词，就要求语料在右侧维度上有确认证据
_COVERAGE_DIMENSIONS: list[tuple[tuple[str, ...], str, tuple[str, ...]]] = [
    (("图像分类", "分类", "classification"), "图像分类任务证据",
     ("classification", "imagenet", "top-1", "accura")),
    (("目标检测", "检测", "detection"), "目标检测任务证据",
     ("detection", "coco", "yolo", "mAP".lower(), "anchor")),
    (("语义分割", "分割", "segmentation"), "语义分割任务证据",
     ("segmentation", "seg", "ade20k", "cityscapes", "miou")),
    (("视频", "video", "时序"), "视频/时序任务证据",
     ("video", "temporal", "tracking", "flow")),
    (("医学", "医疗", "medical"), "医学影像应用证据",
     ("medical", "clinic", "clinical", "segmentation", "diagnos")),
    (("遥感", "remote sensing"), "遥感应用证据",
     ("remote sensing", "hyperspectral", "satellite", "aerial")),
    (("参数量", "参数", "parameter"), "参数量对比证据",
     ("parameter", "params", "#p", "million")),
    (("推理速度", "速度", "throughput", "speed", "延迟", "latency"), "推理速度/延迟证据",
     ("throughput", "fps", "latency", "speed", "ms per")),
    (("显存", "内存占用", "memory"), "显存/内存占用证据",
     ("memory", "gpu hours", "footprint", "vram")),
    (("计算复杂度", "复杂度", "complexity", "flops"), "计算复杂度证据",
     ("complexity", "flops", "gflops", "linear")),
    (("引用", "被引", "citation"), "引用影响力证据",
     ("citation", "cited", "impact")),
    (("硬件", "hardware", "实际部署", "gpu"), "真实硬件实测证据",
     ("hardware", "a100", "rtx", "gpu benchmark", "deployment")),
    # 综述问题的结构化覆盖（run6 审查：没有数据集/基准与演进维度的召回，
    # 报告只能堆分类体系，出不了方法—数据集—指标的对照）
    (("综述", "survey", "review", "现状", "全景", "landscape", "领域"), "综述与演进证据",
     ("survey", "review", "overview", "taxonomy")),
    (("数据集", "dataset", "基准", "benchmark"), "基准/数据集证据",
     ("benchmark", "dataset", "mvtec", "coco", "imagenet", "visA", "open access dataset")),
]


def _topic_probes(question: str, queries: list[str] | None = None) -> list[str]:
    """Derive domain terms from the actual question/decomposition, not a fixed field map."""
    stop = {
        "about", "analysis", "and", "are", "compare", "comparison", "does", "for",
        "from", "how", "research", "the", "this", "using", "what", "which", "with",
    }
    candidates = [*(queries or []), question]
    out: list[str] = []
    for value in candidates:
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_.+-]{2,}", str(value)):
            folded = token.casefold()
            if folded in stop or folded in {item.casefold() for item in out}:
                continue
            out.append(token)
            if len(out) >= 8:
                return out
    return out


def _coverage_dimensions(question: str, queries: list[str] | None = None) -> list[dict]:
    """从问题与实际检索式推导证据覆盖维度。"""
    lowered = question.lower()
    out: list[dict] = []
    topic = _topic_probes(question, queries)
    if topic:
        out.append({"dimension": f"研究主题相关性：{' / '.join(topic[:4])}", "probes": topic})
    for triggers, name, probes in _COVERAGE_DIMENSIONS:
        if any(t.lower() in lowered for t in triggers):
            out.append({"dimension": name, "probes": list(probes)})
    return out


def _coverage_status(papers: list[dict], dimensions: list[dict]) -> list[dict]:
    """逐维度统计确认语料中的证据命中（标题+摘要，大小写不敏感）。"""
    paper_texts = [f"{p.get('title') or ''} {(p.get('abstract') or '')[:600]}".lower()
                   for p in papers]
    out: list[dict] = []
    for dim in dimensions:
        threshold = max(1, len(dim["probes"]) // 3)
        matching_papers = sum(
            sum(1 for probe in dim["probes"] if probe.lower() in body) >= threshold
            for body in paper_texts)
        out.append({"dimension": dim["dimension"], "probe_hits": matching_papers,
                    "covered": matching_papers > 0})
    return out


async def _filter_pool(papers: list[dict], question: str, *, batch_size: int,
                       llm_json: LlmJson, verdict_cache: dict[str, str],
                       should_pause=None, on_progress=None) -> tuple[list[dict], dict]:
    """三级相关性筛选：摘要级（confirmed）/ 标题级（title_confirmed）/ 待判（unknown）。

    只有显式接受才算确认；未提及或调用失败标 unknown 保留待复筛。
    verdict_cache 按 key 复用判定，补搜时只筛新增论文。
    并发模型：FILTER_CONCURRENCY 个 worker 从批次队列领活。暂停在**领新批次时**
    观察——在飞的 ≤4 批跑完并写缓存（判定不浪费），未领的批次留给续跑。
    on_progress(done, total) 在每批完成后回报——筛选是召回阶段最耗时的一段，
    没有进度输出时前端会误以为卡死。
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
    screened = audit["cache_hits"]

    async def _report() -> None:
        if on_progress is not None:
            try:
                await on_progress(min(screened, len(papers)), len(papers))
            except Exception:
                pass

    await _report()

    # 统一工作队列：摘要级批次在前、标题级在后（两类无依赖，混跑也安全）
    work: list[tuple[list[dict], str, str]] = [
        *(  (chunk, _FILTER_SYSTEM, "confirmed") for chunk in _chunks(with_abstract, batch_size)),
        *((chunk, _TITLE_FILTER_SYSTEM, "title_confirmed") for chunk in _chunks(title_only, batch_size)),
    ]
    paused = False

    async def _worker() -> None:
        nonlocal screened, paused
        while work:
            if should_pause is not None:
                flag = should_pause()
                if inspect.isawaitable(flag):
                    flag = await flag
                if flag:
                    paused = True
                    return
            chunk, system, passed_status = work.pop(0)
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
                if not isinstance(data, dict) or not isinstance(data.get("accepted"), list):
                    raise ValueError("relevance filter response missing accepted list")
                rejected = data.get("rejected") if isinstance(data.get("rejected"), list) else []
                accepted_keys = {str(item) for item in data["accepted"] if isinstance(item, str)}
                reject_keys: set[str] = set()
                for item in rejected:
                    key = str(item.get("key")) if isinstance(item, dict) else ""
                    if key in by_key:
                        reject_keys.add(key)
                        audit["rejections"].append({"key": key,
                                                    "reason": str(item.get("reason") or "")[:80]})
                for p in chunk:
                    verdict_cache[p["key"]] = (
                        "rejected" if p["key"] in reject_keys else
                        passed_status if p["key"] in accepted_keys else "unknown")
            except Exception as exc:
                logger.warning("relevance filter batch failed (fail-open as unknown): %s", exc)
                for p in chunk:
                    verdict_cache[p["key"]] = "unknown"
                audit["fail_open_batches"] += 1
            screened += len(chunk)
            await _report()

    await asyncio.gather(*(_worker() for _ in range(
        max(1, min(FILTER_CONCURRENCY, len(work))))))
    if paused:
        from .stage_agent import StagePausedError
        raise StagePausedError("recall paused between filter batches")

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
    should_pause=None,
    protocol_dimensions: list[dict] | None = None,
    sources: tuple[str, ...] = ("arxiv", "scopus", "openalex"),
) -> tuple[list[dict], dict]:
    """执行目标量驱动的多源并行召回，返回（论文清单, 审计）。

    search_one(source, keyword, limit, order) / llm_json(system, user) /
    backfill_abstracts(dois) 均可注入，缺省接真实三源与统一 LLM Core。
    should_pause 在每轮边界观察暂停请求，命中则抛 StagePausedError（协作式安全点）。
    protocol_dimensions：研究协议阶段产出的六类覆盖目标（[{dimension, probes}]），
    并入程序化推导的覆盖维度，驱动补搜缺口检查。
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

        # 相关性筛选是逐篇二分类，不需要推理链：思考模式下单批 1500 token 上限常被
        # 推理吃满、答案截断后整批 fail-open。130 篇对照实测关闭思考后输出 token
        # -77%、耗时 181→34 s，与原判定一致率 116/124（分歧全部落在 uncertain）。
        async def screen_json(system: str, user: str) -> dict:
            return await _dj_json(cfg, system, user, max_tokens=1500, thinking_effort="off")
    else:
        screen_json = llm_json

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
    coverage_trace: list[dict] = []
    coverage_dimensions = _coverage_dimensions(question, queries)
    for dim in protocol_dimensions or []:
        name = str((dim or {}).get("dimension") or "").strip()
        probes = [str(p) for p in (dim or {}).get("probes") or [] if str(p).strip()]
        if name and probes and name not in {d["dimension"] for d in coverage_dimensions}:
            coverage_dimensions.append({"dimension": name, "probes": probes[:8]})
    stop_reason = "planner_unavailable"

    def _fresh_corpus() -> list[dict]:
        """去重后立即应用排除规则：被排除论文不进入任何模型输入与统计。"""
        nonlocal excluded_count
        corpus = _dedupe_candidates(list(all_candidates))
        corpus, removed_info = apply_exclusions(corpus, active_exclusions.get())
        excluded_count = int((removed_info or {}).get("count") or 0)
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

    async def _search_keyword(kw: str, *, limit: int | None = None) -> dict:
        """单关键词三源并行（relevance）；源错误单独记账，不吞成零命中。

        limit 可按剩余目标量收紧（临近达标时少取），避免一轮冲过目标数百篇。
        """
        nonlocal attempts
        attempts += 1
        fetch = max(1, min(limit or per_source, per_source))

        async def _budgeted(src: str) -> list:
            return await asyncio.wait_for(
                search_one(src, kw, fetch, "relevance"), timeout=SEARCH_ONE_BUDGET_SECONDS)

        results = await asyncio.gather(
            *(_budgeted(src) for src in sources),
            return_exceptions=True)
        raw: list = []
        per_source_hits: dict[str, int] = {}
        per_source_errors: dict[str, str] = {}
        for src, res in zip(sources, results):
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
        async def _budgeted(src: str) -> list:
            return await asyncio.wait_for(
                search_one(src, anchor, max(12, per_source // 2), "cited"),
                timeout=SEARCH_ONE_BUDGET_SECONDS)

        # arXiv 不支持按被引排序，不参与经典通道
        cited_sources = tuple(src for src in sources if src != "arxiv")
        results = await asyncio.gather(
            *(_budgeted(src) for src in cited_sources),
            return_exceptions=True)
        row = {"round": round_no, "keyword": anchor, "order": "cited",
               "sources": {}, "source_errors": {}}
        for src, res in zip(cited_sources, results):
            if isinstance(res, BaseException):
                row["sources"][src] = 0
                row["source_errors"][src] = type(res).__name__
                source_failures[f"{src}:{type(res).__name__}"] += 1
                continue
            row["sources"][src] = len(res)
            all_candidates.extend(res)
        classic_stats.append(row)

    async def _planner(corpus: list[dict]) -> dict:
        """规划员调用；返回归一化后的 dict（非 dict/缺字段不致命）。

        覆盖维度随确认语料状态动态更新：缺什么补什么优先于数量达标。
        """
        coverage = _coverage_status(corpus, coverage_dimensions) if coverage_dimensions else []
        coverage_trace.append({"corpus": len(corpus), "checklist": coverage})
        system, user = _planner_prompt(question, target=target, corpus=corpus,
                                       keyword_stats=keyword_stats, coverage=coverage)
        try:
            plan = await llm_json(system, user)
        except Exception as exc:
            logger.warning("recall planner failed, stop expanding: %s", exc)
            return {"__error__": f"{type(exc).__name__}"}
        return plan if isinstance(plan, dict) else {}

    async def _fire_keywords(keywords: list[str]) -> list[dict]:
        """轮内关键词并发发射（KEYWORD_CONCURRENCY 路信号量）。

        串行关键词 × Scopus AdaptiveThrottle 退避曾把单轮拖到数分钟（v7.4.1 实测
        召回 26 分钟里 ~14 分钟是无 LLM 的串行网络等待）；并发后 arXiv/OpenAlex
        在 Scopus 退避时照常推进。目标量与尝试预算在**领词时**检查：并发下允许
        小幅超出目标（≤ 在飞关键词数 × 单源上限），后续 dual_track_trim 收口。
        """
        sem = asyncio.Semaphore(KEYWORD_CONCURRENCY)

        async def _bounded(kw: str) -> dict | None:
            async with sem:
                if attempts >= MAX_KEYWORD_ATTEMPTS:
                    return None
                remaining = target - len(_fresh_corpus())
                if remaining <= 0:
                    return None
                return await _search_keyword(kw, limit=max(10, min(per_source, remaining)))

        results = await asyncio.gather(*(_bounded(kw) for kw in keywords))
        return [stat for stat in results if stat is not None]

    current_keywords = _next_keywords(list(queries))
    if not current_keywords:
        current_keywords = _usable_english_queries([str(q) for q in queries])[:MAX_KEYWORDS_PER_ROUND]
    tried.update(k.lower() for k in current_keywords)

    try:
        while current_keywords:
            if should_pause is not None:
                flag = should_pause()
                if inspect.isawaitable(flag):
                    flag = await flag
                if flag:
                    from .stage_agent import StagePausedError
                    raise StagePausedError(f"recall paused at round {len(rounds_trace) + 1}")
            round_no = len(rounds_trace) + 1
            round_stats = await _fire_keywords(current_keywords)
            if round_no <= CLASSIC_ROUNDS and round_stats and len(_fresh_corpus()) < target:
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
        pool = relevance_trim(corpus, min(len(corpus), int(target * 1.3)),
                              question, queries, now_year)
        supplement_budget_stop = ""
        if has_llm and llm_json is not None and pool:
            kept, filter_audit = await _filter_pool(
                pool, question, batch_size=batch_size, llm_json=screen_json,
                verdict_cache=verdict_cache, should_pause=should_pause,
                on_progress=lambda done, total: _emit(
                    {"phase": "filter", "done": done, "total": total}))
            confirmed = [p for p in kept if p.get("relevance_status") != "unknown"]
            unknown = [p for p in kept if p.get("relevance_status") == "unknown"]

            topups = 0
            # supplement_batches 消费点：每个补搜批次预占一档额度（记账模式
            # 只累计用量；enforce 耗尽即停——lite 预设 0 表示不做覆盖缺口补搜）
            from ..llm import llm_budget as _llm_budget_ctx
            _sup_budget = _llm_budget_ctx()

            def _uncovered(papers: list[dict]) -> list[str]:
                if not coverage_dimensions:
                    return []
                return [c["dimension"] for c in _coverage_status(papers, coverage_dimensions)
                        if not c["covered"]]

            # 确认量不足 **或覆盖维度仍有缺口** 都触发预算内补搜（缺什么补什么）
            while ((len(confirmed) < target or _uncovered(confirmed)) and topups < TOPUP_ROUNDS
                   and attempts < MAX_KEYWORD_ATTEMPTS and effective_rounds < MAX_ROUNDS
                   and stop_reason != "planner_error"):
                # 注意：搜索循环的 planner_stop 不阻止补搜——规划员的 done 针对
                # 原始候选量，质量筛选/覆盖检查可能推翻它（筛后确认量不足就要补）
                if _sup_budget is not None and hasattr(_sup_budget, "reserve"):
                    if not _sup_budget.reserve("supplement_batches", 1.0):
                        supplement_budget_stop = "supplement_budget_exhausted"
                        break
                    _sup_budget.settle("supplement_batches", 1.0, 1.0)
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
                round_stats = await _fire_keywords(plan_keywords)
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
                new_pool = relevance_trim(new_pool, min(len(new_pool), target),
                                          question, [*queries, *plan_keywords], now_year)
                if new_pool:
                    new_kept, new_audit = await _filter_pool(
                        new_pool, question, batch_size=batch_size, llm_json=screen_json,
                        verdict_cache=verdict_cache, should_pause=should_pause)
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

        # 终裁：confirmed 优先占满目标配额；unknown 不再补位填满 target——
        # fail-open 批次的未决论文只随行一个有界数量（screen 阶段重试裁决的
        # 对象），隔离后不进入计量/聚类/综合/选材。宁可语料少于 target 也不
        # 让未裁决论文污染下游（run6 教训：6 篇 unknown 综述凭高被引进入
        # top_cited 与聚类，计量图被跨领域论文整体带偏）。
        final = relevance_trim(confirmed, target, question, queries, now_year)
        escort_cap = max(10, int(target * 0.2))
        escorted_unknown = relevance_trim(
            unknown, min(len(unknown), escort_cap), question, queries, now_year) if unknown else []
        final.extend(escorted_unknown)

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
            "coverage_dimensions": [d["dimension"] for d in coverage_dimensions],
            "coverage_trace": coverage_trace,
            "excluded": excluded_count,
            "source_failures": dict(source_failures),
            "corpus_after_search": len(corpus),
            "abstract_backfill": backfill_audit,
            "filter": filter_audit,
            "relevance": {"confirmed": len(confirmed), "unknown": len(unknown),
                          "escorted_unknown": len(escorted_unknown),
                          "unknown_pending_screen": max(0, len(unknown) - len(escorted_unknown)),
                          "final_count": len(final)},
            "final_count": len(final),
            "keyword_stats": keyword_stats,
        }
        if supplement_budget_stop:
            audit["supplement_budget_stop"] = supplement_budget_stop
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
