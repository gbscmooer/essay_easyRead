"""
Research provider 工厂
"""

import logging

from .base import ResearchProvider
from .openalex import OpenAlexProvider
from .semantic_scholar import SemanticScholarProvider
from .local import LocalProvider

logger = logging.getLogger(__name__)


def get_provider(cfg: dict) -> ResearchProvider:
    """
    根据配置返回 provider 实例。

    cfg 示例：
      {"provider": "openalex", "email": "you@x.com"}
      {"provider": "semantic_scholar", "api_key": "xxx"}
      {"provider": "local"}
    """
    name = (cfg or {}).get("provider", "openalex").lower()
    if name == "openalex":
        return OpenAlexProvider(email=(cfg or {}).get("email", ""))
    if name in ("semantic_scholar", "s2"):
        return SemanticScholarProvider(api_key=(cfg or {}).get("api_key", ""))
    if name in ("local", "none", "off"):
        return LocalProvider()
    logger.warning(f"Unknown research provider '{name}', falling back to local")
    return LocalProvider()


__all__ = ["get_provider", "ResearchProvider", "OpenAlexProvider", "SemanticScholarProvider", "LocalProvider"]
