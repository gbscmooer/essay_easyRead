"""
Stage 3 — chunk

对论文进行文本切片：
- 优先用浅分析的 outline 章节切片（粗粒度）
- 再用 PDF 全文滑窗切片（细粒度）
"""

import asyncio
from sqlmodel import Session, create_engine, select
from ....config import settings
from ....models.analysis import AnalysisResult
from ....models.paper import Paper
from ....services.pdf_parser import extract_pdf_content

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


async def stage_chunk(papers) -> list[dict]:
    """对所有论文进行文本切片"""
    chunks = []
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
                        "paper_id": p.id, "paper_title": p.title,
                        "section_name": sec.get("section", f"Section {i}"),
                        "chunk_index": i,
                        "page_hint": sec.get("page", ""),
                        "text": f"{sec.get('section', '')}: {sec.get('summary', '')}",
                    })

        # PDF 全文滑窗（放线程池避免阻塞事件循环）
        try:
            content = await asyncio.to_thread(extract_pdf_content, p.pdf_path)
            full = content.full_text or ""
            for j, ck in enumerate(_sliding_window(full)):
                chunks.append({
                    "id": f"{p.id}:body:{j}",
                    "paper_id": p.id, "paper_title": p.title,
                    "section_name": "Body",
                    "chunk_index": j, "page_hint": "",
                    "text": ck,
                })
        except Exception:
            continue

    return chunks
