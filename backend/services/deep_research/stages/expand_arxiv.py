"""
Stage 2b — expand_arxiv

解析 L1 seed 论文 PDF 文本中的 References 列表，提取 arXiv ID，
通过 arXiv 官方 API 下载开放获取 PDF。

arXiv 规范：
- 请求串行，间隔 ≥ 3 秒（在 arxiv_client.py 中执行）
- max_results=1 每次检索
- 仅下载能拿到 arXiv ID 的论文

过滤策略（OR 逻辑，两者同时生效）：
- citation_count ≥ min_cite_count（来自 Semantic Scholar）
- 排名在 top_k 之内（按引用数降序）
"""
import asyncio
import logging
from typing import Optional

from sqlmodel import Session, create_engine, select

from ....config import settings
from ....models.paper import Paper
from ....models.workspace import WorkspacePaper
from ..arxiv_client import search_by_title, get_metadata
from ..ref_parser import extract_references
from ..pdf_downloader import download_pdf
from ...ai_config import get_analysis_config, get_arxiv_expand_config
from ...semantic_scholar import get_citation_count_by_arxiv

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


async def _llm_extract_title(ref_text: str, cfg: dict) -> Optional[str]:
    """调用 LLM 从引用文本中提取精确论文标题（轻量调用，max_tokens=80）。"""
    api_key = cfg.get("api_key", "")
    if not api_key:
        return None
    try:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(
            api_key=api_key,
            base_url=cfg.get("base_url") or None,
        )
        try:
            msg = await client.messages.create(
                model=cfg.get("model", "claude-haiku-4-5-20251001"),
                max_tokens=80,
                temperature=0,
                system="只输出英文论文标题，不含引号，不含其他任何内容。若无法识别则输出空字符串。",
                messages=[{
                    "role": "user",
                    "content": f"提取以下引用文献的英文论文标题：\n{ref_text}",
                }],
            )
        finally:
            await client.close()
        title = msg.content[0].text.strip().strip('"\'')
        return title if len(title) > 5 else None
    except Exception as e:
        logger.warning(f"LLM title extraction failed: {e}")
        return None


async def stage_expand_arxiv(
    ws_id: int,
    seeds: list,
    on_paper_added,
) -> list[Paper]:
    """
    通过解析 seed 论文的参考文献列表扩展工作区。

    返回所有找到的论文（新下载 + 已存在 dedup 后）。
    """
    from ...pdf_parser import extract_pdf_content

    cfg = get_arxiv_expand_config()
    top_k: int = int(cfg.get("top_k", 5))
    min_cite: int = int(cfg.get("min_cite_count", 0))
    use_llm: bool = bool(cfg.get("use_llm_title", True))
    max_llm_refs: int = int(cfg.get("max_llm_refs", 15))

    analysis_cfg = get_analysis_config()
    added: list[Paper] = []
    total_added = 0

    for seed in seeds:
        if not getattr(seed, "pdf_path", None):
            continue

        # 提取 PDF 文本
        try:
            content = await asyncio.to_thread(extract_pdf_content, seed.pdf_path)
            text = content.full_text or ""
        except Exception as e:
            logger.warning(f"arxiv_expand: PDF extract failed for paper {seed.id}: {e}")
            continue

        refs = extract_references(text)
        if not refs:
            logger.info(f"arxiv_expand[seed={seed.id}]: no [N] refs found in text")
            continue

        logger.info(f"arxiv_expand[seed={seed.id}]: parsed {len(refs)} reference blocks")

        # --- 解析 arXiv ID ---
        candidates: list[dict] = []  # {arxiv_id, pdf_url, title, year}
        llm_calls = 0

        for ref in refs:
            arxiv_id: Optional[str] = ref.get("arxiv_id")

            if arxiv_id:
                # 正则已找到 arXiv ID，直接构造 PDF 链接（不调 arXiv API）
                candidates.append({
                    "arxiv_id": arxiv_id,
                    "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                    "title": ref["raw_text"][:120],  # 解析后会被覆盖
                    "year": None,
                })
            elif use_llm and llm_calls < max_llm_refs:
                # 没有 arXiv ID，尝试 LLM 提取标题 → arXiv 搜索
                title = await _llm_extract_title(ref["raw_text"], analysis_cfg)
                llm_calls += 1
                if title:
                    result = await search_by_title(title)  # 含 3 秒限速
                    if result and result.get("arxiv_id"):
                        candidates.append({
                            "arxiv_id": result["arxiv_id"],
                            "pdf_url": result.get("pdf_url") or f"https://arxiv.org/pdf/{result['arxiv_id']}.pdf",
                            "title": result.get("title") or title,
                            "year": result.get("year"),
                        })

        # 去重（同一 arXiv ID 可能从多个 ref 中被提取）
        seen_axids: set[str] = set()
        unique_cands: list[dict] = []
        for c in candidates:
            if c["arxiv_id"] not in seen_axids:
                seen_axids.add(c["arxiv_id"])
                unique_cands.append(c)
        candidates = unique_cands

        logger.info(
            f"arxiv_expand[seed={seed.id}]: {len(candidates)} unique arXiv candidates "
            f"(llm_calls={llm_calls})"
        )
        if not candidates:
            continue

        # --- 获取 Semantic Scholar 引用数 ---
        cite_map: dict[str, int] = {}
        for c in candidates:
            axid = c["arxiv_id"]
            cite_map[axid] = await get_citation_count_by_arxiv(axid)

        # --- 过滤（OR 逻辑）：top_k ∪ cite ≥ min_cite ---
        sorted_cands = sorted(
            candidates,
            key=lambda c: cite_map.get(c["arxiv_id"], 0),
            reverse=True,
        )
        qualify_topk = {c["arxiv_id"] for c in sorted_cands[:top_k]}
        qualify_min = (
            {c["arxiv_id"] for c in candidates if cite_map.get(c["arxiv_id"], 0) >= min_cite}
            if min_cite > 0 else set()
        )
        final_ids = qualify_topk | qualify_min

        logger.info(
            f"arxiv_expand[seed={seed.id}]: top_k={top_k}→{len(qualify_topk)}, "
            f"min_cite={min_cite}→{len(qualify_min)}, download={len(final_ids)}"
        )

        # --- 下载 & 注册 ---
        for cand in sorted_cands:
            axid = cand["arxiv_id"]
            if axid not in final_ids:
                continue

            ext_id = f"arxiv:{axid}"
            pdf_url = cand["pdf_url"]
            title = cand.get("title") or axid

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

            # 下载 PDF
            local = await download_pdf(pdf_url)
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
                f"(arXiv:{axid}, cite={cite_map.get(axid, 0)})"
            )
            await on_paper_added(
                "paper_added",
                paper_id=p.id, title=p.title, level=p.level,
                source_paper_id=seed.id, total_expanded=total_added,
                is_new=True,
            )

    logger.info(f"arxiv_expand: done, total_added={total_added}, total_returned={len(added)}")
    return added
