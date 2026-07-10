"""
Stage 2b — expand_arxiv（v2：引用提取管线）

解析 L1 seed 论文 PDF 文本中的 References 列表，通过三级策略获取论文：

  优先级 1 — 正则匹配 arXiv ID（ref_parser 已实现）
             → 直接通过 arXiv API 下载开放获取 PDF
  优先级 2 — 正则匹配 DOI（ref_parser 已实现）
             → 通过 DOI 解析下载 PDF
  优先级 3 — LLM 批量提取标题 → arXiv 搜索 / CrossRef DOI 查询
             → 找到 arXiv 则用 arXiv PDF
             → 找到 DOI 则通过 DOI 下载
             → 两者都找不到则跳过

过滤策略：
  按在引用列表中出现的顺序取 top_k（越靠前越重要）

无需 Semantic Scholar API key。
"""
import asyncio
import json
import logging
import re
from typing import Optional

from sqlmodel import Session, create_engine

from ....config import settings
from ....models.paper import Paper
from ..arxiv_client import search_by_title as arxiv_search_by_title
from ..crossref_client import search_by_title as crossref_search_by_title
from ..ref_parser import extract_references
from ..grobid_parser import is_grobid_alive, parse_references as grobid_parse_references
from ..fusion_ranker import CHANNEL_PDF_REF, tag_channel
from ..reference_store import resolve_cited_links, upsert_references
from ....models.recall import RecallCandidate, RecallSource, build_recall_candidate
from ...ai_config import get_analysis_config, get_arxiv_expand_config
from ...paper_download import download_for_paper
from ...paper_identity import PaperWork, find_or_register

logger = logging.getLogger(__name__)

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.resolved_database_url,
            connect_args={"check_same_thread": False},
        )
    return _engine


# ────────────────────────────────────────────────────────────────
# LLM 批量标题提取
# ────────────────────────────────────────────────────────────────

async def _llm_batch_extract_titles(refs: list[dict], cfg: dict) -> list[Optional[str]]:
    """一次 LLM 调用批量提取多条引用的英文标题。

    返回与 refs 等长的列表，无法识别的条目为 None。
    """
    api_key = cfg.get("api_key", "")
    if not api_key or not refs:
        return [None] * len(refs)

    refs_text = "\n".join(
        f"[{i+1}] {r['raw_text'][:300]}"
        for i, r in enumerate(refs)
    )
    prompt = (
        f"以下是 {len(refs)} 条学术引用文献，请提取每条的英文论文标题。\n"
        f"只输出 JSON 数组，长度必须为 {len(refs)}，按序与每条引用对应。\n"
        f"无法识别则对应位置填空字符串。不要输出任何其他内容。\n\n"
        f"{refs_text}\n\n"
        f'格式示例：["Title A", "Title B", "", "Title D"]'
    )

    try:
        from ...ai_analyzer import _create_client
        client = _create_client(api_key, cfg.get("base_url", ""))
        try:
            msg = await client.messages.create(
                model=cfg.get("model", "claude-haiku-4-5-20251001"),
                # max_tokens 必须远大于 thinking.budget_tokens：留 1024 给思考，
                # 其余给 JSON 输出。之前 2048 太小，思考一长输出就被截断成空。
                max_tokens=4096,
                temperature=1,
                thinking={"type": "enabled", "budget_tokens": 1024},
                system="只输出 JSON 数组，不含任何其他文字。",
                messages=[{"role": "user", "content": prompt}],
            )
        finally:
            await client.close()

        from ...ai_analyzer import _extract_text
        content = _extract_text(msg).strip()

        # 诊断：文本为空时打印 stop_reason 和返回的 block 类型，定位是被截断还是只有思考块
        if not content:
            logger.warning(
                f"LLM title extraction: empty text "
                f"(stop_reason={getattr(msg, 'stop_reason', '?')}, "
                f"blocks={[type(b).__name__ for b in getattr(msg, 'content', [])]})"
            )
            return [None] * len(refs)

        if "[" in content:
            start = content.find("[")
            end = content.rfind("]") + 1
            if end > start:
                content = content[start:end]

        titles = json.loads(content)
        if not isinstance(titles, list):
            raise ValueError("non-list response")

        result: list[Optional[str]] = []
        for t in titles[:len(refs)]:
            if isinstance(t, str):
                clean = t.strip().strip('"\'')
                result.append(clean if len(clean) > 5 else None)
            else:
                result.append(None)
        while len(result) < len(refs):
            result.append(None)

        found = sum(1 for t in result if t)
        logger.info(f"LLM batch title extraction: {found}/{len(refs)} titles identified")
        return result

    except json.JSONDecodeError as e:
        # 拿到了文本但不是合法 JSON：打印前 200 字符以便排查模型输出格式
        logger.warning(
            f"LLM title extraction: JSON parse failed ({e}); "
            f"raw content[:200]={content[:200]!r}"
        )
        return [None] * len(refs)
    except Exception as e:
        logger.warning(f"LLM batch title extraction failed: {e}")
        return [None] * len(refs)


