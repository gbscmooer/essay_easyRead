"""
Domain Explorer — 问句驱动的领域探索工作流（GOAL.md 核心命题的直接实现）

学者提一个自然语言问题（如「用 AI 识别肿瘤有没有人做，做得怎样」），系统产出
免读单篇论文的领域认知。与 pipeline.py（工作区种子扩展）互补，不需要任何种子：

    decompose  LLM 把口语问题拆成英文学术检索式 + 问句意图（无 Key 回退原句直检）
    recall     provider.search_works 各式**并行**检索（Scopus 首选 / OpenAlex 熔断回退），
               带学术类型过滤排干扰，去重合并
    enrich     指标富化（非致命）：OpenAlex 按 DOI 补逐年引用/摘要/关键词（Scopus 检索
               不带、Citation API 又无权限），Scopus Serial Title 批量查期刊 SJR/SNIP
    metrics    纯算法计量：被引分布、引用速度、近 2 年增量与轨迹、突增、**同年百分位**
               （ESI 高被引的语料内复刻）、年份直方图、主阵地期刊、关键词×年份矩阵
    cluster    标题+摘要的 TF-IDF 词法相似图 → Louvain 社区 → 子方向标签（复用 graph_analytics）
    synthesize LLM 只基于召回材料：查新判定 / 成熟度 / 流派 / 热点 / 空白 / Markdown 报告
    persist    产物整体落 ExplorationRun.result

客观性纪律（GOAL.md 铁律）：所有「现状/热点」的底层数字由本模块的算法算出，
LLM 只把数字讲成人话；召回为空时如实报告「检索未命中」，不用模型记忆填补。
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Optional

import networkx as nx
from sqlmodel import Session

from ...models.exploration import (
    NOVELTY_STATUSES, ExplorationRun, ExplorationSynthesis, ExplorationVerdict,
)
from ...models.research import ParadigmInfo
from ...models.schemas import StreamEvent
from ..database import get_engine
from .event_bus import EventBus
from .graph_analytics import _detect_communities, _label_clusters, _merge_small, _tokenize

logger = logging.getLogger(__name__)

# ── 深度档位：控制检索式数量 / 单式召回上限 / 总量上限 / 送入 LLM 的论文数 ────
DEPTH_PRESETS: dict[str, dict] = {
    "quick": {"max_queries": 3, "per_query": 25, "cap": 80, "llm_papers": 20},
    "standard": {"max_queries": 4, "per_query": 40, "cap": 160, "llm_papers": 30},
    "deep": {"max_queries": 5, "per_query": 50, "cap": 240, "llm_papers": 40},
}

# 引用速度（被引/年龄）达到该值且发表在近窗口内 → 突增候选
_SURGE_MIN_VELOCITY = 2.0
_RECENT_WINDOW = 2          # 「近期」= 最近 N 年（相对当前年份）
_ABSTRACT_CLIP = 500        # 送入 LLM 的摘要截断
_MIN_SYNTH_PAPERS = 3       # 证据少于该数不做 LLM 综合（材料不足，防编造）


# ── 独立事件总线注册表（与 research run 的 id 空间隔离，避免键冲突）──────────

_explore_buses: dict[int, EventBus] = {}


def get_exploration_bus(run_id: int) -> EventBus:
    if run_id not in _explore_buses:
        _explore_buses[run_id] = EventBus()
    return _explore_buses[run_id]


def has_exploration_bus(run_id: int) -> bool:
    return run_id in _explore_buses


def drop_exploration_bus(run_id: int) -> None:
    _explore_buses.pop(run_id, None)


# ── Stage 1: decompose ────────────────────────────────────────────────────────

_INTENT_VALUES = ("novelty", "maturity", "landscape")

# 检索式消毒时剔除的 token：字段标签与布尔算符。模型偶尔无视「纯关键词串」的
# 指令，夹带 TITLE-ABS-KEY((...) OR ...) 这类布尔语法——Scopus 侧会二次包裹、
# OpenAlex 侧语义错乱，必须在归一化时剥干净。
_QUERY_STOP_TOKENS = {"title-abs-key", "title", "abs", "key", "all", "and", "or", "not"}


def _sanitize_query(q: str) -> str:
    """剥离引号/括号/字段标签/布尔算符，归一为空格分隔的纯关键词串。"""
    q = str(q).replace('"', " ").replace("(", " ").replace(")", " ")
    toks = [t for t in re.split(r"[\s,]+", q) if t and t.lower() not in _QUERY_STOP_TOKENS]
    return " ".join(toks)

_DECOMPOSE_SYSTEM = (
    "你是学术检索专家。把学者的口语化研究问题拆解成英文学术数据库检索式。"
    "只输出 JSON，不要输出任何其他文字。"
)


def _decompose_user(question: str, max_queries: int) -> str:
    return "\n".join([
        f"## 研究问题\n{question}",
        "",
        "## 输出格式",
        "```json",
        '{"intent": "novelty|maturity|landscape（问句意图：查新=有没有人做；成熟度=做到什么程度；全景=领域现状）",',
        f' "en_queries": ["英文检索式1", "英文检索式2", ...]（{max_queries} 条左右，覆盖领域词+任务词+方法词+同义词，'
        "每条是空格分隔的关键词串，不要引号和布尔算符),",
        ' "focus_terms": ["中文关键术语1", "中文关键术语2"]}',
        "```",
        "要求：检索式面向 Scopus/OpenAlex 的 TITLE-ABS-KEY 语义；问题里的中文概念必须译成学界通用英文术语"
        "（如「识别肿瘤」→ tumor detection / cancer diagnosis）；同义词多给几条提高查全。",
    ])


def _normalize_decompose(data: dict, question: str, max_queries: int) -> dict:
    queries = []
    for q in data.get("en_queries") or []:
        q = _sanitize_query(q)
        if len(q) >= 3 and q not in queries:
            queries.append(q)
    if not queries:
        queries = [_sanitize_query(question)] or [question.strip()]
    intent = str(data.get("intent") or "").strip()
    if intent not in _INTENT_VALUES:
        intent = "landscape"
    focus = [str(t).strip() for t in (data.get("focus_terms") or []) if str(t).strip()][:6]
    return {"intent": intent, "en_queries": queries[:max_queries], "focus_terms": focus, "fallback": False}


async def decompose_question(question: str, llm_cfg: Optional[dict], *, max_queries: int) -> dict:
    """LLM 拆解检索式；无 Key / 调用失败 → 原句直检的回退拆解（OpenAlex 支持自然语言搜索）。"""
    if llm_cfg and llm_cfg.get("api_key"):
        try:
            from .domain_synthesizer import _llm_json
            data = await _llm_json(
                llm_cfg, _DECOMPOSE_SYSTEM, _decompose_user(question, max_queries),
                # 推理型模型的 hidden reasoning 与 content 共享 max_tokens 且长度方差极大
                # （实测 227~3075），预算不足时 content 直接被截空 → 必须给足余量
                max_tokens=4000,
            )
            return _normalize_decompose(data, question, max_queries)
        except Exception as e:
            logger.warning(f"explorer decompose failed (fallback to raw question): {e}")
            return {"intent": "landscape", "en_queries": [_sanitize_query(question)] or [question.strip()],
                    "focus_terms": [], "fallback": True, "fallback_reason": f"{type(e).__name__}: {e}"[:300]}
    return {"intent": "landscape", "en_queries": [_sanitize_query(question)] or [question.strip()],
            "focus_terms": [], "fallback": True}


# ── Stage 2: recall ───────────────────────────────────────────────────────────

def _dedupe_candidates(candidates: list) -> list[dict]:
    """按 canonical_key（缺省退化为规范化标题）去重；同一篇保留信息最全的一条。"""
    from ..paper_identity import normalize_title

    by_key: dict[str, dict] = {}
    for cand in candidates:
        title = (getattr(cand, "title", "") or "").strip()
        if not title:
            continue
        key = getattr(cand, "canonical_key", None) or f"t:{normalize_title(title)}"
        cur = by_key.get(key)
        if cur is None:
            cur = {
                "key": key,
                "title": title,
                "year": getattr(cand, "year", None),
                "citation_count": getattr(cand, "citation_count", None) or 0,
                "doi": getattr(cand, "doi", None),
                "arxiv_id": getattr(cand, "arxiv_id", None),
                "authors": getattr(cand, "authors", None),
                "abstract": getattr(cand, "abstract", None),
                "source_query": getattr(cand, "seed_relation", None) or "",
            }
            # extra（venue 身份 / counts_by_year / keywords 等指标富化元数据）展平进论文 dict
            for k, v in (getattr(cand, "extra", None) or {}).items():
                if v:
                    cur[k] = v
            by_key[key] = cur
            continue
        # 合并：字段缺的那边补上；被引数取大（不同源统计口径略有差异）
        for field in ("year", "doi", "arxiv_id", "authors", "abstract"):
            if not cur.get(field) and getattr(cand, field, None):
                cur[field] = getattr(cand, field)
        for k, v in (getattr(cand, "extra", None) or {}).items():
            if v and not cur.get(k):
                cur[k] = v
        cur["citation_count"] = max(cur["citation_count"], getattr(cand, "citation_count", None) or 0)
    return list(by_key.values())


def _cap_papers(papers: list[dict], *, cap: int, now_year: int, recent_window: int = 3) -> list[dict]:
    """超量时的裁剪：主体给高被引（证据骨干），预留 1/4 席位给近三年的新工作（新兴热点）。"""
    if len(papers) <= cap:
        return papers
    recent_slots = max(1, cap // 4)
    main = sorted(papers, key=lambda p: p.get("citation_count") or 0, reverse=True)[: cap - recent_slots]
    chosen = {p["key"] for p in main}
    recent = sorted(
        (p for p in papers if p["key"] not in chosen and (p.get("year") or 0) >= now_year - recent_window),
        key=lambda p: (p.get("year") or 0, p.get("citation_count") or 0),
        reverse=True,
    )[:recent_slots]
    seen: set[str] = set()
    out = []
    for p in main + recent:
        if p["key"] not in seen:
            seen.add(p["key"])
            out.append(p)
    return out


async def recall_papers(
    queries: list[str], *, per_query: int, cap: int, now_year: int,
    progress=None,
) -> tuple[list[dict], str]:
    """各检索式**并行**调用 provider.search_works（Scopus 自适应限速下安全并行），
    返回（去重后的论文清单, provider 名）。academic_only 打开学术类型过滤排干扰。"""
    from .providers import get_provider
    from ..ai_config import get_research_config

    provider = get_provider(dict(get_research_config()))
    if provider.name == "local":
        return [], provider.name

    async def _one(q: str) -> list:
        try:
            return await provider.search_works(q, limit=per_query, academic_only=True)
        except Exception as e:
            logger.warning(f"explorer recall query '{q}' failed: {e}")
            return []

    # 并行发射：付费源自适应限速会在 429 时自动收敛，免费源自身有串行节流
    batches = await asyncio.gather(*[_one(q) for q in queries])

    # 首选源在并发下熔断时（如无效 Key 连续 401），早到的查询可能已落在死掉的主源上
    # 拿到空结果——只对落空的检索式改走备用源重试，部分丢失也找得回来
    from .providers import FailoverProvider
    if isinstance(provider, FailoverProvider) and getattr(
        getattr(provider, "_primary", None), "_disabled", False
    ):
        dead_idx = [i for i, hits in enumerate(batches) if not hits]
        if dead_idx:
            logger.warning(
                "explorer recall: 首选源熔断，%d/%d 条检索式落空，改走备用源重试",
                len(dead_idx), len(queries),
            )
            retried = await asyncio.gather(*[_one(queries[i]) for i in dead_idx])
            for i, hits in zip(dead_idx, retried):
                batches[i] = hits

    candidates = []
    for q, hits in zip(queries, batches):
        candidates.extend(hits)
        if progress:
            try:
                await progress(query=q, hit_count=len(hits), total_so_far=len(candidates))
            except Exception:
                pass

    papers = _dedupe_candidates(candidates)
    papers = _cap_papers(papers, cap=cap, now_year=now_year)
    aclose = getattr(provider, "aclose", None)
    if aclose:
        try:
            await aclose()
        except Exception:
            pass
    return papers, provider.name


# ── Stage 3: enrich（指标富化：逐年引用 / 摘要 / 关键词 / 期刊指标）──────────
#
# 设计依据（2026-09-02 权限实测，见 docs/DOMAIN_ONBOARDING_AI_WORKFLOW_PLAN.md v1.4）：
# - Scopus Search 不带摘要/关键词（field 请求返回空），Abstract Retrieval 的摘要字段
#   同样不给个人 Key → 摘要与关键词统一从 OpenAlex 按 DOI 补；
# - Scopus Citation Overview / Count API 无权限（404）→ 逐年引用曲线用 OpenAlex
#   counts_by_year（免费且引用计数含预印本，对 CS 前沿更灵敏）；
# - Serial Title API 可用 → 期刊 SJR / SNIP / CiteScore 按 source-id 批量查。
# 统计口径：总量（citation_count）用 Scopus，动态（counts_by_year）用 OpenAlex，
# 两边基数略有差异属正常（同一篇 ResNet 实测 229184 vs 227875）。

_ENRICH_CONCURRENCY = 8      # OpenAlex 匿名档 ~10 rps，8 并发留余量
_SERIAL_CHUNK = 20           # Serial Title source-id 逗号分隔分块


async def enrich_papers(papers: list[dict], *, progress=None) -> dict:
    """就地富化论文列表（全部非致命：任何一步失败只缺字段，不阻塞探索）。"""
    stats = {"history_matched": 0, "venue_metrics": 0}
    if not papers:
        return stats

    # 1) OpenAlex 按 DOI 逐篇补：逐年引用 / 摘要 / 关键词 / 期刊身份
    from .providers.openalex import get_shared_openalex_provider

    oa = get_shared_openalex_provider()
    sem = asyncio.Semaphore(_ENRICH_CONCURRENCY)
    done_count = 0

    async def _one(p: dict) -> None:
        nonlocal done_count
        doi = p.get("doi")
        if not doi or p.get("counts_by_year"):
            return
        async with sem:
            try:
                hist = await oa.get_citation_history(doi)
            except Exception as e:
                logger.debug(f"enrich history failed for {doi}: {e}")
                return
        if not hist:
            return
        p["counts_by_year"] = hist.get("counts_by_year") or []
        stats["history_matched"] += 1
        if not p.get("abstract") and hist.get("abstract"):
            p["abstract"] = hist["abstract"]
        if not p.get("keywords") and hist.get("keywords"):
            p["keywords"] = hist["keywords"]
        for field in ("venue_name", "venue_issn"):
            if not p.get(field) and hist.get(field):
                p[field] = hist[field]
        done_count += 1
        if progress and done_count % 25 == 0:
            try:
                await progress(done=done_count)
            except Exception:
                pass

    await asyncio.gather(*[_one(p) for p in papers])

    # 2) 期刊级指标：Scopus source-id 批量（Scopus 侧身份），OpenAlex-only 的期刊按 ISSN 补
    source_ids = sorted({p["venue_source_id"] for p in papers if p.get("venue_source_id")})
    issns = sorted({
        p["venue_issn"] for p in papers
        if p.get("venue_issn") and not p.get("venue_source_id")
    })
    serial_map: dict[str, dict] = {}
    if source_ids or issns:
        from ..ai_config import get_research_config
        from .providers import _elsevier_key
        from .providers.scopus import ScopusProvider

        key = _elsevier_key(dict(get_research_config()))
        if key:
            scopus = ScopusProvider(api_key=key)
            try:
                serial_map.update(await scopus.get_serial_metrics(source_ids))
                for i in range(0, len(issns), _SERIAL_CHUNK):
                    chunk = ",".join(issns[i:i + _SERIAL_CHUNK])
                    data = await scopus._get("/content/serial/title", {"issn": chunk})
                    entries = [e for e in (data or {}).get("serial-metadata-response", {}).get("entry") or []
                               if isinstance(e, dict)]
                    for e in entries:
                        parsed = ScopusProvider._parse_serial_entries([e])
                        m = next(iter(parsed.values()), None) or {
                            "name": e.get("dc:title") or "", "sjr": None, "snip": None, "citescore": None,
                        }
                        # ISSN 查询的条目要能被论文侧的 venue_issn 命中：prism:issn / eIssn 都登记
                        for issn_resp in (e.get("prism:issn"), e.get("prism:eIssn")):
                            if issn_resp:
                                serial_map.setdefault(str(issn_resp), m)
            except Exception as e:
                logger.warning(f"enrich serial metrics failed (non-fatal): {e}")
            finally:
                await scopus.aclose()

    for p in papers:
        vid = p.get("venue_source_id") or p.get("venue_issn")
        m = serial_map.get(str(vid) if vid else "", None)
        name = p.get("venue_name") or (m or {}).get("name") or None
        if not name and not m:
            continue
        if m:
            stats["venue_metrics"] += 1
        p["venue"] = {
            "name": name,
            "sjr": (m or {}).get("sjr"),
            "snip": (m or {}).get("snip"),
            "citescore": (m or {}).get("citescore"),
        }
    return stats


# ── Stage 4: metrics + cluster（纯算法，客观性的来源）────────────────────────

def _citation_dynamics(p: dict, *, now_year: int) -> None:
    """就地写入近 2 年被引增量 / 近 2 年速度 / 引用轨迹（缺 counts_by_year 时全空）。"""
    cby = [c for c in (p.get("counts_by_year") or []) if isinstance(c, dict)]
    if not cby:
        p["recent_citations"] = None
        p["citation_velocity_2y"] = None
        p["trajectory"] = None
        return
    # 近 2 年 = 最近两个日历年（含当年；当年未走完属口径保守）
    w2 = sum(c.get("cited_by_count") or 0 for c in cby if (c.get("year") or 0) >= now_year - 1)
    w1 = sum(c.get("cited_by_count") or 0 for c in cby if now_year - 3 <= (c.get("year") or 0) <= now_year - 2)
    p["recent_citations"] = w2
    p["citation_velocity_2y"] = round(w2 / 2, 2)
    # 引用轨迹：近 2 年均速 vs 之前 2 年均速（发表满 4 年才定型，新论文不算回落）
    year = p.get("year")
    p["trajectory"] = None
    if isinstance(year, int) and 1900 < year <= now_year - 4:
        r2, r1 = w2 / 2, w1 / 2
        if r1 <= 0:
            p["trajectory"] = "rising" if r2 >= _SURGE_MIN_VELOCITY else None
        elif r2 >= 1.5 * r1:
            p["trajectory"] = "rising"
        elif r2 <= 0.5 * r1:
            p["trajectory"] = "declining"
        else:
            p["trajectory"] = "steady"


def _cohort_percentiles(papers: list[dict]) -> None:
    """ESI「高被引」的语料内可复刻版：按**发表年份**分组算被引百分位。

    ESI 官方口径是全领域同年 top 1%；召回语料只是领域的抽样，绝对 1% 噪声太大，
    故取同年语料前 5% 且 cohort ≥5 才打 highly_cited 徽章。百分位本身就是
    年龄无关的公平指标（老论文不再天然占优）。
    """
    cohorts: dict[int, list[int]] = defaultdict(list)
    for p in papers:
        y = p.get("year")
        if isinstance(y, int) and 1900 < y:
            cohorts[y].append(p.get("citation_count") or 0)
    for p in papers:
        y = p.get("year")
        cohort = cohorts.get(y) if isinstance(y, int) else None
        if not cohort:
            p["citation_percentile"] = None
            p["highly_cited"] = False
            continue
        mine = p.get("citation_count") or 0
        less = sum(1 for c in cohort if c < mine)
        equal = sum(1 for c in cohort if c == mine)
        pr = (less + equal / 2) / len(cohort) * 100
        p["citation_percentile"] = round(pr, 1)
        p["highly_cited"] = bool(len(cohort) >= 5 and pr >= 95)


def _venue_ranking(papers: list[dict]) -> list[dict]:
    """主阵地期刊：语料内论文数 ≥2 的期刊，按篇数排序（带 SJR/SNIP）。"""
    by_venue: dict[str, dict] = {}
    for p in papers:
        v = p.get("venue") or {}
        name = (v.get("name") or "").strip()
        if not name:
            continue
        row = by_venue.setdefault(name, {"name": name, "count": 0,
                                         "sjr": v.get("sjr"), "snip": v.get("snip"),
                                         "citescore": v.get("citescore")})
        row["count"] += 1
    ranked = sorted(by_venue.values(), key=lambda r: r["count"], reverse=True)
    return [r for r in ranked if r["count"] >= 2][:8]


def _keyword_trend(papers: list[dict], *, now_year: int) -> dict:
    """关键词 × 年份矩阵（热力图原料）：Top 关键词在每年新出现的论文数。"""
    kw_years: dict[str, Counter] = defaultdict(Counter)
    years_seen: set[int] = set()
    for p in papers:
        y = p.get("year")
        if not isinstance(y, int) or y <= 1900:
            continue
        years_seen.add(y)
        for kw in p.get("keywords") or []:
            if isinstance(kw, str) and kw.strip():
                kw_years[kw.strip()][y] += 1
    if not years_seen:
        return {"years": [], "rows": []}
    years = [y for y in range(max(min(years_seen), now_year - 11), max(years_seen) + 1)]
    rows = []
    for kw, yc in kw_years.items():
        total = sum(yc.values())
        if total < 3:
            continue
        rows.append({"keyword": kw, "total": total,
                     "counts": [yc.get(y) or 0 for y in years]})
    rows.sort(key=lambda r: r["total"], reverse=True)
    return {"years": years, "rows": rows[:20]}


def compute_metrics(papers: list[dict], *, now_year: int) -> None:
    """就地写入引用速度 / 突增 / 近 2 年动态 / 同年百分位，并给出聚合统计。"""
    for p in papers:
        year, cites = p.get("year"), p.get("citation_count") or 0
        if isinstance(year, int) and 1900 < year <= now_year + 1:
            age = max(1, now_year - year)
            p["citation_velocity"] = round(cites / age, 2)
        else:
            p["citation_velocity"] = None
        _citation_dynamics(p, now_year=now_year)
        # 突增：近窗口发表 + 当前动量达标。动量优先取近 2 年速度（真·当前热度，
        # 老论文全周期速度会高估），缺逐年数据时退回全周期速度
        if p["citation_velocity"] is None:
            p["surge"] = False
        else:
            eff = p["citation_velocity_2y"]
            eff = p["citation_velocity"] if eff is None else eff
            p["surge"] = bool(
                isinstance(year, int) and year >= now_year - _RECENT_WINDOW
                and eff >= _SURGE_MIN_VELOCITY
            )
    _cohort_percentiles(papers)

    known = [p["year"] for p in papers if isinstance(p.get("year"), int)]
    by_year = dict(sorted(Counter(y for y in known if y > 1900).items()))
    recent_n = sum(1 for y in known if y >= now_year - _RECENT_WINDOW)
    top_cited = [p["key"] for p in sorted(papers, key=lambda x: x.get("citation_count") or 0, reverse=True)[:10]]
    rising = [
        p["key"]
        for p in sorted(
            (x for x in papers if x.get("citation_velocity") is not None),
            key=lambda x: x["citation_velocity"], reverse=True,
        )[:10]
    ]
    highly_cited = [
        p["key"]
        for p in sorted(
            (x for x in papers if x.get("highly_cited")),
            key=lambda x: (x.get("citation_percentile") or 0, x.get("citation_count") or 0),
            reverse=True,
        )[:10]
    ]
    return {
        "total": len(papers),
        "by_year": by_year,
        "recent_share": round(recent_n / len(known), 3) if known else 0.0,
        "year_span": [min(known), max(known)] if known else [],
        "top_cited": top_cited,
        "rising": rising,
        "highly_cited": highly_cited,
        "venue_ranking": _venue_ranking(papers),
        "keyword_trend": _keyword_trend(papers, now_year=now_year),
        "enriched": {
            "with_history": sum(1 for p in papers if p.get("counts_by_year")),
            "with_venue_metrics": sum(1 for p in papers if (p.get("venue") or {}).get("sjr") is not None),
        },
    }


def _tfidf_vectors(texts: list[str]) -> list[dict[str, float]]:
    """标题+摘要的 TF-IDF 稀疏向量（L2 归一化）。分词复用 graph_analytics（EN + 中文 bigram）。"""
    token_lists = [_tokenize(t) for t in texts]
    n = max(1, len(token_lists))
    df: Counter = Counter()
    for toks in token_lists:
        df.update(set(toks))
    vecs = []
    for toks in token_lists:
        tf = Counter(toks)
        vec = {t: c * math.log(1 + n / df[t]) for t, c in tf.items()}
        norm = math.sqrt(sum(w * w for w in vec.values())) or 1.0
        vecs.append({t: w / norm for t, w in vec.items()})
    return vecs


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(b) < len(a):
        a, b = b, a
    return sum(w * b.get(t, 0.0) for t, w in a.items())


def cluster_papers(
    papers: list[dict], *, min_sim: float = 0.08, max_neighbors: int = 8,
) -> tuple[dict[str, int], list[dict], float]:
    """词法相似图 → Louvain 社区。返回（node key → cluster（-1 孤立）, related 边, modularity）。

    与深度研究图谱共用同一套社区/标签口径（graph_analytics），前端总览视图无需区分数据来源。
    """
    if not papers:
        return {}, [], 0.0

    vecs = _tfidf_vectors([f'{p["title"]} {p.get("abstract") or ""}' for p in papers])
    sims: list[tuple[int, int, float]] = []
    for i in range(len(papers)):
        for j in range(i + 1, len(papers)):
            s = _cosine(vecs[i], vecs[j])
            if s >= min_sim:
                sims.append((i, j, s))

    # 每个节点只保留最强的 max_neighbors 条边（稀疏化，防 hub 拉扯社区结构）
    top: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for i, j, s in sims:
        top[i].append((s, j))
        top[j].append((s, i))
    keep: set[tuple[int, int]] = set()
    for i, lst in top.items():
        lst.sort(reverse=True)
        for _, j in lst[:max_neighbors]:
            keep.add((min(i, j), max(i, j)))

    rel = nx.Graph()
    rel.add_nodes_from(range(len(papers)))
    for i, j in keep:
        s = next((s for a, b, s in sims if (a, b) == (i, j)), 0.0)
        rel.add_edge(i, j, weight=round(s, 4))

    communities = _merge_small(rel, _detect_communities(rel), min_size=2)
    node2cluster: dict[int, int] = {}
    key2cluster: dict[str, int] = {}
    for cid, members in enumerate(communities):
        for idx in members:
            cluster = -1 if len(members) <= 1 else cid
            node2cluster[idx] = cluster
            key2cluster[papers[idx]["key"]] = cluster

    labels = _label_clusters({i: papers[i]["title"] for i in range(len(papers))}, communities)
    for p in papers:
        c = key2cluster.get(p["key"], -1)
        p["cluster"] = c
        p["cluster_label"] = labels.get(c, "") if c >= 0 else ""

    try:
        modularity = (
            round(float(nx.community.modularity(rel, communities, weight="weight")), 4)
            if communities and rel.number_of_edges() else 0.0
        )
    except Exception:
        modularity = 0.0

    edges = [
        {"source": papers[i]["key"], "target": papers[j]["key"], "kind": "related", "weight": round(s, 4)}
        for i, j, s in sims if (i, j) in keep
    ]
    return key2cluster, edges, modularity


def build_graph_payload(papers: list[dict], edges: list[dict], *, modularity: float) -> dict:
    """组装 ResearchGraph 形状的图谱（前端 DomainOverview 直接复用）。"""
    max_cites = max((p.get("citation_count") or 0 for p in papers), default=0) or 1
    node_ids = {p["key"]: f"x{i}" for i, p in enumerate(papers)}
    nodes = []
    for p in papers:
        meta = {
            "year": p.get("year"),
            "citation_count": p.get("citation_count") or 0,
            "cluster": p.get("cluster", -1),
            "source": "expanded",
            "importance": round((p.get("citation_count") or 0) / max_cites, 4),
        }
        if p.get("cluster_label"):
            meta["cluster_label"] = p["cluster_label"]
        if p.get("doi"):
            meta["external_id"] = p["doi"]
        nodes.append({"id": node_ids[p["key"]], "kind": "paper", "label": p["title"], "meta": meta})
    graph_edges = [
        {"source": node_ids[e["source"]], "target": node_ids[e["target"]], "kind": "related", "weight": e["weight"]}
        for e in edges
    ]
    clusters = len({p.get("cluster") for p in papers if p.get("cluster", -1) >= 0})
    return {
        "nodes": nodes,
        "edges": graph_edges,
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(graph_edges),
            "by_kind": {"related": len(graph_edges)},
            "clusters": clusters,
            "modularity": modularity,
        },
    }


# ── Stage 4: synthesize（LLM 只做综合，不做记忆）─────────────────────────────

_SYNTH_SYSTEM = (
    "你是学术情报分析师。基于给定的文献检索材料回答学者的研究问题。"
    "所有结论必须来自材料中列出的论文与统计数字，禁止编造论文或引用材料外的事实；"
    "材料不足时明确说证据不足。只输出 JSON，不要输出任何其他文字。"
)


def _clip(text: str | None, limit: int) -> str:
    t = (text or "").strip()
    return t[:limit] + ("…" if len(t) > limit else "")


def _synth_user(question: str, decompose: dict, papers: list[dict], metrics: dict, *, max_papers: int) -> tuple[str, list[dict]]:
    """材料装配。返回 (user prompt, 送入的论文子集)——子集编号 E1..En 供证据引用。"""
    ordered = sorted(papers, key=lambda x: (x.get("citation_count") or 0), reverse=True)
    subset = ordered[:max_papers]
    by_key = {p["key"]: p for p in papers}

    lines = [f"## 学者的问题\n{question}", ""]
    lines.append(
        f"## 检索概况\n意图: {decompose.get('intent', 'landscape')}；"
        f"检索式: {'; '.join(decompose.get('en_queries') or [])}；"
        f"共召回 {metrics.get('total', 0)} 篇（去重后）；"
        f"年份跨度 {metrics.get('year_span') or '未知'}；"
        f"近 {_RECENT_WINDOW} 年占比 {metrics.get('recent_share', 0):.0%}。"
    )
    venues = (metrics.get("venue_ranking") or [])[:3]
    if venues:
        lines.append(
            "主阵地期刊: " + "；".join(
                f"{v['name']}（{v['count']} 篇" + (f"，SJR {v['sjr']}" if v.get("sjr") else "") + "）"
                for v in venues
            ) + "。"
        )
    trend_rows = ((metrics.get("keyword_trend") or {}).get("rows") or [])[:6]
    if trend_rows:
        years = (metrics.get("keyword_trend") or {}).get("years") or []
        recent2 = years[-2:]
        lines.append(
            "高频关键词（近两年新论文数）: "
            + "；".join(
                f"{r['keyword']}(共{r['total']}"
                + (f"，近2年{sum(r['counts'][-2:])}" if recent2 else "") + ")"
                for r in trend_rows
            ) + "。"
        )
    if decompose.get("fallback"):
        lines.append(
            "⚠ 检索质量提示：本次未经检索式拆解（原句直检索），查全率可能显著偏低。"
            "若材料未覆盖主题，应判 unclear（检索不可靠）而非 gap。"
        )
    lines += ["", "## 论文材料（按被引排序；E 编号用于证据引用）", ""]
    for i, p in enumerate(subset, start=1):
        p["_evidence_no"] = f"E{i}"
        bits = [f'- [E{i}] {p["title"]}']
        if p.get("year"):
            bits.append(f'({p["year"]})')
        bits.append(f'被引{p.get("citation_count") or 0}')
        if p.get("citation_velocity") is not None:
            bits.append(f'速度{p["citation_velocity"]}/年')
        if p.get("recent_citations") is not None:
            bits.append(f'近2年被引{p["recent_citations"]}')
        if p.get("highly_cited"):
            bits.append('★同年前5%')
        if p.get("trajectory") == "rising":
            bits.append('引用爬升')
        elif p.get("trajectory") == "declining":
            bits.append('引用回落')
        if p.get("surge"):
            bits.append('🔥突增')
        venue = p.get("venue") or {}
        if venue.get("name"):
            bits.append(f'期刊[{venue["name"]}]')
        if p.get("cluster_label"):
            bits.append(f'子方向[{p["cluster_label"]}]')
        lines.append(" ".join(bits))
        if p.get("abstract"):
            lines.append(f'  摘要: {_clip(p["abstract"], _ABSTRACT_CLIP)}')
    lines += [
        "",
        "## 输出格式",
        "```json",
        '{"verdict": {"status": "done_extensively|done_partial|emerging|gap|unclear", '
        '"confidence": 0.0~1.0, "explanation": "2~4句判定依据，必须点名具体论文", '
        '"evidence": ["E3", "E7"]},',
        ' "maturity": "实现到什么程度：代表系统/方法、公开基准上的水平（1~3 句）",',
        ' "landscape_summary": "领域现状一段话（研究在解决什么、主流路线是什么）",',
        ' "hotspots": ["近期热点/突破，每条点名论文"],',
        ' "gaps": ["尚未解决的空白或值得做的新问题"],',
        ' "follow_ups": ["建议的后续检索式或相邻问题"],',
        ' "markdown": "面向学者的完整 Markdown 报告：直接回答问题 → 现状与流派 → 代表工作 → 热点 → 空白与建议"}',
        "```",
        "要求：status 严格按定义——done_extensively=已有多个成熟实现；done_partial=有相关工作但未完全覆盖"
        "问题的全部条件；emerging=近1~2年才出现；gap=材料中确实没有直接实现（≠检索失败）；"
        "unclear=证据不足。evidence 引用的 E 编号必须真实存在于材料中。",
    ]
    return "\n".join(lines), [by_key[p["key"]] for p in subset]


def _preset_for_depth(depth: str = "standard") -> dict:
    return DEPTH_PRESETS.get(depth, DEPTH_PRESETS["standard"])


def _normalize_synthesis(data: dict, subset: list[dict], *, retrieval_fallback: bool = False) -> ExplorationSynthesis:
    valid_e = {p.get("_evidence_no") for p in subset}
    key_by_e = {p.get("_evidence_no"): p["key"] for p in subset}

    verdict = None
    v = data.get("verdict")
    if isinstance(v, dict):
        status = str(v.get("status") or "").strip()
        if status not in NOVELTY_STATUSES:
            status = "unclear"
        try:
            conf = max(0.0, min(1.0, float(v.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            conf = 0.0
        explanation = _clip(str(v.get("explanation") or ""), 600)
        # 铁律护栏（GOAL.md）：「gap」必须与「检索失败」区分。回退检索（原句直检索）
        # 查全率不可信，此时声称空白是对学者的误导——降级为 unclear 并压低置信度。
        if retrieval_fallback and status == "gap":
            status = "unclear"
            conf = min(conf, 0.5)
            explanation = _clip(
                explanation + "（注意：本次检索未经检索式拆解，查全率存疑，已降级为「证据不足」而非空白）", 660,
            )
        ev = [key_by_e[e] for e in (v.get("evidence") or []) if e in valid_e and e in key_by_e][:6]
        verdict = ExplorationVerdict(
            status=status, confidence=round(conf, 2),
            explanation=explanation,
            evidence_keys=ev,
        )

    def _lst(x, n: int) -> list[str]:
        if isinstance(x, list):
            return [_clip(str(s), 90) for s in x if str(s).strip()][:n]
        return [_clip(str(x), 90)] if x and str(x).strip() else []

    return ExplorationSynthesis(
        verdict=verdict,
        maturity=_clip(str(data.get("maturity") or ""), 400),
        landscape_summary=_clip(str(data.get("landscape_summary") or ""), 600),
        hotspots=_lst(data.get("hotspots"), 6),
        gaps=_lst(data.get("gaps"), 6),
        follow_ups=_lst(data.get("follow_ups"), 5),
        markdown_report=str(data.get("markdown") or data.get("markdown_report") or "").strip()[:16000],
    )


async def _task_paradigms(cfg: dict, papers: list[dict]) -> list[ParadigmInfo]:
    """把聚类结果交给 LLM 具象化为流派（复用 domain_synthesizer 的提示词与归一化）。"""
    from .domain_synthesizer import _task_paradigms as _dp

    node_id_of = {p["key"]: f"x{i}" for i, p in enumerate(papers)}
    by_cluster: dict[int, list[dict]] = defaultdict(list)
    for p in papers:
        c = p.get("cluster")
        if isinstance(c, int) and c >= 0:
            by_cluster[c].append(p)
    clusters = []
    for c, members in by_cluster.items():
        if len(members) < 2:
            continue
        members = sorted(members, key=lambda x: x.get("citation_count") or 0, reverse=True)[:10]
        clusters.append({
            "cluster_id": c,
            "members": [
                {
                    "id": node_id_of[m["key"]],
                    "title": m["title"],
                    "year": m.get("year"),
                    "role": "",
                    "takeaway": "",
                    "pagerank": 0.0,
                }
                for m in members
            ],
        })
    clusters.sort(key=lambda x: len(x["members"]), reverse=True)
    if not clusters:
        return []
    return await _dp(cfg, clusters[:8])


async def synthesize_exploration(
    question: str, decompose: dict, papers: list[dict], metrics: dict,
    llm_cfg: Optional[dict], *, max_papers: int,
) -> Optional[ExplorationSynthesis]:
    """LLM 跨文献综合。无 Key / 证据不足 → None（统计视图照常可用）。"""
    if not llm_cfg or not llm_cfg.get("api_key"):
        logger.info("explorer synthesize: 未配置解读 API Key，跳过（纯统计视图）")
        return None
    if len(papers) < _MIN_SYNTH_PAPERS:
        logger.info("explorer synthesize: 召回 %d 篇不足 %d，跳过", len(papers), _MIN_SYNTH_PAPERS)
        return None

    from .domain_synthesizer import _llm_json

    synth: Optional[ExplorationSynthesis] = None
    try:
        user, subset = _synth_user(question, decompose, papers, metrics, max_papers=max_papers)
        # 富化后摘要覆盖率高（run 14 实测 117/155），材料变大 → markdown 输出更长；
        # 推理模型的 hidden reasoning 与 content 共享 max_tokens，4000 会截断 JSON
        # （2026-09-02 run 14 实测全重试失败），提到 8000 留足余量
        data = await _llm_json(llm_cfg, _SYNTH_SYSTEM, user, max_tokens=8000)
        synth = _normalize_synthesis(data, subset, retrieval_fallback=bool(decompose.get("fallback")))
    except Exception as e:
        logger.warning(f"explorer synthesize main task failed (non-fatal): {e}")
        synth = ExplorationSynthesis(partial=True)

    try:
        synth.paradigms = await _task_paradigms(llm_cfg, papers)
    except Exception as e:
        synth.partial = True
        logger.warning(f"explorer synthesize paradigms failed (non-fatal): {e}")

    if synth.partial and not (
        synth.verdict or synth.maturity or synth.landscape_summary
        or synth.hotspots or synth.gaps or synth.paradigms or synth.markdown_report
    ):
        logger.warning("explorer synthesize: 全部任务失败，不产出 synthesis")
        return None
    synth.stats = {
        "papers_fed": min(len(papers), max_papers),
        "model": llm_cfg.get("model", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "partial": synth.partial,
    }
    return synth


# ── 编排：run_exploration ─────────────────────────────────────────────────────

def _set_stage(run_id: int, stage: str, status: str = "running", **fields) -> None:
    with Session(get_engine()) as s:
        run = s.get(ExplorationRun, run_id)
        if run is None:
            return
        run.status = status
        run.current_stage = stage
        for k, v in fields.items():
            setattr(run, k, v)
        run.updated_at = datetime.now(timezone.utc)
        s.add(run)
        s.commit()


async def run_exploration(run_id: int) -> None:
    """领域探索主入口（后台任务）。产物逐段落盘，SSE 全程推送进度。"""
    from ..ai_config import get_analysis_config

    bus = get_exploration_bus(run_id)

    async def emit(event: str, **data):
        await bus.publish(StreamEvent(event=event, data=data))

    try:
        with Session(get_engine()) as s:
            run = s.get(ExplorationRun, run_id)
            question = (run.question or "").strip() if run else ""
            depth = run.depth if run else "standard"
        preset = _preset_for_depth(depth)
        now_year = datetime.now(timezone.utc).year
        llm_cfg = get_analysis_config()

        # 1) decompose
        _set_stage(run_id, "decompose")
        await emit("stage_start", stage="decompose")
        decompose = await decompose_question(question, llm_cfg, max_queries=preset["max_queries"])
        with Session(get_engine()) as s:
            run = s.get(ExplorationRun, run_id)
            if run is not None:
                run.decompose = decompose
                s.add(run)
                s.commit()
        await emit("stage_done", stage="decompose", summary={
            "intent": decompose["intent"],
            "queries": decompose["en_queries"],
            "fallback": decompose["fallback"],
        })

        # 2) recall
        _set_stage(run_id, "recall")
        await emit("stage_start", stage="recall")

        async def _recall_progress(query: str, hit_count: int, total_so_far: int):
            await emit("recall_progress", query=query, hit_count=hit_count, total_so_far=total_so_far)

        papers, provider_name = await recall_papers(
            decompose["en_queries"], per_query=preset["per_query"], cap=preset["cap"],
            now_year=now_year, progress=_recall_progress,
        )
        _set_stage(run_id, "recall", "running", provider=provider_name)
        await emit("stage_done", stage="recall", summary={
            "provider": provider_name, "total": len(papers),
        })

        # 3) enrich（指标富化：逐年引用 / 摘要 / 关键词 / 期刊 SJR——全部非致命）
        _set_stage(run_id, "enrich")
        await emit("stage_start", stage="enrich")

        async def _enrich_progress(done: int):
            await emit("enrich_progress", done=done, total=len(papers))

        try:
            enrich_stats = await enrich_papers(papers, progress=_enrich_progress)
        except Exception as ee:
            logger.warning(f"explorer enrich failed (non-fatal): {ee}")
            enrich_stats = {}
        await emit("stage_done", stage="enrich", summary=enrich_stats)

        # 4) metrics + cluster（纯算法）
        _set_stage(run_id, "metrics")
        await emit("stage_start", stage="metrics")
        metrics = compute_metrics(papers, now_year=now_year)
        _, edges, modularity = cluster_papers(papers)
        metrics["clusters"] = len({p.get("cluster") for p in papers if p.get("cluster", -1) >= 0})
        metrics["modularity"] = modularity
        graph = build_graph_payload(papers, edges, modularity=modularity)
        await emit("stage_done", stage="metrics", summary={
            "total": metrics["total"], "clusters": metrics["clusters"],
        })

        result = {
            "decompose": decompose,
            "provider": provider_name,
            "papers": [
                {k: v for k, v in p.items() if not k.startswith("_")}
                for p in papers
            ],
            "metrics": metrics,
            "graph": graph,
        }
        await emit("metrics_ready", **result)

        # 5) synthesize（无 Key / 证据不足自动跳过）
        synthesis_dict = None
        _set_stage(run_id, "synthesize")
        await emit("stage_start", stage="synthesize")
        try:
            synthesis = await synthesize_exploration(
                question, decompose, papers, metrics, llm_cfg, max_papers=preset["llm_papers"],
            )
            if synthesis is not None:
                synthesis_dict = synthesis.model_dump()
                result["synthesis"] = synthesis_dict
                await emit("synthesis_ready", **synthesis_dict)
                await emit("stage_done", stage="synthesize", summary={"partial": synthesis.partial})
            else:
                await emit("stage_done", stage="synthesize", summary={
                    "skipped": "未配置解读 API Key 或召回证据不足",
                })
        except Exception as se:
            logger.warning(f"explorer synthesize failed (non-fatal): {se}")
            await emit("stage_done", stage="synthesize", summary={"error": str(se)})

        # 6) persist
        _set_stage(run_id, "persist")
        await emit("stage_start", stage="persist")
        with Session(get_engine()) as s:
            run = s.get(ExplorationRun, run_id)
            if run is not None:
                run.result = result
                s.add(run)
                s.commit()
        await emit("stage_done", stage="persist", summary={})

        _set_stage(run_id, "persist", "done")
        await emit("done", run_id=run_id, total=metrics["total"])

    except Exception as e:
        logger.exception(f"Exploration run {run_id} failed")
        err_msg = str(e)
        current_stage = ""
        try:
            with Session(get_engine()) as s:
                run = s.get(ExplorationRun, run_id)
                if run is not None:
                    current_stage = run.current_stage or ""
                    run.status = "failed"
                    run.error_message = err_msg
                    s.add(run)
                    s.commit()
        except Exception:
            logger.exception("Failed to persist failed-state for exploration run %s", run_id)
        try:
            await emit("error", message=err_msg, stage=current_stage)
        except Exception:
            logger.exception("Failed to emit error event for exploration run %s", run_id)
    finally:
        await bus.close()
        drop_exploration_bus(run_id)
