"""
Vector Store — ChromaDB 封装

每个工作区独立 collection，持久化到 settings.resolved_chroma_dir。
"""

import logging
from typing import Optional
import chromadb
from chromadb.config import Settings as ChromaSettings
from ...config import settings

logger = logging.getLogger(__name__)
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(
            path=settings.resolved_chroma_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
    return _client


def _collection_name(ws_id: int) -> str:
    return f"ws_{ws_id}"


def get_or_create(ws_id: int):
    return _get_client().get_or_create_collection(
        name=_collection_name(ws_id),
        metadata={"hnsw:space": "cosine"},
    )


def upsert_chunks(ws_id: int, ids: list[str], docs: list[str], embeddings: list[list[float]], metas: list[dict]):
    col = get_or_create(ws_id)
    col.upsert(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)


def delete_paper(ws_id: int, paper_id: int):
    col = get_or_create(ws_id)
    col.delete(where={"paper_id": paper_id})


def query_workspace(ws_id: int, vec: list[float], top_k: int = 5, paper_ids: Optional[list[int]] = None) -> list[dict]:
    col = get_or_create(ws_id)
    where = {"paper_id": {"$in": paper_ids}} if paper_ids else None
    res = col.query(query_embeddings=[vec], n_results=top_k, where=where,
                    include=["documents", "metadatas", "distances"])
    out = []
    for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        out.append({
            **meta,
            "snippet": doc,
            "score": 1.0 - float(dist),   # cosine distance → similarity
        })
    return out


def drop_workspace_collection(ws_id: int):
    name = _collection_name(ws_id)
    try:
        _get_client().delete_collection(name)
    except Exception as e:
        logger.warning(f"delete_collection failed: {e}")