# ────────────────────────────────────────────────────────────────
# 引用解析：GROBID（默认）→ 正则回退
# ────────────────────────────────────────────────────────────────

async def _parse_references_for_seed(pdf_path: str, cfg: dict) -> tuple[list[dict], list[dict], str]:
    """按配置选择引用解析后端，返回 (refs, contexts, backend_name)。

    parser=grobid 且服务可达时用 GROBID（结构化、带标题、兼容任意引用格式）；
    否则回退正则解析（零依赖，但只能解析 [N] 且不提供标题，contexts 为空）。
    """
    parser = (cfg.get("parser") or "grobid").lower()

    if parser == "grobid":
        grobid_url = cfg.get("grobid_url", "http://localhost:8070")
        if await is_grobid_alive(grobid_url):
            try:
                refs, contexts = await grobid_parse_references(pdf_path, grobid_url)
                if refs:
                    return refs, contexts, "grobid"
                logger.warning("GROBID returned 0 refs, falling back to regex parser")
            except Exception as e:
                logger.warning(f"GROBID parse failed ({e}), falling back to regex parser")
        else:
            logger.warning(
                f"GROBID not reachable at {grobid_url}, falling back to regex parser. "
                f"启动： docker run --rm -p 8070:8070 grobid/grobid:0.9.0-crf"
            )

    # ── 正则回退 ──
    from ...pdf_parser import extract_pdf_content
    content = await asyncio.to_thread(extract_pdf_content, pdf_path)
    refs = extract_references(content.full_text or "")
    return refs, [], "regex"


# ────────────────────────────────────────────────────────────────
# PDF 引用 → RecallCandidate（供融合池）
# ────────────────────────────────────────────────────────────────

def _pdf_dict_to_recall_candidate(cand: dict, seed_id: int, rank: int) -> RecallCandidate | None:
    built = build_recall_candidate(
        title=cand.get("title") or "",
        doi=cand.get("doi"),
        arxiv_id=cand.get("arxiv_id"),
        source=RecallSource.BACKWARD_REF,
        source_rank=rank,
        seed_paper_id=str(seed_id),
        seed_relation="references",
        pdf_url=cand.get("pdf_url"),
        year=cand.get("year"),
        provenance=[
            f"resolver:{cand.get('source', 'pdf')}",
            "method:pdf_reference",
        ],
    )
    if not built:
        return None
    return tag_channel(built, CHANNEL_PDF_REF)


