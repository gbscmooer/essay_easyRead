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
_DEAD_STATUSES = {"auth_error", "network_error", "error", "unconfigured", "quota_exhausted"}


def dead_source_names(healths: list[dict]) -> set[str]:
    return {h["name"] for h in healths if h.get("status") in _DEAD_STATUSES}


def any_primary_source_alive(healths: list[dict]) -> bool:
    """主源（primary/fallback 的 provider 组合）是否仍有可用者。

    supplement 源（arXiv）不参与该判定：它判死只触发召回侧熔断与警告，
    不能让「arXiv 可用但主库全死」的运行误判通过，也不能因 arXiv 抖动
    把主库正常的运行卡死在预检。
    """
    primaries = [h for h in healths if h.get("role") != "supplement"]
    if not primaries:
        return False
    return any(h.get("status") in {"ok", "rate_limited"} for h in primaries)


def any_source_ok(healths: list[dict]) -> bool:
    return any(h.get("status") == "ok" for h in healths)


async def _probe_arxiv() -> dict:
    """arXiv 是三检索源之一（recall 引擎 arxiv+scopus+openalex 并行），但历史上
    预检只测 provider 组合——arXiv 挂了要等正式召回阶段逐通道报错才暴露（run6
    实测 6 次 arXiv HTTPStatusError 全部发生在预检标记完成之后）。这里用
    search_topic 的最小真实查询补上这块盲区。role=supplement：arXiv 判死只
    触发召回熔断与警告，不参与「全部判死才快速失败」的主源判定。"""
    from .arxiv_client import search_topic
    from ..http_diagnostics import capture_http_attempts, http_failure

    started = time.monotonic()
    with capture_http_attempts() as attempts:
        result = {"name": "arxiv", "role": "supplement"}
        try:
            rows = await asyncio.wait_for(search_topic("machine learning", limit=1, timeout=12, max_attempts=1), timeout=12)
            result.update(status="ok", detail=f"最小查询命中 {len(rows)} 条", http_status=200)
        except Exception as exc:
            error = http_failure(exc, attempts)
            status = {"rate_limited": "rate_limited", "auth_error": "auth_error",
                      "server_error": "error", "http_error": "error", "tool_error": "error"}.get(error["failure_kind"], "network_error")
            code = error["http_status"]
            detail = f"HTTP {code}：" if code else ""
            detail += "限流，请稍后重试" if code == 429 else f"探测失败（{error['error_code']}）"
            if not code and error["last_http_status"]:
                detail += f"；此前响应 HTTP {error['last_http_status']}"
            result.update(status=status, detail=detail, http_status=code)
        result.update(latency_ms=round((time.monotonic() - started) * 1000), http_attempts=attempts)
        return result


async def preflight_research_sources(cfg: dict | None = None, *, use_cache: bool = True,
                                     include_arxiv: bool = True) -> list[dict]:
    loop = asyncio.get_running_loop()
    if loop not in _locks:
        _locks[loop] = asyncio.Lock()
    async with _locks[loop]:
        return await _preflight_research_sources(cfg, use_cache=use_cache,
                                                 include_arxiv=include_arxiv)


async def _preflight_research_sources(cfg: dict | None = None, *, use_cache: bool = True,
                                      include_arxiv: bool = True) -> list[dict]:
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
    from .providers.openalex import configured_api_key

    cfg = dict(cfg if cfg is not None else get_research_config())
    provider_name = str(cfg.get("provider") or "auto").lower()
    key = _elsevier_key(cfg)
    email = str(cfg.get("email") or "").strip()

    cache_key = hashlib.sha256(
        f"{provider_name}|{key}|{email}|{cfg.get('api_key', '')}|{configured_api_key()}"
        f"|arxiv={int(include_arxiv)}".encode()).hexdigest()
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
    if include_arxiv:
        healths.append(await _probe_arxiv())
    for h in healths:
        log = logger.info if h.get("status") == "ok" else logger.warning
        log("preflight %s[%s]: %s %s (%sms)", h.get("name"), h.get("role"),
            h.get("status"), h.get("detail"), h.get("latency_ms"))
    _cache[cache_key] = (time.monotonic(), deepcopy(healths))
    for k in list(_cache):
        if time.monotonic() - _cache[k][0] >= HEALTH_TTL:
            _cache.pop(k, None)
    return healths
