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
from typing import Optional

from sqlmodel import Session, create_engine, select

from ....config import settings
from ....models.paper import Paper
from ....models.workspace import WorkspacePaper
from ..arxiv_client import search_by_title as arxiv_search_by_title, get_metadata
from ..crossref_client import search_by_title as crossref_search_by_title
from ..ref_parser import extract_references
from ..grobid_parser import is_grobid_alive, parse_references as grobid_parse_references
from ..pdf_downloader import download_pdf
from ...ai_config import get_analysis_config, get_arxiv_expand_config

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
# PDF URL 解析：arXiv → DOI redirect
# ────────────────────────────────────────────────────────────────

async def _resolve_candidate_pdf_url(candidate: dict) -> Optional[str]:
    """解析候选论文的 PDF 下载链接。

    优先级：
    1. arXiv PDF（开放获取，最稳定）
    2. CrossRef 提供的 PDF 链接
    3. doi.org 重定向（可能是 publisher 页面，不保证 PDF）
    """
    # arXiv 直接构造 PDF URL
    if candidate.get("arxiv_id"):
        return f"https://arxiv.org/pdf/{candidate['arxiv_id']}.pdf"

    # DOI：尝试 CrossRef 获取开放获取链接
    if candidate.get("doi"):
        from ..crossref_client import get_pdf_url_by_doi
        url = await get_pdf_url_by_doi(candidate["doi"])
        return url

    return candidate.get("pdf_url")


# ────────────────────────────────────────────────────────────────
# 引用解析：GROBID（默认）→ 正则回退
# ────────────────────────────────────────────────────────────────

async def _parse_references_for_seed(pdf_path: str, cfg: dict) -> tuple[list[dict], str]:
    """按配置选择引用解析后端，返回 (refs, backend_name)。

    parser=grobid 且服务可达时用 GROBID（结构化、带标题、兼容任意引用格式）；
    否则回退正则解析（零依赖，但只能解析 [N] 且不提供标题）。
    """
    parser = (cfg.get("parser") or "grobid").lower()

    if parser == "grobid":
        grobid_url = cfg.get("grobid_url", "http://localhost:8070")
        if await is_grobid_alive(grobid_url):
            try:
                refs = await grobid_parse_references(pdf_path, grobid_url)
                if refs:
                    return refs, "grobid"
                logger.warning("GROBID returned 0 refs, falling back to regex parser")
            except Exception as e:
                logger.warning(f"GROBID parse failed ({e}), falling back to regex parser")
        else:
            logger.warning(
                f"GROBID not reachable at {grobid_url}, falling back to regex parser. "
                f"启动： docker run --rm -p 8070:8070 grobid/grobid:0.8.1"
            )

    # ── 正则回退 ──
    from ...pdf_parser import extract_pdf_content
    content = await asyncio.to_thread(extract_pdf_content, pdf_path)
    refs = extract_references(content.full_text or "")
    return refs, "regex"


# ────────────────────────────────────────────────────────────────
# 主阶段
# ────────────────────────────────────────────────────────────────

