"""
Stage 2 — expand

联网增量：通过 Semantic Scholar 抽取 seed 论文的 references，
按引用数排序下载 Top-K，纳入工作区，并跑浅分析。
"""

import asyncio
import logging
from sqlmodel import Session, create_engine, select
from datetime import datetime, timezone

from ....config import settings
from ....models.paper import Paper
from ....models.workspace import WorkspacePaper
from ....models.schemas import StreamEvent
from ...semantic_scholar import find_s2_paper_id, get_paper_references
from ..pdf_downloader import download_pdf
from ...ai_analyzer import analyze_paper_stream
from ...ai_config import get_analysis_config
from ....services.pdf_parser import extract_pdf_content
from ...database import save_analysis_result, update_paper_metadata, update_paper_status

logger = logging.getLogger(__name__)

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})
    return _engine


SEM = asyncio.Semaphore(3)  # 并发下载上限


async def _download_and_register(ref: dict, parent: Paper, ws_id: int) -> Paper | None:
    if not ref.get("pdf_url"):
        return None
    async with SEM:
        local = await download_pdf(ref["pdf_url"])
    if not local:
        return None

    with Session(_get_engine()) as s:
        # 去重：external_id / DOI 已存在则跳过
        ext = ref.get("s2_id") or ref.get("doi") or ref.get("arxiv_id")
        if ext:
            existing = s.exec(select(Paper).where(Paper.external_id == ext)).first()
            if existing:
                # 已存在，仅确保挂在该工作区
                wp = s.exec(select(WorkspacePaper)
                            .where(WorkspacePaper.workspace_id == ws_id)
                            .where(WorkspacePaper.paper_id == existing.id)).first()
                if not wp:
                    s.add(WorkspacePaper(workspace_id=ws_id, paper_id=existing.id))
                    s.commit()
                return None

        p = Paper(
            filename=f"{ref['title'][:60]}.pdf",
            pdf_path=local,
            file_size=0,
            title=ref.get("title"),
            year=ref.get("year"),
            status="pending",
            source="expanded",
            parent_paper_id=parent.id,
            external_id=ext,
        )
        s.add(p)
        s.commit()
        s.refresh(p)
        s.add(WorkspacePaper(workspace_id=ws_id, paper_id=p.id))
        s.commit()
        return p


async def _shallow_analyze(paper: Paper):
    """对 expanded 论文跑一遍浅分析（复用 analyze_paper_stream）"""
    cfg = get_analysis_config()
    try:
        with Session(_get_engine()) as s:
            update_paper_status(s, paper.id, "extracting")
        content = await asyncio.to_thread(extract_pdf_content, paper.pdf_path)

        result = {}
        async for evt in analyze_paper_stream(
            content.full_text, cfg["api_key"], cfg["base_url"], cfg["model"],
            temperature=cfg.get("temperature", 0.1),
            max_tokens=cfg.get("max_tokens", 16384),
        ):
            if evt.event == "field" and evt.field_name:
                if evt.field_name == "mermaid_chart":
                    result.setdefault("method", {})["mermaid_chart"] = evt.data.get("value")
                else:
                    result[evt.field_name] = evt.data.get("value")

        for k, dft in [("title", paper.title or ""), ("venue", "Unknown"), ("year", paper.year or 0),
                       ("one_sentence", ""), ("problem", ""), ("key_contributions", []),
                       ("outline", []), ("method", {"overview": "", "pipeline": "", "key_modules": []}),
                       ("experiments", {"datasets": [], "baselines": [], "main_results": "",
                                        "ablation": "", "result_table": [], "figures": []}),
                       ("code_mapping", []), ("interview_qa", []), ("limitations", ""),
                       ("related_papers", [])]:
            result.setdefault(k, dft)

        with Session(_get_engine()) as s:
            update_paper_metadata(s, paper.id, result["title"], result["venue"], result["year"])
            save_analysis_result(s, paper.id, result)
            update_paper_status(s, paper.id, "done")
    except Exception as e:
        logger.warning(f"Shallow analyze failed for paper {paper.id}: {e}")
        with Session(_get_engine()) as s:
            update_paper_status(s, paper.id, "failed", str(e))


async def stage_expand(ws_id: int, seeds: list, params: dict, on_paper_added) -> list:
    """联网增量：对每篇 seed 论文查找引用，下载 Top-K 开放获取 PDF，跑浅分析"""
    top_k = int(params.get("expand_top_k", 5))
    total_limit = int(params.get("expand_total_limit", 50))

    expanded: list[Paper] = []
    expanded_count = 0

    for seed in seeds:
        if expanded_count >= total_limit:
            break
        s2_id = await find_s2_paper_id(
            title=seed.title or seed.filename,
            doi="",
            arxiv_id="",
        )
        if not s2_id:
            continue
        refs = await get_paper_references(s2_id, limit=top_k * 3)
        # 排序：有 pdf_url 优先，再按 citationCount 降序
        refs = [r for r in refs if r.get("pdf_url")]
        refs.sort(key=lambda r: r.get("citation_count") or 0, reverse=True)

        added_for_seed = 0
        for ref in refs:
            if added_for_seed >= top_k or expanded_count >= total_limit:
                break
            p = await _download_and_register(ref, seed, ws_id)
            if p:
                expanded.append(p)
                expanded_count += 1
                added_for_seed += 1
                await on_paper_added(
                    "paper_added",
                    paper_id=p.id, title=p.title,
                    source_paper_id=seed.id, total_expanded=expanded_count,
                )

    # 并发跑浅分析
    if expanded:
        await asyncio.gather(*[_shallow_analyze(p) for p in expanded])

    # 重新查询，只保留 status=done 的扩展论文
    if expanded:
        with Session(_get_engine()) as s:
            done_ids = {p.id for p in s.exec(
                select(Paper).where(Paper.id.in_([p.id for p in expanded]))
                .where(Paper.status == "done")
            ).all()}
        expanded = [p for p in expanded if p.id in done_ids]

    return seeds + expanded
