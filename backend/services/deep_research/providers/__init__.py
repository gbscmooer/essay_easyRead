"""
Research provider 工厂
"""

import logging

from .base import ResearchProvider
from .openalex import OpenAlexProvider
from .semantic_scholar import SemanticScholarProvider
from .scopus import ScopusProvider
from .local import LocalProvider

logger = logging.getLogger(__name__)


def _elsevier_key(cfg: dict) -> str:
    """Elsevier API Key：优先 research.elsevier_api_key，其次环境变量。"""
    key = (cfg or {}).get("elsevier_api_key", "") or ""
    if not key:
        try:
            from ....config import settings
            key = settings.elsevier_api_key or ""
        except Exception:
            key = ""
    return (key or "").strip()


class FailoverProvider:
    """首选源（Scopus）鉴权熔断后自动切换备用源（OpenAlex）的包装。

    Elsevier Key 常与校园网 IP 绑定：在校外带着无效 Key 首选 Scopus 会让
    整条 expand 饿死。ScopusProvider 内部连续鉴权失败会置 _disabled；
    此后所有调用透明转发给 secondary，扩展照常进行。
    """

    def __init__(self, primary, secondary):
        self._primary = primary
        self._secondary = secondary
        self._active = primary

    @property
    def name(self) -> str:
        return self._active.name

    def _pick(self):
        if self._active is self._primary and getattr(self._primary, "_disabled", False):
            logger.warning(
                "research provider failover: %s → %s（Elsevier 鉴权/配额不可用）",
                self._primary.name, self._secondary.name,
            )
            self._active = self._secondary
        return self._active

    async def find_paper_id(self, title: str, doi: str = "", arxiv_id: str = ""):
        return await self._pick().find_paper_id(title, doi, arxiv_id)

    async def get_paper_references(self, paper_id: str, limit: int = 50) -> list[dict]:
        return await self._pick().get_paper_references(paper_id, limit=limit)

    async def get_paper_citations(self, paper_id: str, limit: int = 50) -> list:
        return await self._pick().get_paper_citations(paper_id, limit=limit)

    async def get_recommendations(self, paper_id: str, limit: int = 50) -> list:
        return await self._pick().get_recommendations(paper_id, limit=limit)

    async def search_works(self, query: str, limit: int = 20, **kwargs):
        hits = await self._pick().search_works(query, limit=limit, **kwargs)
        # 并行场景下多条查询可能同时落在刚熔断的 primary 上（_pick 在调用时才检查），
        # 各自拿到空结果但没人触发切换——这里兜底：primary 本次调用中死掉就改走备用源重试
        if not hits and self._active is self._primary and getattr(self._primary, "_disabled", False):
            logger.warning(
                "search_works: 首选源 %s 在本次调用中熔断，该检索式改走 %s 重试",
                self._primary.name, self._secondary.name,
            )
            self._active = self._secondary
            hits = await self._secondary.search_works(query, limit=limit, **kwargs)
        return hits

    async def aclose(self):
        # 显式关闭双方：__getattr__ 透传只会关活跃方，熔断切走后另一方的连接会泄漏
        for p in (self._primary, self._secondary):
            aclose = getattr(p, "aclose", None)
            if aclose:
                try:
                    await aclose()
                except Exception:
                    pass

    def __getattr__(self, item):
        # 少数调用点直接用具体 provider 的扩展方法（如 get_paper_metadata）
        return getattr(self._pick(), item)


def get_provider(cfg: dict) -> ResearchProvider:
    """
    根据配置返回 provider 实例。

    cfg 示例：
      {"provider": "auto", "elsevier_api_key": "xxx"}
      {"provider": "openalex", "email": "you@x.com"}
      {"provider": "semantic_scholar", "api_key": "xxx"}
      {"provider": "scopus", "elsevier_api_key": "xxx"}
      {"provider": "local"}

    provider 为 "auto"（默认）时的首选逻辑（2026-09-02 实测校准）：
    - 有 Elsevier API Key → **无条件 Scopus 首选**，鉴权失败/配额耗尽熔断回退 OpenAlex；
    - 无 Key → 直接 OpenAlex。Scopus Search API 只认 API Key（校园网机构 IP 不放行，
      机构 IP 直连只覆盖 ScienceDirect 全文下载通道），无 Key 硬冲只会白付 2×401。
    """
    name = (cfg or {}).get("provider", "auto").lower()
    if name in ("", "auto"):
        key = _elsevier_key(cfg or {})
        if key:
            logger.info("provider=auto → scopus 首选（检测到 Elsevier API Key；鉴权/配额失败自动回退 openalex）")
            return FailoverProvider(
                ScopusProvider(api_key=key),
                OpenAlexProvider(email=(cfg or {}).get("email", "")),
            )
        logger.info("provider=auto → openalex（无 Elsevier API Key；Scopus Search 只认 Key，机构 IP 不放行）")
        return OpenAlexProvider(email=(cfg or {}).get("email", ""))
    if name in ("scopus", "elsevier"):
        # 显式选 scopus：无 Key 也按用户意愿尝试（快速 401×2 熔断后回退 openalex），
        # 便于验证机构出口是否被 Elsevier 认权
        return FailoverProvider(
            ScopusProvider(api_key=_elsevier_key(cfg or {})),
            OpenAlexProvider(email=(cfg or {}).get("email", "")),
        )
    if name == "openalex":
        return OpenAlexProvider(email=(cfg or {}).get("email", ""))
    if name in ("semantic_scholar", "s2"):
        return SemanticScholarProvider(api_key=(cfg or {}).get("api_key", ""))
    if name in ("local", "none", "off"):
        return LocalProvider()
    logger.warning(f"Unknown research provider '{name}', falling back to local")
    return LocalProvider()


__all__ = [
    "get_provider", "ResearchProvider",
    "OpenAlexProvider", "SemanticScholarProvider", "ScopusProvider", "LocalProvider",
]
