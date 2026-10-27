"""CrossRef API 客户端 — 通过标题查询 DOI 及开放获取 PDF 链接

CrossRef 是免费公开的学术元数据 API，无需 API key。
"Polite pool" 仅需在请求中携带 mailto 参数即可获得更高速率。

文档: https://api.crossref.org/swagger-ui/index.html
"""
import asyncio
import html
import logging
import re
import time
import urllib.parse
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

CROSSREF_API = "https://api.crossref.org/works"
# CrossRef 的 polite pool 靠 User-Agent 里的联系方式识别，填了才有更宽的限速。
# 这里不写死任何个人账号或邮箱；需要 polite pool 时把自己的 mailto 补进括号内。
USER_AGENT = "AcademicPaperReader/1.0 (personal research use)"

# 串行限速：CrossRef polite pool ~50 req/s，保守取 1 秒间隔
_RATE_LIMIT = 1.0
_last_req_time: float = 0.0
# DOI 批量反查摘要被 429 后的暂停截止（monotonic），只影响摘要反查
_ABSTRACT_COOLDOWN = 120.0
_abstract_cooldown_until: float = 0.0
_req_lock: Optional[asyncio.Lock] = None
_client: Optional[httpx.AsyncClient] = None


def _get_lock() -> asyncio.Lock:
    global _req_lock
    if _req_lock is None:
        _req_lock = asyncio.Lock()
    return _req_lock


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=15.0,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
    return _client


async def _crossref_get(url: str, params: dict | None = None) -> dict:
    """限速 GET，返回 JSON dict。"""
    global _last_req_time
    async with _get_lock():
        elapsed = asyncio.get_event_loop().time() - _last_req_time
        if elapsed < _RATE_LIMIT:
            await asyncio.sleep(_RATE_LIMIT - elapsed)
        _last_req_time = asyncio.get_event_loop().time()
    resp = await _get_client().get(url, params=params)
    resp.raise_for_status()
    return resp.json()


def _normalize(s: str) -> str:
    import re
    return re.sub(r"[\s\-–—:.]+", " ", s.lower()).strip()


async def search_by_title(title: str) -> Optional[dict]:
    """通过标题查询 CrossRef，返回 {doi, pdf_url, title, year} 或 None。

    pdf_url 可能是：
    1. CrossRef 返回的开放获取 PDF 链接（link 字段，content-type=application/pdf）
    2. 通过 doi.org 重定向的 URL（不保证是 PDF，后续下载时需验证）
    """
    if not title or not title.strip():
        return None

    norm_query = _normalize(title)

    try:
        params = {
            "query.bibliographic": title.strip(),
            "rows": "3",
            "select": "DOI,title,author,published-print,published-online,link,is-referenced-by-count",
        }
        data = await _crossref_get(CROSSREF_API, params=params)
    except Exception as e:
        logger.debug(f"CrossRef search failed ('{title[:50]}'): {e}")
        return None

    items = data.get("message", {}).get("items", [])
    if not items:
        return None

    # 找最佳标题匹配
    best = None
    best_score = 0.0

    for item in items:
        doi = item.get("DOI")
        if not doi:
            continue

        # CrossRef title 是数组
        cr_titles = item.get("title", [])
        if not cr_titles:
            continue
        cr_title = cr_titles[0]
        norm_cr = _normalize(cr_title)

        # 计算 Jaccard 相似度
        score = _jaccard(norm_query, norm_cr)
        if score > best_score:
            best_score = score
            # 提取 PDF URL
            pdf_url = _extract_pdf_link(item, doi)
            # 提取年份
            year = _extract_year(item)
            best = {
                "doi": doi,
                "pdf_url": pdf_url,
                "title": cr_title,
                "authors": ", ".join(" ".join(str(a.get(k) or "") for k in ("given", "family")).strip() for a in item.get("author") or []),
                "year": year,
                "score": score,
            }

    # 阈值 0.7：标题需要高度匹配
    if best and best_score >= 0.7:
        logger.debug(
            f"CrossRef match: '{title[:50]}' → DOI={best['doi']} "
            f"(score={best_score:.2f}, pdf={'yes' if best['pdf_url'] else 'no'})"
        )
        return best

    return None


