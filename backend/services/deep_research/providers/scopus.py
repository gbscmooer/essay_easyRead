"""
Scopus Provider — Elsevier 官方学术检索库（API Key 认证）

API 文档: https://dev.elsevier.com/
- 认证：`X-ELS-APIKey` 头（`research.elsevier_api_key` 或 `settings.elsevier_api_key`）。
  ⚠ 头名必须是 X-ELS-APIKey——写成 X-APIKey 时有效 Key 也会 401 "Invalid API Key"
  （2026-09-02 实测踩坑；`apikey` 查询参数亦可）。Scopus Search 需要有效的 API Key，
  校园机构 IP 不放行检索 API；但校园 IP + 有效 Key 可享 entitled 机构配额档。
  机构 IP 直连覆盖的是 ScienceDirect 全文通道（Article Retrieval 无 Key 200，
  pdf_downloader 走的就是这条路）。
- find_paper_id: Scopus Search（DOI 精确 / 标题模糊）→ scopus-id
- get_paper_references: Abstract Retrieval `view=REF`（参考文献列表）
- get_paper_citations: Scopus Search `REF(scopus-id)`（正向引用）
- get_recommendations: Scopus 无对应免费接口，返回空（通道自然缺席，RRF 不受影响）

限速：AdaptiveThrottle（成功加速 / 429 尊重 Retry-After 降速 + 并发 4）——机构订阅
的配额远高于匿名免费档，串行 0.6s 间隔浪费订阅额度；自适应策略在两者间自动收敛。

熔断：连续 401/403/429(配额) 时置 `_disabled`，后续调用直接返回空并只告警一次，
避免整条 expand 流水线被无权限的 key 卡死（扩展饿死比报错更难排查）。
"""

import asyncio
import re
import logging
import time
import weakref
from typing import Optional

import httpx

from ....models.recall import RecallCandidate, RecallSource, build_recall_candidate
from ...paper_identity import normalize_title
from ...rate_limiter import AdaptiveThrottle, parse_retry_after

logger = logging.getLogger(__name__)

SCOPUS_BASE = "https://api.elsevier.com"
# 连续鉴权/配额失败到这个次数就熔断（401/403 对同一 Key+IP 是确定性的，2 次足够定罪；
# 熔断后 FailoverProvider 透明切备用源，探索/扩展不中断）
_AUTH_FAIL_LIMIT = 2
# 学术检索的质量过滤：只要 期刊/会议文章、综述、in-press、短综述，
# 排除 letter / note / erratum / 章节等噪声类型（探索召回的「排除干扰」）
_ACADEMIC_DOCTYPES = "ar OR cp OR re OR sh OR ip"
_shared_throttles = weakref.WeakKeyDictionary()
_STOPWORDS = frozenset("a an and are as at be by for from how in into is it of on or that the this to "
                       "vs versus what which with why can do does using use based via".split())


def relaxed_queries(query: str) -> list[str]:
    """原检索式 → 0 命中时的放宽序列：[原式（loose AND）, 去停用词 OR]；不足 3 个实词不放宽。

    实测（2026-10-25）：两条 6~10 词检索式原式均 0 命中，OR 后按相关度排序的前几条多数对题，
    混入的跨领域噪声交给后续筛选与重排；去停用词 AND 与原式结果相同，不单列。
    """
    q = query.strip()
    terms = [t for t in re.findall(r"[\w-]+", q) if t.lower() not in _STOPWORDS]
    return [q, " OR ".join(terms)] if len(terms) >= 3 else [q]


def _shared_throttle():
    loop = asyncio.get_running_loop()
    if loop not in _shared_throttles:
        _shared_throttles[loop] = AdaptiveThrottle(start=0.6, floor=0.5, cap=8.0, concurrency=2)
    return _shared_throttles[loop]


