"""
Embedder — 多 provider 向量嵌入

支持本地 BGE-M3 (sentence-transformers)、OpenAI 和 SiliconFlow（硅基流动）。
根据 ai_config.json 中的 embedding 配置自动选择。
"""

import asyncio
import logging
import httpx
from typing import Optional
from ..ai_config import get_embedding_config

logger = logging.getLogger(__name__)

_model = None  # 本地模型单例（lazy）
_model_lock = asyncio.Lock()


async def _get_local_model():
    global _model
    async with _model_lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer
            cfg = get_embedding_config()
            device = cfg.get("device", "auto")
            if device == "auto":
                import torch
                device = "cuda" if torch.cuda.is_available() else (
                    "mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cpu"
                )
            logger.info(f"Loading {cfg['model']} on {device}...")
            _model = SentenceTransformer(cfg["model"], device=device)
        return _model


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """对文本列表进行向量嵌入"""
    cfg = get_embedding_config()
    provider = cfg["provider"]
    if provider in ("openai", "siliconflow"):
        return await _embed_openai_compatible(texts, cfg)
    return await _embed_local(texts, cfg)


async def embed_query(text: str) -> list[float]:
    """对单条查询文本进行向量嵌入"""
    vecs = await embed_texts([text])
    return vecs[0]


async def _embed_local(texts, cfg):
    model = await _get_local_model()
    bs = cfg.get("batch_size", 32)
    # encode 是 CPU/GPU bound，放线程池
    return await asyncio.to_thread(
        lambda: model.encode(texts, batch_size=bs, normalize_embeddings=True).tolist()
    )


async def _embed_openai_compatible(texts, cfg, max_retries: int = 3):
    """OpenAI 兼容 API（OpenAI / SiliconFlow 等）。

    对瞬时网络抖动（ConnectError/TimeoutException）和 5xx 做指数退避重试，
    避免一次短暂断网就让整个深度研究 run 失败。
    """
    if not cfg.get("api_key"):
        raise RuntimeError(f"{cfg['provider']} embedding 需要在配置中填写 api_key")
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(
                    f"{cfg['base_url'].rstrip('/')}/embeddings",
                    headers={"Authorization": f"Bearer {cfg['api_key']}"},
                    json={"model": cfg["model"], "input": texts},
                )
                r.raise_for_status()
                data = r.json()["data"]
                return [d["embedding"] for d in data]
        except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError) as e:
            last_err = e
        except httpx.HTTPStatusError as e:
            # 4xx（鉴权/参数错误）不重试；仅 5xx 服务端错误重试
            if e.response.status_code < 500:
                raise
            last_err = e
        if attempt < max_retries - 1:
            delay = 2 ** attempt  # 1s, 2s, 4s
            logger.warning(
                f"embedding 请求失败（{type(last_err).__name__}），{delay}s 后重试 "
                f"({attempt + 1}/{max_retries})"
            )
            await asyncio.sleep(delay)
    raise last_err  # type: ignore[misc]