async def _build_pdf_candidates_for_seed(
    seed: Paper,
    cfg: dict,
    *,
    top_k: int,
    use_llm: bool,
    search_cap: int,
    analysis_cfg: dict,
) -> tuple[list[dict], list[dict], list[dict]]:
    """解析 seed PDF 引用并构建候选 dict 列表（不下载）。"""
    if not getattr(seed, "pdf_path", None):
        return [], [], []

    try:
        refs, raw_contexts, backend = await _parse_references_for_seed(seed.pdf_path, cfg)
    except Exception as e:
        logger.warning(f"arxiv_expand: ref parse failed for paper {seed.id}: {e}")
        return [], [], []

    if not refs:
        return [], [], []

    enriched: list[dict] = []
    if raw_contexts:
        ref_by_index: dict[int, dict] = {}
        for r in refs:
            ref_by_index[r.get("index", -1)] = r
        for ctx in raw_contexts:
            m = re.search(r"\d+", ctx.get("ref_id", ""))
            idx = int(m.group(0)) if m else -1
            ref = ref_by_index.get(idx)
            enriched.append({
                **ctx,
                "ref_index": idx,
                "arxiv_id": ref.get("arxiv_id") if ref else None,
                "doi": ref.get("doi") if ref else None,
                "title": ref.get("title") if ref else None,
            })

    ref_inputs: list[dict] = []
    enriched_by_index: dict[int, list[dict]] = {}
    for ctx in enriched:
        idx = ctx.get("ref_index", -1)
        enriched_by_index.setdefault(idx, []).append(ctx)
    for ref in refs:
        idx = ref.get("index", -1)
        ref_inputs.append({
            "cited_doi": ref.get("doi"),
            "cited_arxiv_id": ref.get("arxiv_id"),
            "cited_title": ref.get("title"),
            "frequency": ref.get("freq", 1),
            "contexts": enriched_by_index.get(idx, []),
            "source": backend,
            "confidence": 1.0,
        })

    candidates: list[dict] = []
    title_queue: list[dict] = []

    for ref in refs:
        ref_title = ref.get("title") or ref.get("raw_text", "")[:120]
        if ref.get("arxiv_id"):
            candidates.append({
                "arxiv_id": ref["arxiv_id"],
                "doi": ref.get("doi"),
                "pdf_url": f"https://arxiv.org/pdf/{ref['arxiv_id']}.pdf",
                "title": ref_title,
                "year": None,
                "source": "direct_arxiv",
                "freq": ref.get("freq", 0),
                "ref_index": ref.get("index", 0),
            })
        elif ref.get("doi"):
            candidates.append({
                "arxiv_id": None,
                "doi": ref["doi"],
                "pdf_url": f"https://doi.org/{ref['doi']}",
                "title": ref_title,
                "year": None,
                "source": "direct_doi",
                "freq": ref.get("freq", 0),
                "ref_index": ref.get("index", 0),
            })
        else:
            title_queue.append(ref)

    if title_queue:
        title_queue.sort(key=lambda r: r.get("freq", 0), reverse=True)
        direct_freqs = sorted((c.get("freq", 0) for c in candidates), reverse=True)
        bar = direct_freqs[top_k - 1] if len(direct_freqs) >= top_k else -1
        batch = [r for r in title_queue if r.get("freq", 0) > bar][:search_cap]
    else:
        batch = []

    if batch:
        missing = [i for i, r in enumerate(batch) if not r.get("title")]
        if missing and use_llm:
            sub = [batch[i] for i in missing]
            extracted = await _llm_batch_extract_titles(sub, analysis_cfg)
            for i, t in zip(missing, extracted):
                if t:
                    batch[i] = {**batch[i], "title": t}

        for ref in batch:
            title = ref.get("title")
            if not title:
                continue
            arxiv_result = await arxiv_search_by_title(title)
            if arxiv_result and arxiv_result.get("arxiv_id"):
                candidates.append({
                    "arxiv_id": arxiv_result["arxiv_id"],
                    "doi": None,
                    "pdf_url": (
                        arxiv_result.get("pdf_url")
                        or f"https://arxiv.org/pdf/{arxiv_result['arxiv_id']}.pdf"
                    ),
                    "title": arxiv_result.get("title") or title,
                    "year": arxiv_result.get("year"),
                    "source": "title_arxiv",
                    "freq": ref.get("freq", 0),
                    "ref_index": ref.get("index", 0),
                })
                continue
            crossref_result = await crossref_search_by_title(title)
            if crossref_result and crossref_result.get("doi"):
                candidates.append({
                    "arxiv_id": None,
                    "doi": crossref_result["doi"],
                    "pdf_url": crossref_result.get("pdf_url") or f"https://doi.org/{crossref_result['doi']}",
                    "title": crossref_result.get("title") or title,
                    "year": crossref_result.get("year"),
                    "source": "title_crossref",
                    "freq": ref.get("freq", 0),
                    "ref_index": ref.get("index", 0),
                })

    seen_keys: set[str] = set()
    unique_cands: list[dict] = []
    for c in candidates:
        key = f"arxiv:{c['arxiv_id']}" if c.get("arxiv_id") else f"doi:{c.get('doi', '')}"
        if key in seen_keys or (not c.get("arxiv_id") and not c.get("doi")):
            continue
        seen_keys.add(key)
        unique_cands.append(c)

    unique_cands.sort(key=lambda c: (-c.get("freq", 0), c.get("ref_index", 10**9)))
    return unique_cands[:top_k], enriched, ref_inputs


async def collect_pdf_reference_candidates(
    seeds: list,
    run_params: dict | None = None,
) -> tuple[list[RecallCandidate], dict[int, list[dict]]]:
    """
    解析 L1 seed PDF 引用，转为 pdf_ref 通道 RecallCandidate（不下载）。

    返回 (candidates, seed_id → enriched_contexts)。
    """
    cfg = get_arxiv_expand_config()
    top_k: int = int((run_params or {}).get("expand_top_k") or cfg.get("top_k", 5))
    use_llm: bool = bool(cfg.get("use_llm_title", True))
    max_llm_refs: int = int(cfg.get("max_llm_refs", 15))
    search_cap: int = max(max_llm_refs, top_k)
    analysis_cfg = get_analysis_config()

    all_candidates: list[RecallCandidate] = []
    all_contexts: dict[int, list[dict]] = {}

    for seed in seeds:
        cands, enriched, ref_inputs = await _build_pdf_candidates_for_seed(
            seed, cfg, top_k=top_k, use_llm=use_llm, search_cap=search_cap, analysis_cfg=analysis_cfg,
        )
        if enriched:
            all_contexts[seed.id] = enriched
        if ref_inputs:
            with Session(_get_engine(), expire_on_commit=False) as s:
                upsert_references(s, seed.id, ref_inputs)
                resolve_cited_links(s, citing_paper_id=seed.id)
        for rank, cand in enumerate(cands):
            rc = _pdf_dict_to_recall_candidate(cand, seed.id, rank)
            if rc:
                all_candidates.append(rc)

    return all_candidates, all_contexts


