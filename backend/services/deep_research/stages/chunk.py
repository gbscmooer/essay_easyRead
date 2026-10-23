"""
Stage 3 — chunk

对论文进行文本切片：
- 优先用浅分析的 outline 章节切片（粗粒度）
- 再用 PDF 全文滑窗切片（细粒度）
- download_status 非 available 或 PDF 不存在时跳过正文切片
"""

import asyncio
import os
from sqlmodel import Session, create_engine, select
from ....config import settings
from ....models.analysis import AnalysisResult
from ....models.paper import Paper
from ....services.pdf_parser import extract_pdf_content
from ....services.material_sources import file_digest, page_chunks

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})
    return _engine


def _sliding_window(text: str, chunk_size: int = 1200, overlap: int = 200):
    out = []
    i = 0
    while i < len(text):
        out.append(text[i:i + chunk_size])
        i += chunk_size - overlap
    return out


async def stage_chunk(papers, *, citation_contexts: dict[int, list[dict]] | None = None) -> list[dict]:
    """对所有论文进行文本切片，并附加引用上下文切片。"""
    chunks = []
    ctx_map = citation_contexts or {}
    by_id = {p.id: p for p in papers}

    for p in papers:
        # 优先用浅分析的 outline 章节切片
        outline_sections = []
        with Session(_get_engine()) as s:
            a = s.exec(select(AnalysisResult).where(AnalysisResult.paper_id == p.id)).first()
            if a and a.outline:
                outline_sections = a.outline

        if outline_sections:
            for i, sec in enumerate(outline_sections):
                if sec.get("summary"):
                    chunks.append({
                        "id": f"{p.id}:sec:{i}",
                        "source_kind": "analysis_summary",
                        "paper_id": p.id, "paper_title": p.title,
                        "section_name": sec.get("section", f"Section {i}"),
                        "chunk_index": i,
                        "page_hint": sec.get("page", ""),
                        "text": f"{sec.get('section', '')}: {sec.get('summary', '')}",
                    })

        # PDF 全文滑窗：仅当 download_status=available 且文件存在
        pdf_ok = (
            getattr(p, "download_status", "none") == "available"
            and p.pdf_path
            and os.path.isfile(p.pdf_path)
        )
        if not pdf_ok:
            continue

        try:
            content = await asyncio.to_thread(extract_pdf_content, p.pdf_path)
            digest = await asyncio.to_thread(file_digest, p.pdf_path)
            chunks.extend(page_chunks(p, content, digest))
        except Exception:
            continue

    # 引用上下文切片
    for seed_id, contexts in ctx_map.items():
        seed = by_id.get(seed_id)
        if not seed:
            continue
        for i, ctx in enumerate(contexts):
            section = ctx.get("section", "")
            context_text = ctx.get("context", "")
            if not context_text:
                continue
            intent = ctx.get("intent", "")
            prefix_parts = ["Citation Context"]
            if intent:
                prefix_parts.append(intent)
            if section:
                prefix_parts.append(section)
            text = f"[{' | '.join(prefix_parts)}] {context_text}"
            chunks.append({
                "id": f"{seed_id}:cite_ctx:{i}",
                "source_kind": "citation_context",
                "paper_id": seed_id,
                "paper_title": seed.title or "",
                "section_name": section or "Citation Context",
                "chunk_index": i,
                "page_hint": "",
                "text": text,
                "seed_paper_id": seed_id,
                "target_arxiv_id": ctx.get("arxiv_id"),
                "target_doi": ctx.get("doi"),
            })

    return chunks
