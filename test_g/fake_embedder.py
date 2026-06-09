"""
确定性假 embedder — 离线、零依赖、可复现

把文本切词后哈希到固定维度的词袋向量。性质：
- 共享词越多 → cosine 越高（足以驱动 purpose/method 语义 facet 的相对排序）
- 完全确定性，不联网、不加载模型 → 沙盒结果每次一致，便于调参对比

签名与生产 embed_texts 一致：async (list[str]) -> list[list[float]]，可直接互换。
"""

from __future__ import annotations

import hashlib
import math
import re

DIM = 128


def _tokenize(text: str) -> list[str]:
    """中文无词边界，整句会被切成一个巨型 token → 改为：ASCII 按词 + 中文按字符 bigram，
    这样'异常检测'能在不同论文间共享 bigram(异常/常检/检测)，cosine 才反映主题重叠。"""
    text = str(text).lower()
    toks = [w for w in re.findall(r"[a-z0-9]+", text) if len(w) > 1]
    cjk = re.findall(r"[一-鿿]", text)
    toks.extend(cjk)  # 单字
    toks.extend(cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1))  # bigram
    return toks


def _stable_hash(tok: str) -> int:
    # 用 md5 取稳定整数：不受 PYTHONHASHSEED 影响，跨进程/跨运行一致
    return int.from_bytes(hashlib.md5(tok.encode("utf-8")).digest()[:4], "big")


def _embed_one(text: str) -> list[float]:
    vec = [0.0] * DIM
    for tok in _tokenize(text):
        vec[_stable_hash(tok) % DIM] += 1.0
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


async def fake_embed(texts: list[str]) -> list[list[float]]:
    return [_embed_one(t) for t in texts]
