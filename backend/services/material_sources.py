"""Bind factual excerpts to current PDF pages; reject derived/stale index text."""

import asyncio
import hashlib
from pathlib import Path
from sqlmodel import Session
from ..models.paper import Paper
from .database import get_engine
from .pdf_parser import extract_pdf_content


def file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def page_chunks(paper, content, document_hash, size=1200, overlap=200):
    chunks = []
    for page, text in enumerate(content.page_texts, 1):
        for start in range(0, len(text), size - overlap):
            excerpt = text[start : start + size]
            if not excerpt.strip():
                continue
            chunks.append(
                {
                    "id": f"{paper.id}:pdf:{document_hash[:16]}:{page}:{start}",
                    "paper_id": paper.id,
                    "paper_title": paper.title or "",
                    "section_name": "Body",
                    "chunk_index": len(chunks),
                    "page_hint": str(page),
                    "page_number": page,
                    "start_char": start,
                    "end_char": start + len(excerpt),
                    "document_hash": document_hash,
                    "source_kind": "pdf_original",
                    "text": excerpt,
                }
            )
    return chunks


async def verified_pdf_hits(hits):
    """Read each file once and match exact excerpts; never infer provenance from download status."""
    with Session(get_engine()) as session:
        papers = {
            pid: session.get(Paper, pid) for pid in {int(h["paper_id"]) for h in hits}
        }
    documents = {}
    for pid, paper in papers.items():
        if not paper or not paper.pdf_path or not Path(paper.pdf_path).is_file():
            continue
        try:
            digest = await asyncio.to_thread(file_digest, paper.pdf_path)
            content = await asyncio.to_thread(extract_pdf_content, paper.pdf_path)
            # Detect a concurrent replacement; stale positions cannot leave this boundary.
            if digest != await asyncio.to_thread(file_digest, paper.pdf_path):
                continue
            documents[pid] = (digest, content.page_texts)
        except Exception:
            continue
    out = []
    for hit in hits:
        pid = int(hit["paper_id"])
        if pid not in documents or hit.get("source_kind") in {
            "analysis_summary",
            "citation_context",
        }:
            continue
        digest, pages = documents[pid]
        if hit.get("document_hash") and hit["document_hash"] != digest:
            continue
        excerpt = str(hit.get("snippet") or "")
        if not excerpt.strip():
            continue
        number = hit.get("page_number")
        start = hit.get("start_char")
        end = hit.get("end_char")
        if any(value is not None for value in (number, start, end)):
            if not (
                type(number) is int
                and type(start) is int
                and type(end) is int
                and 1 <= number <= len(pages)
                and 0 <= start < end <= len(pages[number - 1])
                and pages[number - 1][start:end] == excerpt
            ):
                continue
        else:
            # Legacy chunks require one unambiguous exact location in the original.
            locations = []
            for page, text in enumerate(pages, 1):
                offset = text.find(excerpt)
                if offset >= 0:
                    locations.append((page, offset))
                    if text.find(excerpt, offset + 1) >= 0:
                        locations.append((page, offset + 1))
            if len(locations) != 1:
                continue
            number, start = locations[0]
            end = start + len(excerpt)
        out.append(
            {
                **hit,
                "paper_title": papers[pid].title or "",
                "snippet": excerpt,
                "source_kind": "pdf_original",
                "document_hash": digest,
                "page_number": number,
                "start_char": start,
                "end_char": end,
                "download_status": papers[pid].download_status or "none",
            }
        )
    return out


async def document_versions_match(hits):
    with Session(get_engine()) as session:
        papers = {
            int(h["paper_id"]): session.get(Paper, int(h["paper_id"])) for h in hits
        }
    for pid, paper in papers.items():
        if not paper or not paper.pdf_path:
            return False
        try:
            digest = await asyncio.to_thread(file_digest, paper.pdf_path)
        except OSError:
            return False
        if any(
            h.get("document_hash") != digest for h in hits if int(h["paper_id"]) == pid
        ):
            return False
    return True
