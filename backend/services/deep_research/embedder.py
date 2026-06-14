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


def _est_tokens(s: str) -> int:
    """粗略 token 估算：CJK 字 ≈ 1 token，其余 ≈ 4 字符/token。"""
    cjk = sum(1 for c in s if "一" <= c <= "鿿")
    return cjk + (len(s) - cjk + 3) // 4


def _truncate_to_tokens(s: str, max_tokens: int) -> str:
    """按估算 token 把文本截断到 max_tokens 以内（fast-path：未超限原样返回）。

    比纯字符截断更准：同一阈值下英文（~4 字/token）能整段保留，中文（~1 字/token）
    才会被收敛——避免"为兼容中文把英文 chunk 也腰斩"。
    """
    if max_tokens <= 0 or _est_tokens(s) <= max_tokens:
        return s
    tok = noncjk = 0
    for i, c in enumerate(s):
        if "一" <= c <= "鿿":
            tok += 1
        else:
            noncjk += 1
            if noncjk % 4 == 1:
                tok += 1
        if tok > max_tokens:
            return s[:i]
    return s


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
    批量 400 时自动降级为逐条发送，跳过不合法的单条输入。
    """
    if not cfg.get("api_key"):
        raise RuntimeError(f"{cfg['provider']} embedding 需要在配置中填写 api_key")

    # 截断过长输入：远程 embedding 模型有 max token 限制，超长会被服务端拒为 400
    # （SiliconFlow code 20015）。按 **token 估算** 截断（见 _truncate_to_tokens）——同一
    # 阈值下英文 chunk（1200 字≈300 token）整段保留、中文长文本才收敛，避免纯字符截断
    # "为兼容中文把英文也腰斩"。默认 7000，适配 bge-m3 的 8192 上下文；若换回 512-token
    # 模型（如 bge-large-en-v1.5），把 embedding.max_input_tokens 调到 ~480。
    # 空串也可能触发 400，置为单空格兜底。本地 sentence-transformers 自行截断，仅远程路径处理。
    max_tokens = int(cfg.get("max_input_tokens") or 7000)
    hard_chars = int(cfg.get("max_input_chars") or 0)  # 可选硬字符上限（0=不启用）
    safe_texts, n_trunc = [], 0
    for t in texts:
        t = t or ""
        orig_len = len(t)
        t = _truncate_to_tokens(t, max_tokens)
        if hard_chars and len(t) > hard_chars:
            t = t[:hard_chars]
        if len(t) < orig_len:
            n_trunc += 1
        safe_texts.append(t if t.strip() else " ")
    if n_trunc:
        logger.info(f"embedding: 已按 ~{max_tokens} token 上限截断 {n_trunc}/{len(texts)} 条超长输入（避免 400）")

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(
                    f"{cfg['base_url'].rstrip('/')}/embeddings",
                    headers={"Authorization": f"Bearer {cfg['api_key']}"},
                    json={"model": cfg["model"], "input": safe_texts},
                )
                r.raise_for_status()
                data = r.json()["data"]
                return [d["embedding"] for d in data]
        except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError) as e:
            last_err = e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400 and len(safe_texts) > 1:
                # 批量 400：可能是某条文本导致，降级为逐条发送
                logger.warning(
                    f"embedding batch 400 ({e.response.text[:200]})，降级为逐条发送 {len(safe_texts)} 条"
                )
                return await _embed_one_by_one(safe_texts, cfg)
            # 其他 4xx（鉴权/参数错误）不重试；仅 5xx 服务端错误重试
            if e.response.status_code < 500:
                logger.error(
                    f"embedding API {e.response.status_code}: {e.response.text[:500]}"
                )
                raise
            last_err = e
        if attempt < max_retries - 1:
            # SSL/代理瞬态错误用更长的等待（给代理恢复时间）
            is_ssl = "ssl" in str(last_err).lower() or "certificate" in str(last_err).lower()
            delay = (3 + attempt * 2) if is_ssl else 2 ** attempt  # SSL: 3s,5s,7s,9s; 其他: 1s,2s,4s,8s
            logger.warning(
                f"embedding 请求失败（{type(last_err).__name__}: "
                f"{str(last_err)[:120]}），{delay}s 后重试 "
                f"({attempt + 1}/{max_retries})"
            )
            await asyncio.sleep(delay)
    raise last_err  # type: ignore[misc]


async def _embed_one_by_one(texts: list[str], cfg) -> list[list[float]]:
    """逐条发送 embedding 请求（批量 400 降级路径）。

    单条仍然 400 的文本用零向量占位并 warning，避免整批失败。
    """
    results: list[list[float]] = []
    dim: int | None = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        for i, t in enumerate(texts):
            try:
                r = await client.post(
                    f"{cfg['base_url'].rstrip('/')}/embeddings",
                    headers={"Authorization": f"Bearer {cfg['api_key']}"},
                    json={"model": cfg["model"], "input": [t]},
                )
                r.raise_for_status()
                vec = r.json()["data"][0]["embedding"]
                if dim is None:
                    dim = len(vec)
                results.append(vec)
            except Exception as e:
                logger.warning(f"embedding 单条 #{i} 失败（{e}），用零向量占位")
                if dim is None:
                    # 还没拿到维度，先尝试一条空格拿维度
                    try:
                        r2 = await client.post(
                            f"{cfg['base_url'].rstrip('/')}/embeddings",
                            headers={"Authorization": f"Bearer {cfg['api_key']}"},
                            json={"model": cfg["model"], "input": [" "]},
                        )
                        r2.raise_for_status()
                        dim = len(r2.json()["data"][0]["embedding"])
                    except Exception:
                        dim = 1024  # bge-large-en-v1.5 默认维度
                results.append([0.0] * dim)
    return results