async def stage_expand_arxiv(
    ws_id: int,
    seeds: list,
    on_paper_added,
    run_params: Optional[dict] = None,
) -> list[Paper]:
    """
    通过解析 seed 论文的参考文献列表扩展工作区。

    解析后端：GROBID（默认，结构化、带标题）→ 正则回退。
    每条引用按下列优先级解析下载链接：
    1. 引用自带 arXiv ID → arXiv PDF
    2. 引用自带 DOI → DOI 下载
    3. 有标题（GROBID 直接给 / 正则后端用 LLM 提）→ arXiv 搜索 / CrossRef 查 DOI

    返回所有找到的论文（新下载 + 已存在 dedup 后）。
    """
    logger.info(f"stage_expand_arxiv: start, seeds={len(seeds)}")

    cfg = get_arxiv_expand_config()
    # 优先用本次 run 的 expand_top_k（深度研究控制面板设的「最多下载数」），
    # 没传则回退到 Settings 里的 arxiv_expand.top_k
    top_k: int = int((run_params or {}).get("expand_top_k") or cfg.get("top_k", 5))
    use_llm: bool = bool(cfg.get("use_llm_title", True))
    max_llm_refs: int = int(cfg.get("max_llm_refs", 15))
    # 标题搜索的引用条数至少要 >= top_k，否则候选不够填满 top_k
    search_cap: int = max(max_llm_refs, top_k)

    logger.info(
        f"stage_expand_arxiv: top_k={top_k} (run expand_top_k="
        f"{(run_params or {}).get('expand_top_k')}) "
        f"use_llm={use_llm} search_cap={search_cap}"
    )

    analysis_cfg = get_analysis_config()
    added: list[Paper] = []
    total_added = 0

    for seed in seeds:
        if not getattr(seed, "pdf_path", None):
            continue

        # ── Step 1: 解析参考文献（GROBID 默认，正则回退）──
        try:
            refs, backend = await _parse_references_for_seed(seed.pdf_path, cfg)
        except Exception as e:
            logger.warning(f"arxiv_expand: ref parse failed for paper {seed.id}: {e}")
            continue

        if not refs:
            logger.info(f"arxiv_expand[seed={seed.id}]: no references parsed")
            continue

        logger.info(
            f"arxiv_expand[seed={seed.id}]: parsed {len(refs)} references "
            f"(backend={backend})"
        )

        # ── Step 2: 分类 — 自带 ID 直接命中 vs 需按标题搜索 ──
        candidates: list[dict] = []
        title_queue: list[dict] = []   # 无 arXiv/DOI、需按标题联网搜索的引用
        direct_arxiv = 0
        direct_doi = 0

        for ref in refs:
            ref_title = ref.get("title") or ref.get("raw_text", "")[:120]
            if ref.get("arxiv_id"):
                # 优先级 1：引用自带 arXiv ID
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
                direct_arxiv += 1
            elif ref.get("doi"):
                # 优先级 2：引用自带 DOI（无 arXiv ID）
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
                direct_doi += 1
            else:
                # 优先级 3：按标题搜索（GROBID 已带标题；regex 后端需 LLM 提）
                title_queue.append(ref)

        # ── Step 3: 按标题搜索 → arXiv + CrossRef ──
        title_arxiv_found = 0
        title_crossref_found = 0

        # 频次感知配额：只搜索"有可能进入 top_k"的引用，
        # 避免在最终会被切掉的引用上浪费 arXiv(3 秒限速) + CrossRef 请求
        if title_queue:
            title_queue.sort(key=lambda r: r.get("freq", 0), reverse=True)
            direct_freqs = sorted((c.get("freq", 0) for c in candidates), reverse=True)
            # 已有 >= top_k 个直接命中时，门槛 = 第 top_k 名频次；
            # 只有频次严格高于门槛的引用才可能挤进 top_k。不足 top_k 时取 -1 放行全部。
            bar = direct_freqs[top_k - 1] if len(direct_freqs) >= top_k else -1
            batch = [r for r in title_queue if r.get("freq", 0) > bar][:search_cap]
        else:
            batch = []

        if batch:
            # 标题：优先用解析后端直接给的；缺标题的（regex 后端）才走 LLM 提取
            missing = [i for i, r in enumerate(batch) if not r.get("title")]
            if missing and use_llm:
                sub = [batch[i] for i in missing]
                extracted = await _llm_batch_extract_titles(sub, analysis_cfg)
                for i, t in zip(missing, extracted):
                    if t:
                        batch[i] = {**batch[i], "title": t}

            logger.info(
                f"arxiv_expand[seed={seed.id}]: title-search {len(batch)} refs "
                f"(direct_arxiv={direct_arxiv}, direct_doi={direct_doi}, "
                f"no_id={len(title_queue)}, llm_extract={len(missing) if use_llm else 0})"
            )

            for ref in batch:
                title = ref.get("title")
                if not title:
                    continue

                # 策略 A：arXiv 标题搜索（含 3 秒限速）
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
                    title_arxiv_found += 1
                    continue  # arXiv 找到了就不用再查 CrossRef

                # 策略 B：arXiv 没找到 → CrossRef 按标题查 DOI
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
                    title_crossref_found += 1

        # ── Step 4: 去重 ──
        seen_keys: set[str] = set()
        unique_cands: list[dict] = []
        for c in candidates:
            # 用 arXiv ID 或 DOI 作为去重 key
            key = f"arxiv:{c['arxiv_id']}" if c.get("arxiv_id") else f"doi:{c.get('doi', '')}"
            if key in seen_keys or (not c.get("arxiv_id") and not c.get("doi")):
                continue
            seen_keys.add(key)
            unique_cands.append(c)
        candidates = unique_cands

        logger.info(
            f"arxiv_expand[seed={seed.id}]: {len(candidates)} unique candidates "
            f"(direct_arxiv={direct_arxiv}, direct_doi={direct_doi}, "
            f"title_arxiv={title_arxiv_found}, title_crossref={title_crossref_found})"
        )
        if not candidates:
            continue

        # ── Step 5: 按正文引用频次排序取 top_k（频次相同则按引用先后）──
        candidates.sort(
            key=lambda c: (-c.get("freq", 0), c.get("ref_index", 10**9))
        )
        final_cands = candidates[:top_k]
        logger.info(
            f"arxiv_expand[seed={seed.id}]: downloading top {len(final_cands)} candidates"
        )

        # ── Step 6: 下载 PDF + 注册到数据库 ──
        for cand in final_cands:
            ext_id = (
                f"arxiv:{cand['arxiv_id']}"
                if cand.get("arxiv_id")
                else f"doi:{cand.get('doi', '')}"
            )
            title = cand.get("title") or cand.get("arxiv_id") or cand.get("doi", "untitled")

            # 去重：已下载过则只补 workspace 关联
            with Session(_get_engine(), expire_on_commit=False) as s:
                existing = s.exec(
                    select(Paper).where(Paper.external_id == ext_id)
                ).first()
                if existing:
                    wp = s.exec(
                        select(WorkspacePaper)
                        .where(WorkspacePaper.workspace_id == ws_id)
                        .where(WorkspacePaper.paper_id == existing.id)
                    ).first()
                    if not wp:
                        s.add(WorkspacePaper(workspace_id=ws_id, paper_id=existing.id))
                        s.commit()
                    if existing not in added:
                        added.append(existing)
                    await on_paper_added(
                        "paper_added",
                        paper_id=existing.id, title=existing.title,
                        level=existing.level, source_paper_id=seed.id,
                        total_expanded=total_added, is_new=False,
                    )
                    continue

            # 解析最佳 PDF URL
            pdf_url = await _resolve_candidate_pdf_url(cand)
            if not pdf_url:
                logger.warning(
                    f"arxiv_expand: no PDF URL for '{title[:50]}' "
                    f"(arxiv={cand.get('arxiv_id')}, doi={cand.get('doi')})"
                )
                continue

            # 下载 PDF
            local = await download_pdf(pdf_url)
            if not local:
                # 如果主 URL 下载失败，尝试备选 URL
                fallback_url = None
                if cand.get("arxiv_id") and cand.get("doi"):
                    # arXiv 失败了试 DOI
                    fallback_url = f"https://doi.org/{cand['doi']}"
                elif cand.get("doi") and pdf_url != f"https://doi.org/{cand['doi']}":
                    # CrossRef link 失败了试 doi.org 重定向
                    fallback_url = f"https://doi.org/{cand['doi']}"

                if fallback_url and fallback_url != pdf_url:
                    logger.info(f"arxiv_expand: trying fallback {fallback_url}")
                    local = await download_pdf(fallback_url)

                if not local:
                    logger.warning(f"arxiv_expand: PDF download failed: {pdf_url}")
                    continue

            with Session(_get_engine(), expire_on_commit=False) as s:
                p = Paper(
                    filename=f"{title[:60]}.pdf",
                    pdf_path=local,
                    file_size=0,
                    title=title[:255],
                    year=cand.get("year"),
                    status="indexed",
                    source="expanded",
                    level=2,
                    parent_paper_id=seed.id,
                    external_id=ext_id,
                )
                s.add(p)
                s.commit()
                s.add(WorkspacePaper(workspace_id=ws_id, paper_id=p.id))
                s.commit()
                s.refresh(p)

            total_added += 1
            added.append(p)
            logger.info(
                f"arxiv_expand: added paper={p.id} '{p.title[:50]}' "
                f"({cand['source']}, ext_id={ext_id})"
            )
            await on_paper_added(
                "paper_added",
                paper_id=p.id, title=p.title, level=p.level,
                source_paper_id=seed.id, total_expanded=total_added,
                is_new=True,
            )

    logger.info(f"arxiv_expand: done, total_added={total_added}, total_returned={len(added)}")
    return added
