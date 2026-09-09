"""
集中限速器与弹性 HTTP 客户端

支持：
1. 域名级限速（QPS / 最小请求间隔）与最大并发控制
2. HTTP 429 / 503 / 504 自动指数退避与 Jitter，优先响应 Retry-After 响应头
3. 共享连接池与标准学术请求头（Polite Pool）
"""

from __future__ import annotations

import asyncio
import email.utils
import logging
import random
import time
import urllib.parse
from typing import Any, Mapping, Optional

import httpx

logger = logging.getLogger(__name__)

# 默认全局学术 User-Agent
DEFAULT_USER_AGENT = "AcademicPaperReader/1.0 (personal research use)"


class DomainRateLimiter:
    """按域名隔离的限速与并发控制器。"""

    def __init__(self):
        # 域名默认规则: (min_interval_seconds, max_concurrent)
        self._rules: dict[str, tuple[float, int]] = {
            "api.openalex.org": (0.12, 5),          # OpenAlex polite pool ~10 req/s
            "arxiv.org": (3.0, 1),                  # API and PDF share the same IP budget
            "export.arxiv.org": (3.0, 1),           # arXiv API 查询严格遵循 3s 间隔
            "api.unpaywall.org": (0.1, 5),          # Unpaywall OA 解析 10 req/s
            "api.crossref.org": (0.15, 5),          # CrossRef polite pool
            "api.semanticscholar.org": (1.0, 2),    # S2 无 key 时 1 req/s
            "api.elsevier.com": (0.2, 3),           # Elsevier / ScienceDirect API
            "doi.org": (0.2, 4),                    # DOI 解析
        }
        self._default_rule: tuple[float, int] = (0.05, 5)

        self._locks: dict[str, asyncio.Lock] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._last_req_time: dict[str, float] = {}
        self._cooldown: dict[str, float] = {}

    def _get_domain(self, url: str) -> str:
        try:
            parsed = urllib.parse.urlparse(url)
            domain = (parsed.hostname or "").lower()
            return "arxiv.org" if domain == "export.arxiv.org" else domain
        except Exception:
            return ""

    def _get_rule(self, domain: str) -> tuple[float, int]:
        for pattern, rule in sorted(self._rules.items(), key=lambda item: -len(item[0])):
            if domain == pattern or domain.endswith("." + pattern):
                return rule
        return self._default_rule

    def get_semaphore(self, domain: str) -> asyncio.Semaphore:
        if domain not in self._semaphores:
            _, max_concurrency = self._get_rule(domain)
            self._semaphores[domain] = asyncio.Semaphore(max_concurrency)
        return self._semaphores[domain]

    def _get_lock(self, domain: str) -> asyncio.Lock:
        if domain not in self._locks:
            self._locks[domain] = asyncio.Lock()
        return self._locks[domain]

    async def throttle(self, url: str) -> str:
        """根据目标 URL 域名执行等待限速，返回规范化域名。"""
        domain = self._get_domain(url)
        min_interval, _ = self._get_rule(domain)
        lock = self._get_lock(domain)

        async with lock:
            last_time = self._last_req_time.get(domain, 0.0)
            now = time.monotonic()
            elapsed = now - last_time
            sleep_sec = max(min_interval - elapsed, self._cooldown.get(domain, 0) - now)
            if sleep_sec > 0:
                await asyncio.sleep(sleep_sec)
            self._last_req_time[domain] = time.monotonic()

        return domain

    def cool_down(self, url: str, seconds: float) -> None:
        domain = self._get_domain(url)
        self._cooldown[domain] = max(self._cooldown.get(domain, 0), time.monotonic() + seconds)


# 全局单例限速器
global_rate_limiter = DomainRateLimiter()

_shared_client: Optional[httpx.AsyncClient] = None


class AdaptiveThrottle:
    """自适应限速：成功加速、429 减速的并发控制器。

    付费学术 API（如 Elsevier/Scopus）的配额随订阅档位变化且不透明：写死一个保守
    串行间隔要么浪费机构订阅的额度（校园网 IP 通常有权 ~10 req/s），要么在免费档
    上一路 429。让请求间隔在 [floor, cap] 之间随成败自适应，并发用信号量封顶：

    - 成功：interval = max(floor, interval × shrink) —— 逐步放开到订阅允许的速度；
    - 429：interval = min(cap, max(Retry-After, interval × grow)) —— 立即降速并尊重
      服务端指令；
    - acquire() 保证跨任务的发射间隔 ≥ interval（经典 slot 分配），信号量保证同时在
      飞的请求数 ≤ concurrency——两者合起来既防 429 又允许真正的并行。
    """

    def __init__(
        self,
        *,
        start: float = 0.35,
        floor: float = 0.1,
        cap: float = 8.0,
        shrink: float = 0.8,
        grow: float = 1.6,
        concurrency: int = 4,
    ):
        self.floor = max(0.0, floor)
        self.cap = max(self.floor, cap)
        self.shrink = shrink
        self.grow = grow
        self.interval = min(max(start, self.floor), self.cap)
        self._sem = asyncio.Semaphore(max(1, concurrency))
        self._lock = asyncio.Lock()
        self._next_slot = 0.0  # time.monotonic() 坐标

    def on_success(self) -> None:
        self.interval = max(self.floor, self.interval * self.shrink)

    def on_rate_limited(self, retry_after: float = 0.0) -> None:
        self.interval = min(self.cap, max(float(retry_after or 0.0), self.interval * self.grow))
        self._next_slot = max(self._next_slot, time.monotonic() + float(retry_after or 0))

    async def acquire(self) -> None:
        """占一个并发位 + 分配一个不早于当前 interval 间隔的发射槽。"""
        await self._sem.acquire()
        try:
            async with self._lock:
                while self._next_slot > time.monotonic():
                    await asyncio.sleep(self._next_slot - time.monotonic())
                self._next_slot = time.monotonic() + self.interval
        except BaseException:
            self._sem.release()
            raise

    def release(self) -> None:
        self._sem.release()

    async def __aenter__(self) -> "AdaptiveThrottle":
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.release()