class ScopusProvider:
    name = "scopus"

    def __init__(self, api_key: str = ""):
        self.api_key = (api_key or "").strip()
        self._client: httpx.AsyncClient | None = None
        self._throttle = None
        self._auth_fails = 0
        self._disabled = False
        self.last_request_status = "unknown"
        self._complete_view: bool | None = None   # 机构订阅可达时检索可带摘要（COMPLETE 视图），首次检索时探测

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Accept": "application/json"}
            if self.api_key:
                # 头名必须是 X-ELS-APIKey：写成 X-APIKey 时有效 Key 也 401（实测踩坑）
                headers["X-ELS-APIKey"] = self.api_key
            self._client = httpx.AsyncClient(timeout=30.0, headers=headers)
        return self._client

    async def _get(self, path: str, params: dict | None = None) -> Optional[dict]:
        """GET + JSON。自适应限速；鉴权/配额失败计入熔断；瞬时错误重试 2 次。返回 None 表示放弃。"""
        if self._disabled:
            return None
        self._throttle = self._throttle or _shared_throttle()
        from ...rate_limiter import account_source_request
        for attempt in range(3):
            async with self._throttle:
                if self._disabled:
                    return None
                try:
                    account_source_request()
                    r = await self._http().get(f"{SCOPUS_BASE}{path}", params=params)
                    if r.status_code == 200:
                        self.last_request_status = "ok"
                        self._throttle.on_success()
                    if r.status_code in (401, 403):
                        self.last_request_status = "auth_error"
                        self._auth_fails += 1
                        if self._auth_fails >= _AUTH_FAIL_LIMIT:
                            self._disabled = True
                            logger.warning(
                                "Scopus 连续 %d 次鉴权失败（HTTP %s），本次运行内熔断。"
                                "请检查 elsevier_api_key 或确认校园网机构订阅可达。",
                                self._auth_fails, r.status_code,
                            )
                        return None
                    if r.status_code == 429:
                        self.last_request_status = "rate_limited"
                        if "QUOTA_EXCEEDED" in (r.text or ""):
                            self.last_request_status = "quota_exhausted"
                            self._disabled = True
                            logger.warning("Scopus 配额耗尽（QUOTA_EXCEEDED），本次运行内熔断")
                            return None
                        wait = parse_retry_after(r.headers, default_wait=2.0 * (attempt + 1))
                        self._throttle.on_rate_limited(wait)
                        logger.warning(f"Scopus 429, retry {attempt + 1}/3 in {wait:.1f}s")
                        await asyncio.sleep(wait)
                        continue
                    r.raise_for_status()
                    return r.json()
                except httpx.HTTPStatusError as e:
                    self.last_request_status = "error"
                    logger.warning(f"Scopus HTTP {e.response.status_code}: {e}")
                    return None
                except (httpx.TimeoutException, httpx.HTTPError) as e:
                    self.last_request_status = "timeout" if isinstance(e, httpx.TimeoutException) else "error"
                    if attempt == 2:
                        logger.warning(f"Scopus request failed: {e}")
                        return None
                    await asyncio.sleep(1.0 * (attempt + 1))
        return None

    # ── Scopus Search 结果条目 → RecallCandidate ──────────────────────────

    @staticmethod
    def _entry_year(entry: dict) -> int | None:
        # Scopus Search 实际返回 prism:coverDate（请求 publicationDate 也不给，实测）；
        # in-press 条目的 coverDate 可能是占位未来年份，由下游 compute_metrics 的
        # year <= now+1 守卫兜住
        raw = str(entry.get("prism:coverDate") or entry.get("prism:publicationDate") or "")[:4]
        try:
            return int(raw) if len(raw) == 4 else None
        except ValueError:
            return None

    @staticmethod
    def _entry_doi(entry: dict) -> str | None:
        doi = entry.get("prism:doi")
        return str(doi).replace("https://doi.org/", "") if doi else None

    @classmethod
    def _entries_to_candidates(
        cls, entries: list[dict], *, source: RecallSource,
        seed_paper_id: str | None = None, seed_relation: str | None = None,
        limit: int = 50,
    ) -> list[RecallCandidate]:
        out: list[RecallCandidate] = []
        for rank, entry in enumerate(entries[:limit]):
            title = entry.get("dc:title")
            if not title:
                continue
            authors = entry.get("dc:creator") or None
            # 期刊身份随检索免费带回（source-id 供 Serial Title 批量查 SJR/SNIP；
            # 2026-09-02 实测 search 响应支持这两个字段且不占额外调用）
            extra = {
                "scopus_id": cls._entry_eid(entry),
                "venue_source_id": str(entry.get("source-id") or "") or None,
                "venue_name": entry.get("prism:publicationName") or None,
                "publication_type": entry.get("prism:aggregationType") or entry.get("subtypeDescription"),
                "publisher": entry.get("dc:publisher"),
            }
            flag = entry.get("openaccessFlag")
            cand = build_recall_candidate(
                title=title,
                doi=cls._entry_doi(entry),
                abstract=(entry.get("dc:description") or "").strip() or None,   # 仅 COMPLETE 视图有
                is_open_access=flag if isinstance(flag, bool) else None,
                year=cls._entry_year(entry),
                authors=f"{authors} et al." if authors else None,
                source=source,
                source_rank=rank,
                raw_score=float(entry.get("citedby-count") or 0),
                seed_paper_id=seed_paper_id,
                seed_relation=seed_relation,
                citation_count=int(entry.get("citedby-count") or 0),
                provenance=["provider:scopus", f"method:{source.value}"],
                extra={k: v for k, v in extra.items() if v},
            )
            if cand:
                out.append(cand)
        return out

    @staticmethod
    def _search_entries(data: Optional[dict]) -> list[dict]:
        if not data:
            return []
        results = data.get("search-results") or {}
        # 零结果时 Scopus 不返回 entry 键，或返回 [{"error": "Result set was empty"}]
        return [e for e in (results.get("entry") or []) if isinstance(e, dict) and "error" not in e]

    @staticmethod
    def _entry_eid(entry: dict) -> str | None:
        # dc:identifier 形如 "SCOPUS_ID:84942127758"；部分响应只有 eid 字段
        ident = entry.get("dc:identifier") or ""
        if ident.upper().startswith("SCOPUS_ID:"):
            return ident.split(":", 1)[1]
        eid = entry.get("eid") or ""
        return eid or None

    # ── ResearchProvider 协议实现 ─────────────────────────────────────────

    async def find_paper_id(self, title: str, doi: str = "", arxiv_id: str = "") -> Optional[str]:
        if doi:
            data = await self._get("/content/search/scopus", {
                "query": f"DOI({doi})",
                "count": 1,
                "field": "dc:identifier,dc:title,eid",
            })
            entries = self._search_entries(data)
            if entries:
                return self._entry_eid(entries[0])
        if title:
            query = f'TITLE("{title}")'
            data = await self._get("/content/search/scopus", {
                "query": query,
                "count": 5,
                "field": "dc:identifier,dc:title,eid",
            })
            for entry in self._search_entries(data):
                et = entry.get("dc:title") or ""
                if normalize_title(et) == normalize_title(title):
                    return self._entry_eid(entry)
        return None

    async def get_paper_references(self, paper_id: str, limit: int = 50) -> list[dict]:
        """Abstract Retrieval `view=REF` → 参考文献列表（legacy dict 格式；每页 40 条，按需翻页）。
        路径是 scopus_id（下划线）：原来写成 scopus-id 一直 400，参考文献回溯从未生效（E25）。
        条目自带题名、DOI、年份与被引数，但没有摘要（回溯时再按 DOI 批量取）。
        当前 Key 只能取第一页（前 40 条，按正文引用顺序，多为引言里的基础文献）；翻页（startref=41）返回 400。"""
        sid = str(paper_id or "").replace("SCOPUS_ID:", "").strip()
        if not sid:
            return []
        out: list[dict] = []
        start = 1
        while len(out) < limit and start == 1:
            data = await self._get(f"/content/abstract/scopus_id/{sid}",
                                   {"view": "REF", "startref": "1", "refcount": "40"})
            resp = (data or {}).get("abstracts-retrieval-response") or {}
            block = resp.get("references") or {}
            refs = block.get("reference") or []
            if not refs:   # 旧版结构（bib.record.reference-list）兜底
                record = (resp.get("bib") or {}).get("record") or {}
                refs = ((record.get("reference-list") or {}).get("reference")) or []
            if isinstance(refs, dict):
                refs = [refs]
            for ref in refs:
                info = ref.get("ref-info") or {}
                title = ref.get("title") or (info.get("ref-title") or {}).get("ref-titletext") or ""
                title = str(title).strip()
                if not title:
                    continue
                date = str(ref.get("prism:coverDate") or "")
                out.append({
                    "ext_id": str(ref.get("scopus-id") or "") or None,
                    "title": title,
                    "year": int(date[:4]) if date[:4].isdigit() else None,
                    "citation_count": int(ref.get("citedby-count") or 0),
                    "doi": str(ref.get("ce:doi") or info.get("ref-doi") or "") or None,
                    "arxiv_id": None,
                    "pdf_url": None,
                })
            start += 40
        return out[:limit]

    async def works_by_dois(self, dois: list[str], *, seed_paper_id: str | None = None,
                            seed_relation: str = "ref_chain") -> list[RecallCandidate]:
        """按 DOI 批量取完整检索记录（每次 25 个；COMPLETE 视图可用时带摘要），用于参考文献回溯的候选。"""
        fields, view = await self._search_fields()
        clean = [d.strip() for d in dois if d and re.fullmatch(r"10\.\S+", d.strip())]
        out: list[RecallCandidate] = []
        for i in range(0, len(clean), 25):
            chunk = clean[i:i + 25]
            data = await self._get("/content/search/scopus", {
                "query": " OR ".join(f'DOI("{d}")' for d in chunk), "count": len(chunk), "field": fields, **view})
            out += self._entries_to_candidates(self._search_entries(data), source=RecallSource.BACKWARD_REF,
                                               seed_paper_id=seed_paper_id, seed_relation=seed_relation,
                                               limit=len(chunk))
        return out

    async def get_paper_citations(self, paper_id: str, limit: int = 50) -> list[RecallCandidate]:
        data = await self._get("/content/search/scopus", {
            "query": f"REF({paper_id})",
            "count": min(limit, 25),
            "sort": "-citedby-count",
            "field": "dc:identifier,dc:title,eid,prism:doi,prism:coverDate,prism:publicationDate,citedby-count",
        })
        entries = self._search_entries(data)
        return self._entries_to_candidates(
            entries, source=RecallSource.FORWARD_CIT,
            seed_paper_id=paper_id, seed_relation="cited_by", limit=limit,
        )

    async def get_recommendations(self, paper_id: str, limit: int = 50) -> list[RecallCandidate]:
        # Scopus 无免费推荐接口；recommend 通道缺席，RRF 其余通道不受影响
        return []

    @staticmethod
    def build_search_query(query: str, *, academic_only: bool = False) -> str:
        """检索式 → Scopus Query 语法。

        ⚠ 不给整串加引号：TITLE-ABS-KEY("a b c d e f") 是**精确短语**匹配，多词关键
        词串会被要求逐字连续出现（实测 6 词短语 0 命中）；无引号多词是 loose AND
        （同一查询 2058 命中）。academic_only 追加学术类型过滤排干扰。
        """
        # 括号、方括号与问号在 Scopus 里是语法字符：问题原句里的“(ECMO)”会让整条检索 400（ResearchQA 实测）
        q = re.sub(r"\s+", " ", re.sub(r"[()\[\]{}?]", " ", query)).strip()
        base = f"TITLE-ABS-KEY({q})"
        return f"{base} AND DOCTYPE({_ACADEMIC_DOCTYPES})" if academic_only else base

    async def search_works(self, query: str, limit: int = 20, *, academic_only: bool = False,
                           order: str = "relevance") -> list[RecallCandidate]:
        """关键词检索。Scopus Search 单页上限 25 条（count>25 直接 400，实测），
        超出时按 start 翻页补齐。order="cited" 走被引降序（经典层补充，relevance
        排序天然偏新文献，奠基论文需显式按被引召回）。"""
        if not query or not query.strip():
            return []
        entries: list[dict] = []
        sort = "-citedby-count" if order == "cited" else "-relevancy"
        fields, view = await self._search_fields()
        # 规划员给的检索式常是 6~10 词的句子，全部词同时出现时命中很少或为 0（E10：Q3 前三条检索式 arXiv 与
        # Scopus 全 0；ResearchQA 医学题每条只命中 4~6 篇，而本地镜像每条给 45 篇 cs 论文）。原式不足名额时用去停用词
        # 的 OR 补满（OR 按相关度排序，命中词越多越靠前；只取第一页），原式结果排在前面
        want = min(limit, 100)
        for i, variant in enumerate(relaxed_queries(query)):
            if i > 0 and len(entries) >= want:
                break
            got: list[dict] = []
            remaining = want if i == 0 else 25  # 放宽式只取第一页
            # 放宽式几乎命中全库：按被引降序会排出 GLOBOCAN、t-SNE 这类与题无关的全库高被引论文（ResearchQA 实测）；
            # 放宽式一律按相关度取一页，order="cited" 时再在本地按被引重排
            page_sort = sort if i == 0 else "-relevancy"
            start = 0
            while remaining > 0:
                count = min(25, remaining)
                data = await self._get("/content/search/scopus", {
                    "query": self.build_search_query(variant, academic_only=academic_only),
                    "count": count,
                    "start": start,
                    "sort": page_sort,
                    "field": fields,
                    **view,
                })
                page = self._search_entries(data)
                got.extend(page)
                if len(page) < count:  # 没有更多结果
                    break
                remaining -= count
                start += count
            if i > 0:
                seen = {self._entry_eid(e) for e in entries}
                got = [e for e in got if self._entry_eid(e) not in seen]
                if order == "cited":
                    got.sort(key=lambda e: -int(e.get("citedby-count") or 0))
            entries.extend(got[: max(0, want - len(entries))] if i > 0 else got)
        return self._entries_to_candidates(
            entries, source=RecallSource.SEARCH, seed_relation="search_match", limit=limit,
        )

    async def _search_fields(self) -> tuple[str, dict]:
        """检索返回的字段与视图：校园网/机构 IP 下 COMPLETE 视图带摘要，非 arXiv 论文（期刊为主的学科）不再只有题名。"""
        fields = ("dc:identifier,dc:title,eid,prism:doi,prism:coverDate,prism:publicationDate,citedby-count,"
                  "dc:creator,source-id,prism:publicationName,prism:aggregationType,subtypeDescription")
        if await self._complete_view_ok():
            return fields + ",dc:description,openaccessFlag", {"view": "COMPLETE"}
        return fields, {}

    async def search_titles(self, title: str, limit: int = 3) -> list[RecallCandidate]:
        """按题名精确检索（TITLE 短语），用于查找模型点名的论文；同题判定由调用方做。"""
        clean = re.sub(r'["(){}\[\]]', " ", title or "").strip()
        if not clean:
            return []
        fields, view = await self._search_fields()
        data = await self._get("/content/search/scopus", {"query": f'TITLE("{clean}")', "count": max(1, min(limit, 25)),
                                                          "field": fields, **view})
        return self._entries_to_candidates(self._search_entries(data), source=RecallSource.SEARCH,
                                           seed_relation="title_lookup", limit=limit)

    async def abstracts_by_dois(self, dois: list[str]) -> dict[str, str]:
        """按 DOI 批量取摘要（DOI(a) OR DOI(b) …，每次 25 个，COMPLETE 视图）：召回后仍缺摘要的论文兜底。
        COMPLETE 视图不可用（校外）时返回空。"""
        if not await self._complete_view_ok():
            return {}
        found: dict[str, str] = {}
        clean = [d.strip() for d in dois if d and re.fullmatch(r"10\.\S+", d.strip())]
        for i in range(0, len(clean), 25):
            chunk = clean[i:i + 25]
            query = " OR ".join(f'DOI("{d}")' for d in chunk)
            data = await self._get("/content/search/scopus", {"query": query, "count": len(chunk),
                                                              "field": "prism:doi,dc:description",
                                                              "view": "COMPLETE"})
            for e in self._search_entries(data):
                doi, ab = str(e.get("prism:doi") or "").lower(), (e.get("dc:description") or "").strip()
                if doi and ab:
                    found[doi] = ab
        return found

    async def _complete_view_ok(self) -> bool:
        """COMPLETE 视图（含摘要）是否可用：取决于出口 IP 是否在机构订阅内，每个实例只探测一次。
        不走 _get——普通 Key 在校外会 401，这是预期的降级，不能计入鉴权熔断。"""
        if self._complete_view is not None:
            return self._complete_view
        if not self.api_key or self._disabled:
            return False
        self._throttle = self._throttle or _shared_throttle()
        from ...rate_limiter import account_source_request
        status = None
        for attempt in range(3):
            try:
                async with self._throttle:
                    account_source_request()
                    r = await self._http().get(f"{SCOPUS_BASE}/content/search/scopus", params={
                        "query": "TITLE-ABS-KEY(retrieval)", "count": 1, "view": "COMPLETE",
                        "field": "dc:identifier,dc:description"}, timeout=15.0)
                status = r.status_code
            except httpx.HTTPError:
                status = None
            if status in (200, 401, 403):
                break
            await asyncio.sleep(1.5 * (attempt + 1))
        # 只有 200 / 401 / 403 才是确定的结论；限流（429）、5xx、超时只算这一次没探测成功，下次检索再探测——
        # 原来一次 429 就让整个实例此后都不带摘要（E25：E21 召回 2784 篇里 23% 因此没有摘要，进不了证据库）
        if status in (200, 401, 403):
            self._complete_view = status == 200
            logger.info("Scopus COMPLETE 视图（摘要）%s", "可用" if self._complete_view else "不可用，检索不带摘要")
            return self._complete_view
        logger.warning("Scopus COMPLETE 视图探测未成功（%s），本次检索不带摘要，下次再探测", status)
        return False

    async def health_check(self, *, timeout: float = 10.0) -> dict:
        """权限体检：带 Key 发一次最小真实 Scopus Search（count=1），不是 ping 可达。

        200 才说明 Key+出口 IP 都有检索权；401/403 是确定性失败，要在烧正式
        检索配额之前暴露。刻意不走 _get（它会重试 3 次并吞掉状态码），也不
        计入熔断计数——预检失败不该影响后续独立调用的重试语义。
        """
        if not self.api_key:
            return {"name": self.name, "status": "unconfigured",
                    "detail": "未配置 Elsevier API Key（auto 模式下不会启用 Scopus）",
                    "latency_ms": None, "http_status": None}
        start = time.monotonic()
        try:
            self._throttle = self._throttle or _shared_throttle()
            async with self._throttle:
                r = await self._http().get(f"{SCOPUS_BASE}/content/search/scopus", params={
                    "query": "TITLE-ABS-KEY(retrieval)", "count": 1, "field": "dc:identifier",
                }, timeout=timeout)
                if r.status_code == 429:
                    self._throttle.on_rate_limited(parse_retry_after(r.headers, 6.0))
        except httpx.HTTPError as e:
            return {"name": self.name, "status": "network_error",
                    "detail": f"连接失败（{type(e).__name__}）",
                    "latency_ms": None, "http_status": None}
        latency = int((time.monotonic() - start) * 1000)
        base = {"name": self.name, "latency_ms": latency, "http_status": r.status_code}
        if r.status_code == 200:
            return {**base, "status": "ok", "detail": "检索权限正常"}
        if r.status_code == 401:
            return {**base, "status": "auth_error",
                    "detail": "HTTP 401：Key 无效或未随请求生效"}
        if r.status_code == 403:
            return {**base, "status": "auth_error",
                    "detail": "HTTP 403：无检索权限（Key 权限档不足或出口 IP 不在订阅范围）"}
        if r.status_code == 429:
            return {**base, "status": "rate_limited",
                    "detail": "HTTP 429：限速或配额耗尽（权限仍在）"}
        return {**base, "status": "error", "detail": f"HTTP {r.status_code}"}

    async def aclose(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── 期刊级文献计量指标（Serial Title API）────────────────────────────

    @staticmethod
    def _latest_metric(items: list | dict | None) -> float | None:
        """SJRList/SNIPList 形如 {"SJR": [{"@year": "2025", "$": "19.713"}, ...]}，取最新年份的值。"""
        if isinstance(items, dict):
            items = items.get("SJR") or items.get("SNIP") or []
        if not isinstance(items, list) or not items:
            return None
        def _year(it: dict) -> int:
            try:
                return int(str(it.get("@year") or "0"))
            except ValueError:
                return 0
        best = max((it for it in items if isinstance(it, dict)), key=_year, default=None)
        try:
            return float(best.get("$")) if best is not None else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _parse_serial_entries(cls, entries: list[dict]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for e in entries:
            if not isinstance(e, dict):
                continue
            sid = str(e.get("source-id") or "")
            if not sid:
                continue
            citescore = None
            cs_info = e.get("citeScoreYearInfoList") or {}
            if isinstance(cs_info, dict):
                try:
                    citescore = float(cs_info.get("citeScoreCurrentMetric"))
                except (TypeError, ValueError):
                    citescore = None
            out[sid] = {
                "name": e.get("dc:title") or "",
                "sjr": cls._latest_metric(e.get("SJRList")),
                "snip": cls._latest_metric(e.get("SNIPList")),
                "citescore": citescore,
            }
        return out

    async def get_serial_metrics(self, source_ids: list[str]) -> dict[str, dict]:
        """按 source-id **批量**查期刊 SJR / SNIP / CiteScore。

        实测（2026-09-02）：`/content/serial/title?source-id=a,b,c` 逗号分隔一次返回多条，
        且 SJRList/SNIPList/citeScoreYearInfoList 均在个人 dev Key 权限内（Citation
        Overview/Count 反而 404）。分块 20 个防 URL 过长。返回 {source_id: 指标}。
        """
        ids = [str(s).strip() for s in source_ids if str(s).strip()]
        out: dict[str, dict] = {}
        for i in range(0, len(ids), 20):
            chunk = ",".join(ids[i:i + 20])
            data = await self._get("/content/serial/title", {"source-id": chunk})
            entries = (data or {}).get("serial-metadata-response", {}).get("entry") or []
            out.update(self._parse_serial_entries(entries))
        return out
