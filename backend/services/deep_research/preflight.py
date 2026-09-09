"""数据源预检（preflight）——调查启动前用带权限的最小真实请求体检每个源。

要解决的问题是「查询之后才报告 403」：Elsevier Key 与校园网出口绑定、配额
档位变动、网络切换，这类确定性失败如果只在正式检索时暴露，用户已经等了
拆解→检索两步才看到报错。预检在 stage 0 就用真实 Key 发一次最小检索
（Scopus Search count=1 / OpenAlex per-page=1），把 401/403/断网提前推到
眼前：主源判死则提前熔断省掉逐通道试错，全部判死则快速失败不再空跑。

状态口径（不做 ping 可达性判断——探测必须打到需要权限的真实接口）：
  ok            带权限的最小检索成功
  auth_error    401/403：Key 无效、权限档不足或出口 IP 不在订阅范围（确定性失败）
  rate_limited  429：权限仍在，只是限速/配额窗口，不提前熔断
  network_error 连接失败/超时
  unconfigured  该源缺必要配置（如无 Key 的 Scopus），本来就不会被启用
"""

import asyncio
import hashlib
import logging
import time
import weakref
from copy import deepcopy

logger = logging.getLogger(__name__)

# 结果短缓存：页面往返、误触双击不重复烧配额；改配置后最多等一个 TTL 才生效
HEALTH_TTL = 20.0
_cache: dict[str, tuple[float, list[dict]]] = {}
_locks = weakref.WeakKeyDictionary()

# 判死口径：auth_error / network_error / error / unconfigured 都是「这次运行
# 别再用它」；rate_limited 例外——权限没丢，提前熔断只会在限速窗口里错杀
_DEAD_STATUSES = {"auth_error", "network_error", "error", "unconfigured"}


def dead_source_names(healths: list[dict]) -> set[str]:
    return {h["name"] for h in healths if h.get("status") in _DEAD_STATUSES}


def any_source_ok(healths: list[dict]) -> bool:
    return any(h.get("status") == "ok" for h in healths)


async def preflight_research_sources(cfg: dict | None = None, *, use_cache: bool = True) -> list[dict]:
    loop = asyncio.get_running_loop()
    if loop not in _locks:
        _locks[loop] = asyncio.Lock()
    async with _locks[loop]:
        return await _preflight_research_sources(cfg, use_cache=use_cache)


async def _preflight_research_sources(cfg: dict | None = None, *, use_cache: bool = True) -> list[dict]:
    """按当前研究配置体检数据源，返回 [{name, role, status, detail, latency_ms, http_status}]。

    role=primary 是首选源（auto+Key 时为 Scopus），role=fallback 是熔断回退源。
    探测与 providers.get_provider 的选源逻辑同构：预检探测的就是本次运行真
    正会使用的源组合，而不是固定的全源清单。探测并发发射、单个 12s 超时。
    """
    from ..ai_config import get_research_config
    from .providers import (
        LocalProvider, OpenAlexProvider, ScopusProvider,
        SemanticScholarProvider, _elsevier_key,
    )

    cfg = dict(cfg if cfg is not None else get_research_config())
    provider_name = str(cfg.get("provider") or "auto").lower()
    key = _elsevier_key(cfg)
    email = str(cfg.get("email") or "").strip()

    cache_key = hashlib.sha256(f"{provider_name}|{key}|{email}|{cfg.get('api_key', '')}".encode()).hexdigest()
    now = time.monotonic()
    if use_cache:
        hit = _cache.get(cache_key)
        if hit and now - hit[0] < HEALTH_TTL:
            return deepcopy(hit[1])

    def scopus() -> ScopusProvider:
        return ScopusProvider(api_key=key)

    def openalex() -> OpenAlexProvider:
        return OpenAlexProvider(email=email)

    if provider_name in ("", "auto"):
        targets = [(scopus, "primary"), (openalex, "fallback")] if key else [(openalex, "primary")]
    elif provider_name in ("scopus", "elsevier"):
        targets = [(scopus, "primary"), (openalex, "fallback")]
    elif provider_name == "openalex":
        targets = [(openalex, "primary")]
    elif provider_name in ("semantic_scholar", "s2"):
        targets = [(lambda: SemanticScholarProvider(api_key=str(cfg.get("api_key") or "")), "primary")]
    else:
        targets = [(LocalProvider, "primary")]

    async def _probe(factory, role: str) -> dict:
        provider = factory()
        try:
            health = await asyncio.wait_for(provider.health_check(), timeout=12.0)
        except Exception as e:
            health = {"name": getattr(provider, "name", "?"), "status": "network_error",
                      "detail": f"探测失败（{type(e).__name__}）",
                      "latency_ms": None, "http_status": None}
        finally:
            aclose = getattr(provider, "aclose", None)
            if aclose:
                try:
                    await aclose()
                except Exception:
                    pass
        return {**health, "role": role}

    healths = list(await asyncio.gather(*(_probe(f, r) for f, r in targets)))
    for h in healths:
        log = logger.info if h.get("status") == "ok" else logger.warning
        log("preflight %s[%s]: %s %s (%sms)", h.get("name"), h.get("role"),
            h.get("status"), h.get("detail"), h.get("latency_ms"))
    _cache[cache_key] = (time.monotonic(), deepcopy(healths))
    for k in list(_cache):
        if time.monotonic() - _cache[k][0] >= HEALTH_TTL:
            _cache.pop(k, None)
    return healths
