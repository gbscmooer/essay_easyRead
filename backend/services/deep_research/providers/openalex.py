"""
OpenAlex Provider — 免费、无需 key、配额慷慨

API 文档: https://docs.openalex.org/
- 公共匿名调用即可，速率约 10 req/s（24 h 内 100 k）
- 提供 `mailto` 邮箱可进入 "polite pool"，获得更稳定的服务质量
"""

import asyncio
import logging
import time
from typing import Optional

import httpx

from ....models.recall import RecallCandidate, RecallSource, build_recall_candidate, format_authors
from ...paper_identity import normalize_title

logger = logging.getLogger(__name__)

OA_BASE = "https://api.openalex.org"
WORK_FIELDS = (
    "id,title,publication_year,cited_by_count,doi,ids,open_access,"
    "primary_location,locations,abstract_inverted_index,authorships,"
    "counts_by_year,keywords,type,topics,primary_topic"
)
# 逐篇补查（get_citation_history）的精简字段：逐年引用 + 摘要 + 关键词 + 期刊身份
HISTORY_FIELDS = "id,doi,cited_by_count,counts_by_year,abstract_inverted_index,keywords,primary_location,locations,open_access"


class OpenAlexProvider:
    name = "openalex"

    def __init__(self, email: str = ""):
        self.email = (email or "").strip()
        self._client: httpx.AsyncClient | None = None
        self._last_req = 0.0
        self._lock = asyncio.Lock()

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def _get(self, path: str, params: dict | None = None) -> dict:
        params = dict(params or {})
        if self.email:
            params["mailto"] = self.email
        from ...rate_limiter import account_source_request
        for attempt in range(4):
            async with self._lock:
                gap = time.monotonic() - self._last_req
                if gap < 0.12:
                    await asyncio.sleep(0.12 - gap)
                self._last_req = time.monotonic()
            account_source_request()
            try:
                r = await self._http().get(f"{OA_BASE}{path}", params=params)
                if r.status_code == 429:
                    from ...rate_limiter import parse_retry_after
                    wait = parse_retry_after(r.headers, default_wait=2.0 * (attempt + 1))
                    logger.warning(f"OpenAlex 429, retry {attempt+1}/4 in {wait:.1f}s")
                    await asyncio.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()
            except httpx.HTTPError as e:
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 404:
                    return {}
                if attempt == 3:
                    raise
                logger.warning(f"OpenAlex error (attempt {attempt+1}): {e}")
                await asyncio.sleep(1.0 * (attempt + 1))
        return {}

    async def _safe_get(self, path: str, params: dict | None = None) -> dict | None:
        """GET 请求；失败返回 None，不抛异常。"""
        try:
            data = await self._get(path, params)
            return data if data else None
        except httpx.TimeoutException:
            logger.warning("OpenAlex request timed out")
            return None
        except httpx.HTTPError as e:
            logger.warning(f"OpenAlex HTTP error: {e}")
            return None
        except Exception as e:
            logger.warning(f"OpenAlex request failed: {e}")
            return None

    async def find_paper_id(self, title: str, doi: str = "", arxiv_id: str = "") -> Optional[str]:
        # 1) DOI 最准
        if doi:
            try:
                data = await self._get(f"/works/doi:{doi}", {"select": "id"})
                if data.get("id"):
                    return _strip_id(data["id"])
            except Exception as e:
                logger.debug(f"OpenAlex DOI lookup failed: {e}")
        # 2) arXiv：用 DOI 形式 10.48550/arXiv.XXXX.XXXXX
        if arxiv_id:
            try:
                ax_doi = f"10.48550/arXiv.{arxiv_id}"
                data = await self._get(f"/works/doi:{ax_doi}", {"select": "id"})
                if data.get("id"):
                    return _strip_id(data["id"])
            except Exception:
                pass
        # 3) 标题搜索
        if title:
            try:
                data = await self._get("/works", {
                    "search": title,
                    "per-page": 1,
                    "select": "id,title",
                })
                arr = data.get("results") or []
                if arr and normalize_title(arr[0].get("title")) == normalize_title(title):
                    return _strip_id(arr[0]["id"])
            except Exception as e:
                logger.debug(f"OpenAlex title search failed: {e}")
        return None

    async def get_paper_references(self, paper_id: str, limit: int = 50) -> list[dict]:
        # 1) 读 work，拿 referenced_works
        try:
            work = await self._get(f"/works/W{paper_id.lstrip('W')}", {"select": "referenced_works"})
        except Exception as e:
            logger.warning(f"OpenAlex fetch work failed: {e}")
            return []
        ref_ids = work.get("referenced_works") or []
        if not ref_ids:
            return []
        ref_ids = ref_ids[: min(limit, 50)]

        # 2) 批量拉 references 详情：filter=openalex:W1|W2|...
        oid_csv = "|".join(_strip_id(r) for r in ref_ids if r)
        if not oid_csv:
            return []
        try:
            data = await self._get("/works", {
                "filter": f"openalex:{oid_csv}",
                "per-page": min(len(ref_ids), 50),
                "select": WORK_FIELDS,
            })
        except Exception as e:
            logger.warning(f"OpenAlex batch fetch failed: {e}")
            return []

        results = []
        for w in data.get("results") or []:
            if not w.get("title"):
                continue
            ids = w.get("ids") or {}
            doi_url = w.get("doi") or ""
            doi = doi_url.replace("https://doi.org/", "") if doi_url else None
            arxiv = _extract_arxiv(w)
            pdf_url = _extract_pdf(w)
            results.append({
                "ext_id": _strip_id(w["id"]),
                "title": w["title"],
                "year": w.get("publication_year"),
                "citation_count": w.get("cited_by_count") or 0,
                "doi": doi,
                "arxiv_id": arxiv,
                "pdf_url": pdf_url,
            })
        return results

    async def get_paper_metadata(self, paper_id: str) -> dict | None:
        """单篇 work 的规范元数据（身份补齐用）：doi / arxiv / title / year。"""
        wid = paper_id.lstrip("W")
        work = await self._safe_get(
            f"/works/W{wid}",
            {"select": "id,title,publication_year,doi,ids,primary_location,locations"},
        )
        if not work:
            return None
        doi_url = work.get("doi") or ""
        return {
            "openalex_id": _strip_id(work.get("id") or ""),
            "title": work.get("title"),
            "year": work.get("publication_year"),
            "doi": doi_url.replace("https://doi.org/", "") if doi_url else None,
            "arxiv_id": _extract_arxiv(work),
        }

    async def get_paper_citations(self, paper_id: str, limit: int = 50) -> list[RecallCandidate]:
        try:
            wid = paper_id.lstrip("W")
            data = await self._safe_get("/works", {
                "filter": f"cites:W{wid}",
                "per-page": min(limit, 50),
                "sort": "cited_by_count:desc",
                "select": WORK_FIELDS,
            })
            if not data:
                return []
            return self._works_to_candidates(
                data.get("results") or [],
                source=RecallSource.FORWARD_CIT,
                seed_paper_id=paper_id,
                seed_relation="cited_by",
                limit=limit,
            )
        except Exception as e:
            logger.warning(f"OpenAlex get_paper_citations failed: {e}")
            return []

    async def get_recommendations(self, paper_id: str, limit: int = 50) -> list[RecallCandidate]:
        try:
            wid = paper_id.lstrip("W")
            work = await self._safe_get(f"/works/W{wid}", {"select": "related_works"})
            if not work:
                return []
            related_ids = work.get("related_works") or []
            if not related_ids:
                return []
            related_ids = related_ids[: min(limit, 50)]
            oid_csv = "|".join(_strip_id(r) for r in related_ids if r)
            if not oid_csv:
                return []
            data = await self._safe_get("/works", {
                "filter": f"openalex:{oid_csv}",
                "per-page": min(len(related_ids), 50),
                "select": WORK_FIELDS,
            })
            if not data:
                return []
            return self._works_to_candidates(
                data.get("results") or [],
                source=RecallSource.RECOMMEND,
                seed_paper_id=paper_id,
                seed_relation="related",
                limit=limit,
            )
        except Exception as e:
            logger.warning(f"OpenAlex get_recommendations failed: {e}")
            return []

    async def search_works(self, query: str, limit: int = 20, *, academic_only: bool = False,
                           order: str = "relevance") -> list[RecallCandidate]:
        try:
            if not query or not query.strip():
                return []
            params = {
                "search": query.strip(),
                "per-page": min(limit, 50),
                # order="cited"：被引降序的经典层补充（relevance 偏新，奠基论文需显式召回）
                "sort": "cited_by_count:desc" if order == "cited" else "relevance_score:desc",
                "select": WORK_FIELDS,
            }
            if academic_only:
                # 排除 editorial / letter / erratum / newsletter 等噪声类型；
                # 保留 preprint——CV/ML 领域 arXiv 预印本是一等公民
                params["filter"] = "type:article|review|preprint|book-chapter"
            data = await self._safe_get("/works", params)
            if not data:
                return []
            return self._works_to_candidates(
                data.get("results") or [],
                source=RecallSource.SEARCH,
                seed_relation="search_match",
                limit=limit,
            )
        except Exception as e:
            logger.warning(f"OpenAlex search_works failed: {e}")
            return []

    def _works_to_candidates(
        self,
        works: list[dict],
        *,
        source: RecallSource,
        seed_paper_id: str | None = None,
        seed_relation: str | None = None,
        limit: int = 50,
    ) -> list[RecallCandidate]:
        out: list[RecallCandidate] = []
        for rank, w in enumerate(works[:limit]):
            cand = _work_to_candidate(
                w,
                source=source,
                source_rank=rank,
                seed_paper_id=seed_paper_id,
                seed_relation=seed_relation,
            )
            if cand:
                out.append(cand)
        return out

    async def find_pdf_by_doi(self, doi: str) -> Optional[str]:
        """通过 DOI 精确查询 Open Access PDF URL。"""
        if not doi:
            return None
        clean_doi = doi.replace("https://doi.org/", "").strip()
        data = await self._safe_get(
            f"/works/doi:{clean_doi}",
            {"select": "id,doi,open_access,primary_location,locations"},
        )
        if not data:
            return None
        return _extract_pdf(data)

    async def get_abstracts_by_dois(self, dois: list[str]) -> dict[str, str]:
        """DOI 批量反查摘要：filter=doi:a|b|…（OR 语义，单请求 ≤50 个）。

        Scopus Search 不返回摘要（个人 Key 权限），召回引擎靠它成批补齐；
        返回 {doi(小写): 摘要文本}，查不到的 DOI 不出现在结果里。
        """
        out: dict[str, str] = {}
        cleaned = [d.strip().replace("https://doi.org/", "") for d in dois if d and d.strip()]
        for start in range(0, len(cleaned), 50):
            chunk = cleaned[start:start + 50]
            data = await self._safe_get("/works", {
                "filter": "doi:" + "|".join(chunk),
                "select": "doi,abstract_inverted_index",
                "per-page": 50,
            })
            for work in (data or {}).get("results") or []:
                doi = str(work.get("doi") or "").replace("https://doi.org/", "").lower()
                abstract = _reconstruct_abstract(work)
                if doi and abstract:
                    out[doi] = abstract
        return out

    async def get_citation_history(self, doi: str) -> dict | None:
        """按 DOI 拉逐年引用曲线 + 摘要 + 关键词 + 期刊身份（指标富化用）。

        Scopus 的 Citation Overview/Count API 对个人 dev Key 404 无权限（2026-09-02
        实测），逐年引用曲线唯一免费来源就是 OpenAlex 的 counts_by_year；Scopus 检索
        结果又不带摘要——本方法一次补齐探索侧富化需要的全部字段。
        """
        if not doi:
            return None
        clean_doi = doi.replace("https://doi.org/", "").strip()
        work = await self._safe_get(
            f"/works/doi:{clean_doi}", {"select": HISTORY_FIELDS},
        )
        if not work or not work.get("id"):
            return None
        venue_name, venue_issn = _venue_of(work)
        return {
            "openalex_cited_by": work.get("cited_by_count") or 0,
            "counts_by_year": work.get("counts_by_year") or [],
            "abstract": _reconstruct_abstract(work),
            "keywords": _extract_keywords(work),
            "venue_name": venue_name,
            "venue_issn": venue_issn,
            "pdf_url": _extract_pdf(work),
            "arxiv_id": _extract_arxiv(work),
        }

    async def health_check(self, *, timeout: float = 10.0) -> dict:
        """权限体检：发一次最小真实 /works 查询（per-page=1）。匿名可用，
        403/断网才值得报告——这决定熔断回退后还有没有源可用。"""
        params = {"per-page": 1, "select": "id"}
        if self.email:
            params["mailto"] = self.email
        start = time.monotonic()
        try:
            r = await self._http().get(f"{OA_BASE}/works", params=params, timeout=timeout)
        except httpx.HTTPError as e:
            return {"name": self.name, "status": "network_error",
                    "detail": f"连接失败（{type(e).__name__}）",
                    "latency_ms": None, "http_status": None}
        latency = int((time.monotonic() - start) * 1000)
        base = {"name": self.name, "latency_ms": latency, "http_status": r.status_code}
        if r.status_code == 200:
            return {**base, "status": "ok", "detail": "检索服务正常"}
        if r.status_code == 403:
            return {**base, "status": "auth_error",
                    "detail": "HTTP 403：出口 IP 被限制"}
        if r.status_code == 429:
            return {**base, "status": "rate_limited",
                    "detail": "HTTP 429：限速（稍后自动恢复）"}
        return {**base, "status": "error", "detail": f"HTTP {r.status_code}"}

    async def aclose(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None


_shared_oa_provider: Optional[OpenAlexProvider] = None


def get_shared_openalex_provider(email: str = "") -> OpenAlexProvider:
    """获取应用全局共享的 OpenAlexProvider 实例（复用限速与连接池）。"""
    global _shared_oa_provider
    if _shared_oa_provider is None:
        _shared_oa_provider = OpenAlexProvider(email=email)
    elif email and not _shared_oa_provider.email:
        _shared_oa_provider.email = email
    return _shared_oa_provider


def _strip_id(oid: str) -> str:
    """https://openalex.org/W123 → W123"""
    if not oid:
        return ""
    return oid.rsplit("/", 1)[-1]


def _extract_arxiv(work: dict) -> Optional[str]:
    """从 ids 或 locations 中尝试抽 arXiv ID"""
    doi = (work.get("doi") or "").lower()
    if "arxiv" in doi:
        # 10.48550/arxiv.2401.12345
        tail = doi.split("arxiv.", 1)[-1]
        return tail.strip("/").split("/")[0] if tail else None
    for loc in (work.get("locations") or []):
        src = (loc.get("source") or {}).get("display_name") or ""
        if "arxiv" in src.lower():
            url = loc.get("landing_page_url") or ""
            if "arxiv.org/abs/" in url:
                return url.split("arxiv.org/abs/", 1)[-1].strip("/")
    return None


def _extract_pdf(work: dict) -> Optional[str]:
    """优先 open_access.oa_url，其次扫描 locations.pdf_url"""
    oa = work.get("open_access") or {}
    if oa.get("oa_url"):
        return oa["oa_url"]
    pri = work.get("primary_location") or {}
    if pri.get("pdf_url"):
        return pri["pdf_url"]
    for loc in work.get("locations") or []:
        if loc.get("pdf_url"):
            return loc["pdf_url"]
    return None


def _extract_authors(work: dict) -> str | None:
    names: list[str] = []
    for authorship in work.get("authorships") or []:
        author = authorship.get("author") or {}
        name = author.get("display_name")
        if name:
            names.append(name)
    return format_authors(names)


def _reconstruct_abstract(work: dict) -> str | None:
    inv = work.get("abstract_inverted_index")
    if not inv:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    if not positions:
        return None
    positions.sort()
    return " ".join(w for _, w in positions)


# OpenAlex keywords 的 score 是 0~1 的相关度（ResNet 实测最高 0.91），阈值按此标定
_KEYWORD_MIN_SCORE = 0.5
# 学科级泛化词：在任何语料里都是全场高频，进热力图只会贡献平铺的噪声行
_GENERIC_KEYWORDS = {
    "computer science", "artificial intelligence", "machine learning", "deep learning",
    "neural network", "neural networks", "pattern recognition", "pattern recognition (psychology)",
    "mathematics", "physics", "engineering", "medicine", "biology", "chemistry", "psychology",
    "science", "technology", "data science", "big data", "statistics", "algorithm", "computer vision",
}
# 带括号消歧后缀的概念词是 OpenAlex 的跨学科条目：后缀学科不在 CS/AI 允许集内的
# 一律丢弃（run6 实测 "Anomaly (physics)"、"Benchmark (surveying)"、"Quality (philosophy)"
# 这类词凭高出现频次进了关键词趋势热力图，把缺陷检测语料污染成了跨学科大杂烩）
_KEYWORD_SUFFIX_ALLOWLIST = {
    "computer science", "artificial intelligence", "machine learning", "computer vision",
    "natural language processing", "data mining", "information retrieval", "software engineering",
    "electrical engineering", "human computer interaction", "operating system",
}


def _keyword_suffix_allowed(name: str) -> bool:
    """无括号后缀的概念词直接放行；带后缀的只留 CS/AI 学科消歧条目。"""
    lowered = name.casefold()
    if "(" not in lowered:
        return True
    suffix = lowered.rsplit("(", 1)[-1].rstrip(")").strip() if "(" in lowered else ""
    return suffix in _KEYWORD_SUFFIX_ALLOWLIST


def _extract_keywords(work: dict, *, min_score: float = _KEYWORD_MIN_SCORE, cap: int = 6) -> list[str]:
    """OpenAlex keywords（算法标注的领域短语，score 0~1）→ 每篇最多 cap 个。"""
    seen: set[str] = set()
    out: list[str] = []
    for k in work.get("keywords") or []:
        name = str((k or {}).get("display_name") or "").strip()
        try:
            score = float((k or {}).get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        folded = name.casefold()
        if (len(name) < 3 or score < min_score or folded in seen
                or folded in _GENERIC_KEYWORDS or not _keyword_suffix_allowed(name)):
            continue
        seen.add(folded)
        out.append(name)
        if len(out) >= cap:
            break
    return out


def _venue_of(work: dict) -> tuple[str | None, str | None]:
    """primary_location.source → (期刊名, 首个 ISSN)。"""
    src = ((work.get("primary_location") or {}).get("source")) or {}
    name = str(src.get("display_name") or "").strip() or None
    issn = ""
    for cand in src.get("issn") or []:
        if cand and str(cand).strip():
            issn = str(cand).strip()
            break
    return name, (issn or None)


def _work_to_candidate(
    work: dict,
    *,
    source: RecallSource,
    source_rank: int,
    seed_paper_id: str | None = None,
    seed_relation: str | None = None,
) -> RecallCandidate | None:
    if not work.get("title"):
        return None
    doi_url = work.get("doi") or ""
    doi = doi_url.replace("https://doi.org/", "") if doi_url else None
    arxiv = _extract_arxiv(work)
    pdf_url = _extract_pdf(work)
    oa = work.get("open_access") or {}
    openalex_id = _strip_id(work.get("id") or "")
    venue_name, venue_issn = _venue_of(work)
    from ...paper_metadata import openalex_metadata
    bibliography = openalex_metadata(work)

    return build_recall_candidate(
        title=work["title"],
        doi=doi,
        arxiv_id=arxiv,
        openalex_id=openalex_id,
        abstract=_reconstruct_abstract(work),
        year=work.get("publication_year"),
        authors=_extract_authors(work),
        source=source,
        source_rank=source_rank,
        raw_score=float(work.get("cited_by_count") or 0),
        seed_paper_id=seed_paper_id,
        seed_relation=seed_relation,
        pdf_url=pdf_url,
        is_open_access=bool(oa.get("is_oa")) if oa else bool(pdf_url),
        citation_count=work.get("cited_by_count") or 0,
        provenance=["provider:openalex", f"method:{source.value}"],
        extra={
            "counts_by_year": work.get("counts_by_year") or [],
            "keywords": _extract_keywords(work),
            "venue_name": bibliography["venue"],
            "venue_issn": venue_issn,
            "subjects": bibliography["subjects"],
            "publication_type": bibliography["publication_type"],
            "publisher": ((work.get("primary_location") or {}).get("source") or {}).get("host_organization_name"),
        },
    )
