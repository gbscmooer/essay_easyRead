"""
Reranker — 对向量检索结果做语义重排序

SiliconFlow / 其他 OpenAI 兼容厂商的 /v1/rerank 端点。
默认模型 BAAI/bge-reranker-v2-m3。

调用流程：
  vector_store.query_workspace(top_k=N_BIG)  # 例如 30
  → reranker.rerank(query, snippets, top_n=N_SMALL)  # 例如 5
  → 把 top_n 喂给 LLM 生成回答

返回每条文档的 relevance_score（越大越相关），并附原索引。
"""

import logging
import math
import httpx
from ..ai_config import get_reranker_config

logger = logging.getLogger(__name__)


async def rerank(query: str, documents: list[str], top_n: int | None = None) -> list[dict]:
    """对 documents 按与 query 的相关性重排序。

    返回:
      [{"index": 原始下标, "score": float, "document": 文本}, ...]
      按 score 降序，长度 <= top_n。

    如果未启用 / 配置缺失 / 请求失败：回退到原顺序（带 0.0 分数），不抛异常，
    上层因此始终能拿到 top_n 条结果。
    """
    if not documents:
        return []

    cfg = get_reranker_config()
    if not cfg.get("enabled", True):
        return _fallback(documents, top_n)

    api_key = cfg.get("api_key", "").strip()
    base_url = (cfg.get("base_url") or "").rstrip("/")
    model = cfg.get("model") or "BAAI/bge-reranker-v2-m3"

    if not api_key or not base_url:
        logger.warning("Reranker enabled but api_key/base_url missing — falling back")
        return _fallback(documents, top_n)

    payload = {
        "model": model,
        "query": query,
        "documents": documents,
        "top_n": top_n if top_n is not None else len(documents),
        "return_documents": False,  # 我们已经有原文，省带宽
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{base_url}/rerank",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.warning(f"Rerank request failed ({e}) — falling back to vector order")
        return _fallback(documents, top_n)

    # SiliconFlow 响应格式：
    #   { "results": [ {"index": 0, "relevance_score": 0.98}, ... ] }
    results = data.get("results") or []
    if not results:
        return _fallback(documents, top_n)

    out: list[dict] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0 or idx >= len(documents):
            continue
        try:
            score = float(item.get("relevance_score") or 0.0)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(score):
            continue
        out.append({
            "index": idx,
            "score": score,
            "document": documents[idx],
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    if top_n is not None:
        out = out[:top_n]
    return out


def _fallback(documents: list[str], top_n: int | None) -> list[dict]:
    n = len(documents) if top_n is None else min(top_n, len(documents))
    return [
        {"index": i, "score": 0.0, "document": documents[i], "fallback": True}
        for i in range(n)
    ]
