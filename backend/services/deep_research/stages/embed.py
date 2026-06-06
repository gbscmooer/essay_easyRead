"""
Stage 4 — embed

对文本切片进行向量嵌入并写入 ChromaDB。
"""

from typing import AsyncIterator
from ..embedder import embed_texts
from ..vector_store import upsert_chunks, drop_workspace_collection

BATCH = 32


async def stage_embed(ws_id: int, chunks: list[dict], rebuild: bool) -> AsyncIterator[int]:
    """分批嵌入文本切片并 upsert 到 ChromaDB，yield 已完成数量"""
    if rebuild:
        drop_workspace_collection(ws_id)

    done = 0
    for i in range(0, len(chunks), BATCH):
        batch = chunks[i:i + BATCH]
        vecs = await embed_texts([c["text"] for c in batch])
        upsert_chunks(
            ws_id,
            ids=[c["id"] for c in batch],
            docs=[c["text"] for c in batch],
            embeddings=vecs,
            metas=[{
                "paper_id": c["paper_id"], "paper_title": c["paper_title"],
                "section_name": c["section_name"], "chunk_index": c["chunk_index"],
                "page_hint": c["page_hint"],
            } for c in batch],
        )
        done += len(batch)
        yield done
