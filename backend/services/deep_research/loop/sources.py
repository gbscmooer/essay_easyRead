"""数据源：检索、按题名解析、参考文献列表、补摘要。只做 I/O，不做相关性判断（那是 Selector 的事）。

- arxiv    本地镜像（字面 + 向量两路，摘要齐全、不限流）；镜像未建时退回 arXiv 在线 API
- scopus   在线检索（校园网下带摘要；校外只有题名，靠 OpenAlex 按 DOI 补摘要）
- openalex 在线检索，参考文献列表（referenced_works）与按 DOI 批量补摘要

单源失败只记入审计，不中断循环。
"""

from __future__ import annotations

import asyncio
import logging

from .papers import dedupe_candidates, title_key

logger = logging.getLogger(__name__)

SEARCH_TIMEOUT = 120.0


class Sources:
    """search_one(source, query, limit, order) 给出时只用它检索（评测的本地语料），enabled 即它响应的源名。"""

    def __init__(self, *, enabled: tuple[str, ...], dead: set[str] | None = None, online: bool = True,
                 search_one=None):
        self.online = online
        self.custom = search_one
        self.enabled = tuple(s for s in enabled if s not in (dead or set())) if online or search_one else ()
        self._providers: dict[str, object] = {}
        self.audit: dict[str, dict] = {s: {"calls": 0, "hits": 0, "errors": 0} for s in self.enabled}

    # ── 提供方 ──────────────────────────────────────────────────────────────
    def _mirror(self):
        from ..arxiv_mirror import default_mirror

        return default_mirror()

    def _provider(self, name: str):
        if name not in self._providers:
            from ...ai_config import get_research_config
            from ..providers import _elsevier_key

            cfg = dict(get_research_config())
            if name == "scopus":
                from ..providers.scopus import ScopusProvider

                self._providers[name] = ScopusProvider(api_key=_elsevier_key(cfg))
            else:
                from ..providers.openalex import get_shared_openalex_provider

                self._providers[name] = get_shared_openalex_provider(str(cfg.get("email") or ""))
        return self._providers[name]

    async def aclose(self) -> None:
        scopus = self._providers.get("scopus")
        if scopus is not None and hasattr(scopus, "aclose"):
            try:
                await scopus.aclose()
            except Exception:  # noqa: BLE001
                pass

    # ── 检索 ────────────────────────────────────────────────────────────────
    async def _search_one(self, source: str, query: str, limit: int) -> list:
        if self.custom is not None:
            return await self.custom(source, query, limit, "relevance")
        if source == "arxiv":
            mirror = self._mirror()
            if mirror is not None:
                return await mirror.as_search_one()(source, query, limit, "relevance")
            from ..arxiv_client import search_topic
            from ....models.recall import RecallSource, build_recall_candidate

            rows = await search_topic(query, limit=limit)
            out = []
            for row in rows:
                cand = build_recall_candidate(
                    title=row["title"], arxiv_id=row["arxiv_id"], doi=row.get("doi"), year=row.get("year"),
                    authors=row.get("authors"), abstract=row.get("abstract"), pdf_url=row.get("pdf_url"),
                    citation_count=row.get("citation_count") or 0, source=RecallSource.SEARCH,
                    provenance=["provider:arxiv"])
                if cand:
                    out.append(cand)
            return out
        provider = self._provider(source)
        return await provider.search_works(query, limit=limit, academic_only=True, order="relevance")

    async def search(self, query: str, limit: int) -> list[dict]:
        """一条检索式在所有可用源上并行检索，去重后返回（各源交替排列，先后不代表相关性）。"""
        if not self.enabled or not query.strip():
            return []

        async def one(src: str) -> list[dict]:
            self.audit[src]["calls"] += 1
            try:
                rows = await asyncio.wait_for(self._search_one(src, query, limit), timeout=SEARCH_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 —— 单源失败不影响其他源
                self.audit[src]["errors"] += 1
                logger.warning("search %s failed for %r: %s", src, query, exc)
                return []
            papers = dedupe_candidates(rows)
            self.audit[src]["hits"] += len(papers)
            for p in papers:
                p["source_query"] = query
                p.setdefault("provider", src)
            return papers

        groups = await asyncio.gather(*(one(s) for s in self.enabled))
        interleaved = [p for row in _zip_longest(groups) for p in row if p]
        return dedupe_candidates(interleaved)

    # ── 按题名解析（展开时把引用条目变成论文） ─────────────────────────────
    async def resolve_titles(self, titles: list[str]) -> dict[str, dict]:
        """题名 → 论文（同题才收，不用相近论文顶替）。先查本地镜像，再查在线源。返回 {title_key: paper}。"""
        from ..evidence_search import _same_title

        wanted = {title_key(t): t for t in titles if t and len(t) > 10}
        found: dict[str, dict] = {}
        if self.custom is not None:
            return found
        mirror = self._mirror()
        if mirror is not None and wanted:
            def lookup() -> dict[str, dict]:
                rows = mirror.match([(None, t) for t in wanted.values()])
                hits = {}
                for (k, t), row in zip(wanted.items(), rows):
                    if row is None:
                        with mirror._lock:
                            near = mirror.lexical(t, 5)
                        row = next((c for c in near if _same_title(t, c.get("title") or "")), None)
                    if row:
                        hits[k] = row
                return hits

            try:
                rows = await asyncio.to_thread(lookup)
            except Exception as exc:  # noqa: BLE001
                logger.warning("mirror title lookup failed: %s", exc)
                rows = {}
            for k, row in rows.items():
                found[k] = _mirror_row(row)
        rest = [t for k, t in wanted.items() if k not in found]
        online = [s for s in ("openalex", "scopus") if s in self.enabled]
        if rest and online:
            src = online[0]
            sem = asyncio.Semaphore(4)

            async def one(t: str) -> tuple[str, dict | None]:
                async with sem:
                    try:
                        provider = self._provider(src)
                        if src == "scopus":
                            cands = await provider.search_titles(t, 3)
                        else:
                            cands = await provider.search_works(t, limit=3)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("title lookup %s failed for %r: %s", src, t, exc)
                        return t, None
                hit = next((p for p in dedupe_candidates(cands) if _same_title(t, p.get("title") or "")), None)
                return t, hit

            for t, hit in await asyncio.gather(*(one(t) for t in rest)):
                if hit:
                    found[title_key(t)] = hit
        return found

    # ── 参考文献列表（拿不到 arXiv HTML 时的展开兜底） ─────────────────────
    async def references(self, paper: dict, limit: int) -> list[dict]:
        rows: list[dict] = []
        via = ""
        if self.custom is not None:
            return []
        try:
            if paper.get("openalex_id") and "openalex" in self.enabled:
                via = "openalex"
                rows = await self._provider("openalex").get_paper_references(str(paper["openalex_id"]), limit=limit)
            elif paper.get("scopus_id") and "scopus" in self.enabled:
                via = "scopus"
                rows = await self._provider("scopus").get_paper_references(str(paper["scopus_id"]), limit=limit)
            elif (paper.get("doi") or paper.get("arxiv_id")) and "openalex" in self.enabled:
                via = "openalex"
                oa = self._provider("openalex")
                oid = await oa.find_paper_id(paper.get("title") or "", doi=paper.get("doi") or "",
                                             arxiv_id=paper.get("arxiv_id") or "")
                rows = await oa.get_paper_references(oid, limit=limit) if oid else []
        except Exception as exc:  # noqa: BLE001
            logger.warning("references failed for %s: %s", paper.get("key"), exc)
            return []
        from ....models.recall import RecallSource, build_recall_candidate

        cands = []
        for r in rows or []:
            ext = r.get("ext_id")
            cand = build_recall_candidate(
                title=r.get("title") or "", doi=r.get("doi"), arxiv_id=r.get("arxiv_id"), year=r.get("year"),
                citation_count=r.get("citation_count") or 0, pdf_url=r.get("pdf_url"),
                openalex_id=ext if via == "openalex" else None,
                extra={"scopus_id": ext} if via == "scopus" and ext else None,
                source=RecallSource.BACKWARD_REF, provenance=[f"provider:{via}", "method:reference_list"])
            if cand:
                cands.append(cand)
        return dedupe_candidates(cands)

    # ── 补摘要（Selector 读摘要判断；Scopus 校外不带摘要） ─────────────────
    async def fill_abstracts(self, papers: list[dict]) -> int:
        missing = [p for p in papers if not (p.get("abstract") or "").strip()]
        if not missing or self.custom is not None:
            return 0
        filled = 0
        mirror = self._mirror()
        if mirror is not None:
            try:
                rows = await asyncio.to_thread(mirror.match, [(p.get("doi"), p.get("title") or "") for p in missing])
            except Exception as exc:  # noqa: BLE001
                logger.warning("mirror abstract match failed: %s", exc)
                rows = [None] * len(missing)
            for p, row in zip(missing, rows):
                if row and (row.get("abstract") or "").strip():
                    p["abstract"] = row["abstract"]
                    p.setdefault("arxiv_id", row.get("arxiv_id"))
                    filled += 1
        with_doi = [p for p in missing if not (p.get("abstract") or "").strip() and p.get("doi")]
        if with_doi and self.online:
            try:
                got = await self._provider("openalex").get_abstracts_by_dois([p["doi"] for p in with_doi])
            except Exception as exc:  # noqa: BLE001
                logger.warning("openalex abstract backfill failed: %s", exc)
                got = {}
            for p in with_doi:
                text = got.get(str(p["doi"]).lower())
                if text:
                    p["abstract"] = text
                    filled += 1
        return filled


def _mirror_row(row: dict) -> dict:
    from ....models.recall import RecallSource, build_recall_candidate

    cand = build_recall_candidate(
        title=row["title"], arxiv_id=row["arxiv_id"], doi=row.get("doi"), year=row.get("year"),
        authors=row.get("authors"), abstract=row.get("abstract"),
        pdf_url=f"https://arxiv.org/pdf/{row['arxiv_id']}", citation_count=0,
        source=RecallSource.BACKWARD_REF, provenance=["provider:arxiv", "index:local_mirror", "method:title_lookup"])
    return dedupe_candidates([cand])[0]


def _zip_longest(groups: list[list]) -> list[tuple]:
    import itertools

    return list(itertools.zip_longest(*groups)) if groups else []