async def _register_pdf_candidate(
    cand: dict,
    seed: Paper,
    ws_id: int,
    on_paper_added,
    *,
    total_added: int,
    added_ids: set[int],
    added: list[Paper],
) -> int:
    """通过 find_or_register + download_for_paper 登记单条 PDF 引用候选。"""
    title = cand.get("title") or cand.get("arxiv_id") or cand.get("doi", "untitled")
    legacy_ext = f"arxiv:{cand['arxiv_id']}" if cand.get("arxiv_id") else f"doi:{cand.get('doi', '')}"
    work = PaperWork(
        title=title,
        doi=cand.get("doi"),
        arxiv_id=cand.get("arxiv_id"),
        external_id=legacy_ext,
        url=cand.get("pdf_url"),
        source="expanded",
        level=2,
        parent_paper_id=seed.id,
        filename=f"{title[:60]}.pdf",
    )

    with Session(_get_engine(), expire_on_commit=False) as s:
        paper, is_new = find_or_register(s, work, workspace_id=ws_id)
        upsert_references(s, seed.id, [{
            "cited_paper_id": paper.id,
            "cited_doi": cand.get("doi"),
            "cited_arxiv_id": cand.get("arxiv_id"),
            "cited_title": title,
            "frequency": cand.get("freq", 1),
            "source": cand.get("source", "arxiv_expand"),
        }])

    with Session(_get_engine(), expire_on_commit=False) as s:
        path = await download_for_paper(s, paper.id)
        if path:
            paper = s.get(Paper, paper.id)
            if paper:
                paper.status = "indexed"
                s.add(paper)
                s.commit()
                s.refresh(paper)

    with Session(_get_engine(), expire_on_commit=False) as s:
        paper = s.get(Paper, paper.id)

    if paper.id not in added_ids:
        added_ids.add(paper.id)
        added.append(paper)
        if is_new:
            total_added += 1
        await on_paper_added(
            "paper_added",
            paper_id=paper.id,
            title=paper.title,
            level=paper.level,
            source_paper_id=seed.id,
            total_expanded=total_added,
            is_new=is_new,
        )
    return total_added


# ────────────────────────────────────────────────────────────────
# 主阶段
# ────────────────────────────────────────────────────────────────

async def stage_expand_arxiv(
    ws_id: int,
    seeds: list,
    on_paper_added,
    run_params: Optional[dict] = None,
) -> tuple[list[Paper], dict[int, list[dict]]]:
    """
    通过解析 seed 论文参考文献扩展工作区（独立 stage，不经 RRF）。

    生产路径优先走 collect_pdf_reference_candidates + stage_expand 融合；
    本函数保留供直接调用与兼容测试。
    """
    logger.info(f"stage_expand_arxiv: start, seeds={len(seeds)}")

    cfg = get_arxiv_expand_config()
    top_k: int = int((run_params or {}).get("expand_top_k") or cfg.get("top_k", 5))
    use_llm: bool = bool(cfg.get("use_llm_title", True))
    max_llm_refs: int = int(cfg.get("max_llm_refs", 15))
    search_cap: int = max(max_llm_refs, top_k)
    analysis_cfg = get_analysis_config()

    added: list[Paper] = []
    added_ids: set[int] = set()
    total_added = 0
    all_seed_contexts: dict[int, list[dict]] = {}

    for seed in seeds:
        cands, enriched, ref_inputs = await _build_pdf_candidates_for_seed(
            seed, cfg, top_k=top_k, use_llm=use_llm, search_cap=search_cap, analysis_cfg=analysis_cfg,
        )
        if enriched:
            all_seed_contexts[seed.id] = enriched
        if ref_inputs:
            with Session(_get_engine(), expire_on_commit=False) as s:
                upsert_references(s, seed.id, ref_inputs)
                resolve_cited_links(s, citing_paper_id=seed.id)
        for cand in cands:
            total_added = await _register_pdf_candidate(
                cand, seed, ws_id, on_paper_added,
                total_added=total_added, added_ids=added_ids, added=added,
            )

    logger.info(
        f"arxiv_expand: done, total_added={total_added}, total_returned={len(added)}, "
        f"seeds_with_contexts={len(all_seed_contexts)}"
    )
    return added, all_seed_contexts
