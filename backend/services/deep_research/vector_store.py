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


def get_or_create(ws_id: int, embed_model: Optional[str] = None):
    # embed_model 仅在「集合首次创建」时写入 metadata 作为指纹；集合已存在时 Chroma 会忽略
    # 此处 metadata（保留原值）。stage_embed 据此检测"换了 embedding 模型"并自动重建，
    # 避免新旧模型向量混进同一 collection（两者常同为 1024 维，不会触发维度报错而静默腐烂）。
    meta = {"hnsw:space": "cosine"}
    if embed_model:
        meta["embed_model"] = embed_model
    return _get_client().get_or_create_collection(
        name=_collection_name(ws_id),
        metadata=meta,
    )


def get_existing_ids(ws_id: int) -> set:
    """集合中已存在的 chunk id 集合（增量嵌入用：跳过已嵌入的 chunk）。失败回退空集（=全量重嵌，安全）。"""
    try:
        col = get_or_create(ws_id)
        return set(col.get(include=[]).get("ids") or [])
    except Exception as e:
        logger.warning(f"get_existing_ids failed: {e}")
        return set()


def collection_model(ws_id: int) -> Optional[str]:
    """集合创建时记录的 embedding 模型指纹；用于检测模型变更触发自动重建。

    关键：用 get_collection（**不创建**）。若用 get_or_create 会在集合不存在时先建一个
    无指纹的空集，导致随后 get_or_create(embed_model=...) 因"集合已存在"写不进指纹。
    集合不存在 / 读取失败 → None。
    """
    try:
        col = _get_client().get_collection(_collection_name(ws_id))
        return (col.metadata or {}).get("embed_model")
    except Exception:
        return None


def workspace_index_available(ws_id: int) -> bool:
    """Return whether a non-empty index exists without creating a collection.

    Missing/unreadable collections are treated as empty so read-only callers can
    fail before loading an embedding model or mutating Chroma state.
    """
    try:
        col = _get_client().get_collection(_collection_name(ws_id))
        return int(col.count()) > 0
    except Exception:
        return False


def upsert_chunks(ws_id: int, ids: list[str], docs: list[str], embeddings: list[list[float]], metas: list[dict]):
    col = get_or_create(ws_id)
    col.upsert(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)


def delete_paper(ws_id: int, paper_id: int):
    col = get_or_create(ws_id)
    col.delete(where={"paper_id": paper_id})


def list_workspace_chunks(ws_id: int, paper_ids: Optional[list[int]] = None, *, strict: bool = False) -> list[dict]:
    """列出工作区全部 chunk（供 BM25 等词法索引构建）。失败返回空列表。"""
    try:
        col = _get_client().get_collection(_collection_name(ws_id))
        where = {"paper_id": {"$in": paper_ids}} if paper_ids else None
        res = col.get(where=where, include=["documents", "metadatas"])
        ids = res.get("ids") or []
        docs = res.get("documents") or []
        metas = res.get("metadatas") or []
        out: list[dict] = []
        for cid, doc, meta in zip(ids, docs, metas):
            out.append({
                "id": cid,
                **(meta or {}),
                "text": doc or "",
            })
        return out
    except Exception as e:
        logger.warning(f"list_workspace_chunks failed: {e}")
        if strict and not isinstance(e, chromadb.errors.NotFoundError):
            raise
        return []


def query_workspace(ws_id: int, vec: list[float], top_k: int = 5, paper_ids: Optional[list[int]] = None) -> list[dict]:
    # Query is read-only: a missing collection must not create persistent state.
    col = _get_client().get_collection(_collection_name(ws_id))
    where = {"paper_id": {"$in": paper_ids}} if paper_ids else None
    res = col.query(query_embeddings=[vec], n_results=top_k, where=where,
                    include=["documents", "metadatas", "distances"])
    out = []
    for cid, doc, meta, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]):
        out.append({
            **(meta or {}),
            "chunk_id": cid,
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