def get_shared_client(timeout: float = 30.0) -> httpx.AsyncClient:
    """获取应用共享的 AsyncClient 实例。"""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": DEFAULT_USER_AGENT},
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
        )
    return _shared_client


def parse_retry_after(headers: Mapping[str, str], default_wait: float = 2.0) -> float:
    """解析 HTTP 响应头中的 Retry-After（支持秒数或 HTTP 日期）。"""
    raw = headers.get("retry-after", "").strip()
    if not raw:
        return default_wait
    # 纯数字秒数
    if raw.isdigit():
        return max(1.0, float(raw))
    # RFC 2822 / RFC 1123 日期
    try:
        parsed_tuple = email.utils.parsedate_tz(raw)
        if parsed_tuple:
            target_ts = email.utils.mktime_tz(parsed_tuple)
            wait_sec = target_ts - time.time()
            return max(1.0, wait_sec)
    except Exception:
        pass
    return default_wait


async def resilient_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    max_retries: int = 3,
    base_backoff: float = 1.5,
    client: httpx.AsyncClient | None = None,
    redirect_limit: int = 5,
) -> httpx.Response:
    """具备域名限速与 429/503 弹性重试的 HTTP GET 请求。"""
    cli = client or get_shared_client(timeout=timeout)
    from .research_exclusions import active_exclusions, check_download_url
    check_download_url(url)
    guarded = bool(active_exclusions.get().get("exclusions"))
    domain = global_rate_limiter._get_domain(url)
    sem = global_rate_limiter.get_semaphore(domain)

    merged_headers = {"User-Agent": DEFAULT_USER_AGENT}
    if headers:
        merged_headers.update(headers)

    last_exc: Optional[Exception] = None
    target = None

    for attempt in range(max_retries):
        async with sem:
            await global_rate_limiter.throttle(url)
            try:
                resp = await cli.get(url, params=params, headers=merged_headers, timeout=timeout,
                                     **({"follow_redirects": False} if guarded else {}))
                if guarded and resp.is_redirect and resp.headers.get("location"):
                    target = urllib.parse.urljoin(str(resp.url), resp.headers["location"])
                    check_download_url(target)
                    if redirect_limit <= 0:
                        raise ValueError("Too many research download redirects")
                    if urllib.parse.urlparse(target).netloc != urllib.parse.urlparse(url).netloc:
                        merged_headers = {k: v for k, v in merged_headers.items()
                                          if k.lower() not in {"authorization", "x-els-apikey", "cookie"}}
                    # Release this host's semaphore before following a same-host redirect.
                    break
                if resp.status_code == 429:
                    wait_sec = parse_retry_after(
                        resp.headers,
                        default_wait=base_backoff * (2 ** attempt) + random.uniform(0.1, 0.5),
                    )
                    logger.warning(
                        f"HTTP 429 from {domain or url} (attempt {attempt + 1}/{max_retries}), waiting {wait_sec:.1f}s"
                    )
                    global_rate_limiter.cool_down(url, wait_sec)
                    if attempt < max_retries - 1:
                        await asyncio.sleep(wait_sec)
                        continue
                elif resp.status_code in (502, 503, 504):
                    wait_sec = base_backoff * (2 ** attempt) + random.uniform(0.1, 0.3)
                    logger.warning(
                        f"HTTP {resp.status_code} from {domain or url} (attempt {attempt + 1}/{max_retries}), waiting {wait_sec:.1f}s"
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(wait_sec)
                        continue

                resp.raise_for_status()
                return resp
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_exc = e
                wait_sec = base_backoff * (2 ** attempt) + random.uniform(0.1, 0.3)
                logger.warning(
                    f"Network error requesting {url} (attempt {attempt + 1}/{max_retries}): {e}, waiting {wait_sec:.1f}s"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(wait_sec)
                    continue
                raise
            except httpx.HTTPStatusError as e:
                last_exc = e
                if e.response.status_code in (429, 502, 503, 504) and attempt < max_retries - 1:
                    wait_sec = parse_retry_after(
                        e.response.headers,
                        default_wait=base_backoff * (2 ** attempt) + random.uniform(0.1, 0.5),
                    )
                    await asyncio.sleep(wait_sec)
                    continue
                raise

    if target:
        return await resilient_get(target, headers=merged_headers, timeout=timeout, max_retries=max_retries,
                                   base_backoff=base_backoff, client=cli, redirect_limit=redirect_limit - 1)
    if last_exc:
        raise last_exc
    raise RuntimeError(f"resilient_get exhausted {max_retries} retries for {url}")