async def get_pdf_url_by_doi(doi: str) -> Optional[str]:
    """通过已知 DOI 获取 PDF 下载链接。

    策略：
    1. CrossRef metadata 中的 link 字段（开放获取 PDF）
    2. doi.org 重定向（可能是 publisher 页面，不一定是 PDF）
    """
    try:
        url = f"{CROSSREF_API}/{urllib.parse.quote(doi, safe='')}"
        params = {"select": "DOI,link,title"}
        data = await _crossref_get(url, params=params)
        item = data.get("message", {})
        pdf_url = _extract_pdf_link(item, doi)
        if pdf_url:
            return pdf_url
    except Exception as e:
        logger.debug(f"CrossRef DOI lookup failed ({doi}): {e}")

    # 兜底：直接用 doi.org 重定向
    return f"https://doi.org/{doi}"


def jats_to_text(raw: str) -> str:
    """Crossref 摘要是出版社存缴的 JATS XML：去标签与“Abstract”小标题，留纯文本。"""
    text = re.sub(r"<jats:title>.*?</jats:title>", " ", raw or "", flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"^(abstract|summary)\b[\s.:]*", "", text, flags=re.I)


async def get_abstracts_by_dois(dois: list[str], *, chunk: int = 20) -> dict[str, str]:
    """DOI 批量查 Crossref 摘要（filter=doi:a,doi:b）。覆盖率取决于出版社是否存缴摘要；无需 key。"""
    global _abstract_cooldown_until
    found: dict[str, str] = {}
    usable = [d for d in dict.fromkeys(dois) if d and "," not in d]  # filter 参数以逗号分隔
    for i in range(0, len(usable), chunk):
        if time.monotonic() < _abstract_cooldown_until:
            break
        part = usable[i:i + chunk]
        try:
            data = await _crossref_get(CROSSREF_API, params={
                "filter": ",".join(f"doi:{d}" for d in part), "rows": len(part), "select": "DOI,abstract"})
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 429:
                # 被限流：按 Retry-After（缺省 2 分钟）暂停摘要反查，不再逐批撞限
                from ..rate_limiter import parse_retry_after

                wait = parse_retry_after(exc.response.headers, default_wait=_ABSTRACT_COOLDOWN)
                _abstract_cooldown_until = time.monotonic() + max(wait, _ABSTRACT_COOLDOWN)
                logger.warning("Crossref abstract lookup rate limited; pausing it for %.0fs",
                               max(wait, _ABSTRACT_COOLDOWN))
                break
            logger.warning("Crossref abstract lookup failed (non-fatal): %s", exc)
            continue
        except Exception as exc:  # noqa: BLE001 —— 单批失败不影响其余批次
            logger.warning("Crossref abstract lookup failed (non-fatal): %s", exc)
            continue
        for item in (data.get("message") or {}).get("items") or []:
            text = jats_to_text(item.get("abstract") or "")
            if item.get("DOI") and text:
                found[str(item["DOI"]).lower()] = text
    return found


def _extract_pdf_link(item: dict, doi: str) -> Optional[str]:
    """从 CrossRef item 的 link 字段提取 PDF URL。"""
    links = item.get("link", [])
    for link in links:
        content_type = (link.get("content-type") or "").lower()
        url = link.get("URL", "")
        if "pdf" in content_type or url.endswith(".pdf"):
            return url
    # 没有直接 PDF 链接时，返回 doi.org 重定向地址
    return None


def _extract_year(item: dict) -> Optional[int]:
    """从 CrossRef item 提取发表年份。"""
    for key in ("published-online", "published-print"):
        date_parts = item.get(key, {}).get("date-parts", [[]])
        if date_parts and date_parts[0]:
            try:
                return int(date_parts[0][0])
            except (ValueError, IndexError):
                pass
    return None


def _jaccard(a: str, b: str) -> float:
    """Jaccard 词集合相似度。"""
    words_a = set(a.split())
    words_b = set(b.split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)
