"""
Stage 4 — embed

对文本切片进行向量嵌入并写入 ChromaDB。
"""

import logging
from typing import AsyncIterator
from ..embedder import embed_texts
from ..vector_store import (
    upsert_chunks, drop_workspace_collection, get_or_create,
    get_existing_ids, collection_model, list_workspace_chunks,
)
from ...ai_config import get_embedding_config

logger = logging.getLogger(__name__)

BATCH = 16


async def stage_embed(ws_id: int, chunks: list[dict], rebuild: bool) -> AsyncIterator[int]:
    """分批嵌入文本切片并 upsert 到 ChromaDB，yield 已完成数量。

    增量复用：rebuild=False 且 embedding 模型未变时，跳过已嵌入的 chunk（id 已存在），
      只嵌入新增的，省下重复的远程 API 调用。
    模型变更自愈：集合记录的 embedding 模型与当前不一致时自动整库重建——避免新旧模型
      向量混进同一 collection（常同为 1024 维，不报维度错而静默腐烂相似度）。
    """
    model = get_embedding_config().get("model", "") or ""

    prev_model = collection_model(ws_id)
    model_changed = bool(prev_model) and prev_model != model
    full_rebuild = rebuild or model_changed
    if model_changed and not rebuild:
        logger.warning(f"embedding 模型由 {prev_model!r} 变为 {model!r}，自动重建向量库以避免新旧向量混用")
    if full_rebuild:
        previous = list_workspace_chunks(ws_id, strict=True)
        chunks = list({c["id"]: c for c in [*previous, *chunks]}.values())
    # Finish all model calls before discarding the usable old collection.
    rebuilt_batches = []
    if full_rebuild:
        for i in range(0, len(chunks), BATCH):
            batch = chunks[i:i + BATCH]
            rebuilt_batches.append(await embed_texts([c["text"] for c in batch]))
        drop_workspace_collection(ws_id)

    # 触发集合创建并写入模型指纹（集合已存在则保留原指纹）
    get_or_create(ws_id, embed_model=model)

    existing = set() if full_rebuild else get_existing_ids(ws_id)
    todo = [c for c in chunks if c["id"] not in existing]
    reused = len(chunks) - len(todo)
    if reused:
        logger.info(f"embed: 复用 {reused} 个已嵌入 chunk，仅嵌入 {len(todo)} 个新增/变更")

    done = reused
    if done:
        yield done
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        vecs = rebuilt_batches[i // BATCH] if full_rebuild else await embed_texts([c["text"] for c in batch])
        upsert_chunks(
            ws_id,
            ids=[c["id"] for c in batch],
            docs=[c["text"] for c in batch],
            embeddings=vecs,
            metas=[{
                "chunk_id": c["id"],
                "paper_id": c["paper_id"], "paper_title": c["paper_title"],
                "section_name": c["section_name"], "chunk_index": c["chunk_index"],
                "page_hint": c["page_hint"],
                **({"seed_paper_id": c["seed_paper_id"]} if c.get("seed_paper_id") else {}),
                **({"target_arxiv_id": c["target_arxiv_id"]} if c.get("target_arxiv_id") else {}),
                **({"target_doi": c["target_doi"]} if c.get("target_doi") else {}),
            } for c in batch],
        )
        done += len(batch)
        if not full_rebuild:
            yield done
    if full_rebuild:
        yield done
