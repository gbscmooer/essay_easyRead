"""
Reference Backfill — 存量论文引用回填 + 图谱重建

老工作区（v2.x 引用体系上线前）的 PaperReference 表为空，而现行 graph_builder 的
cites / bibcoupling / cocitation 边完全依赖该表——直接重建图会退化成纯 similar 图。
本服务用 Semantic Scholar 引用 API 为工作区论文回填真实引用关系：

1. 离线身份补齐：解析 legacy external_id → doi / arxiv / s2 / canonical（不联网）
2. 逐篇拉取 S2 references（路径 id 优先 s2_id / ``DOI:x`` / ``arXiv:x``，避免额外查询）
3. 引用落库 upsert_references + 预解析 cited_paper_id（s2 → doi → arxiv → 唯一精确标题）
4. 纯噪声标题（PDF 页眉误提取）用 S2 规范元数据修复
5. 重建并持久化工作区图谱

幂等可续跑：默认跳过已有引用记录的论文，中断后重跑只补缺口。
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlmodel import Session, select

from ...models.paper import Paper
from ...models.research import ResearchRun
from ...models.workspace import WorkspacePaper
from ..database import get_engine
from ..paper_identity import (
    apply_identity_to_paper,
    clean_display_title,
    is_junk_title,
    normalize_arxiv_id,
    normalize_doi,
    normalize_openalex_id,
    normalize_s2_id,
    normalize_title,
    paper_to_work,
)
from ..semantic_scholar import find_s2_paper_id, get_paper_metadata, get_paper_references
from .providers.openalex import OpenAlexProvider
from .reference_store import list_by_citing, resolve_cited_links, upsert_references

logger = logging.getLogger(__name__)

REF_SOURCE = "s2_backfill"
REF_SOURCE_OPENALEX = "openalex_backfill"
DEFAULT_REFS_LIMIT = 100

_openalex: OpenAlexProvider | None = None


def _get_openalex() -> OpenAlexProvider:
    global _openalex
    if _openalex is None:
        _openalex = OpenAlexProvider()
    return _openalex


@dataclass
class _LocalIndex:
    """工作区论文的强身份索引，用于把外部引用预解析到本地 paper_id。"""

    by_s2: dict[str, int] = field(default_factory=dict)
    by_doi: dict[str, int] = field(default_factory=dict)
    by_arxiv: dict[str, int] = field(default_factory=dict)
    by_openalex: dict[str, int] = field(default_factory=dict)
    by_title: dict[str, int] = field(default_factory=dict)  # 仅无歧义的精确规范化标题


def _load_ws_papers(session: Session, ws_id: int) -> list[Paper]:
    rows = session.exec(
        select(Paper)
        .join(WorkspacePaper, WorkspacePaper.paper_id == Paper.id)
        .where(WorkspacePaper.workspace_id == ws_id)
        .order_by(Paper.id)
    ).all()
    # 防御性去重（历史 join 异常可能产生重复行）
    seen: set[int] = set()
    out: list[Paper] = []
    for p in rows:
        if p.id in seen:
            continue
        seen.add(p.id)
        out.append(p)
    return out


def _build_local_index(papers: list[Paper]) -> _LocalIndex:
    idx = _LocalIndex()
    title_counts: Counter[str] = Counter()
    for p in papers:
        if p.id is None:
            continue
        ns2 = normalize_s2_id(p.s2_id)
        if ns2:
            idx.by_s2[ns2] = p.id
        ndoi = normalize_doi(p.doi)
        if ndoi:
            idx.by_doi[ndoi] = p.id
        narxiv = normalize_arxiv_id(p.arxiv_id)
        if narxiv:
            idx.by_arxiv[narxiv] = p.id
        noa = normalize_openalex_id(p.openalex_id)
        if noa:
            idx.by_openalex[noa] = p.id
        if not is_junk_title(p.title):
            tn = p.title_normalized or normalize_title(p.title)
            if tn:
                title_counts[tn] += 1
                idx.by_title[tn] = p.id
    # 同名歧义标题不参与解析（避免错误挂边）
    for tn, cnt in title_counts.items():
        if cnt > 1:
            idx.by_title.pop(tn, None)
    return idx


def _resolve_ref_locally(ref: dict, idx: _LocalIndex) -> tuple[int | None, float]:
    """按 s2/openalex → doi → arxiv → 唯一精确标题 解析引用到本地 paper_id。"""
    ns2 = normalize_s2_id(ref.get("s2_id"))
    if ns2 and ns2 in idx.by_s2:
        return idx.by_s2[ns2], 1.0
    noa = normalize_openalex_id(ref.get("ext_id"))
    if noa and noa in idx.by_openalex:
        return idx.by_openalex[noa], 1.0
    ndoi = normalize_doi(ref.get("doi"))
    if ndoi and ndoi in idx.by_doi:
        return idx.by_doi[ndoi], 1.0
    narxiv = normalize_arxiv_id(ref.get("arxiv_id"))
    if narxiv and narxiv in idx.by_arxiv:
        return idx.by_arxiv[narxiv], 1.0
    tn = normalize_title(ref.get("title"))
    if tn and tn in idx.by_title:
        return idx.by_title[tn], 0.8
    return None, 0.0


def _s2_path_id(paper: Paper) -> str | None:
    """S2 API 路径 id：s2 paperId / DOI:x / arXiv:x。"""
    ns2 = normalize_s2_id(paper.s2_id)
    if ns2:
        return ns2
    ndoi = normalize_doi(paper.doi)
    if ndoi:
        return f"DOI:{ndoi}"
    narxiv = normalize_arxiv_id(paper.arxiv_id)
    if narxiv:
        return f"arXiv:{narxiv}"
    return None


async def _repair_title_from_s2(session: Session, paper: Paper, path_id: str) -> bool:
    """用 S2 规范元数据修复纯噪声标题；返回是否修复。"""
    meta = await get_paper_metadata(path_id)
    if not meta or not meta.get("title"):
        return False
    paper.title = meta["title"]
    paper.title_normalized = normalize_title(meta["title"])
    if meta.get("year") and not paper.year:
        paper.year = meta["year"]
    if meta.get("venue") and not paper.venue:
        paper.venue = meta["venue"]
    if meta.get("authors") and not paper.authors:
        paper.authors = meta["authors"]
    if meta.get("doi") and not paper.doi:
        paper.doi = normalize_doi(meta["doi"])
    if meta.get("arxiv_id") and not paper.arxiv_id:
        paper.arxiv_id = normalize_arxiv_id(meta["arxiv_id"])
    if meta.get("s2_id") and not paper.s2_id:
        paper.s2_id = normalize_s2_id(meta["s2_id"])
    paper.updated_at = datetime.now(timezone.utc)
    session.add(paper)
    session.commit()
    return True


async def _openalex_fallback(session: Session, paper: Paper, refs_limit: int) -> list[dict]:
    """OpenAlex 回退：补 doi/arxiv/标题身份（如缺）并拉引用列表。

    OpenAlex 匿名限额宽松（~10 rps），是 W-id 论文的主通道；S2 共享池拥挤时
    也充当引用列表的备胎。
    """
    if not paper.openalex_id:
        return []
    oa = _get_openalex()
    try:
        if not paper.doi and not paper.arxiv_id or is_junk_title(paper.title):
            meta = await oa.get_paper_metadata(paper.openalex_id)
            if meta:
                changed = False
                ndoi = normalize_doi(meta.get("doi"))
                narxiv = normalize_arxiv_id(meta.get("arxiv_id"))
                if ndoi and not paper.doi:
                    paper.doi = ndoi
                    changed = True
                if narxiv and not paper.arxiv_id:
                    paper.arxiv_id = narxiv
                    changed = True
                if is_junk_title(paper.title) and meta.get("title"):
                    paper.title = meta["title"]
                    paper.title_normalized = normalize_title(meta["title"])
                    changed = True
                if changed:
                    paper.updated_at = datetime.now(timezone.utc)
                    session.add(paper)
                    session.commit()
        return await oa.get_paper_references(paper.openalex_id, limit=refs_limit)
    except Exception as exc:
        logger.debug("openalex fallback failed for paper %s: %s", paper.id, exc)
        return []


async def backfill_workspace_references(
    ws_id: int,
    *,
    refs_limit: int = DEFAULT_REFS_LIMIT,
    force: bool = False,
    progress: dict | None = None,
) -> dict:
    """为工作区所有论文回填引用关系。

    Returns:
        统计 dict：papers_total / papers_fetched / papers_skipped / papers_no_identity /
        refs_saved / refs_linked / titles_fixed / errors。
    """
    prog = progress if progress is not None else {}
    stats = {
        "papers_total": 0,
        "papers_fetched": 0,
        "papers_skipped": 0,
        "papers_no_identity": 0,
        "refs_saved": 0,
        "refs_linked": 0,
        "titles_fixed": 0,
        "errors": 0,
    }

    engine = get_engine()
    with Session(engine) as session:
        papers = _load_ws_papers(session, ws_id)
        stats["papers_total"] = len(papers)
        prog.update({"phase": "identity", "done": 0, "total": len(papers), **stats})

        # 1) 离线身份补齐：legacy external_id → doi/arxiv/s2/canonical/title_normalized
        for p in papers:
            # 先剥标题前缀页眉（不动纯噪声标题，交给 S2 修复）
            cleaned = clean_display_title(p.title)
            if cleaned and p.title and cleaned != p.title.strip():
                p.title = cleaned
                p.title_normalized = normalize_title(cleaned)
                stats["titles_fixed"] += 1
            apply_identity_to_paper(p, paper_to_work(p))
            session.add(p)
        session.commit()

        idx = _build_local_index(papers)

        # 2) 逐篇拉取 S2 references 并落库
        prog.update({"phase": "refs", "done": 0, "total": len(papers), **stats})
        for i, p in enumerate(papers):
            prog.update({"done": i, **stats})
            try:
                if p.id is None:
                    continue
                if not force and list_by_citing(session, p.id):
                    stats["papers_skipped"] += 1
                    continue

                path_id = _s2_path_id(p)
                # 只有既无强 ID 也无 OpenAlex ID 时才用 S2 标题搜索兜底
                # （W-id 论文直接走 OpenAlex 回退，更快更稳）
                if not path_id and not p.openalex_id and not is_junk_title(p.title) and p.title:
                    found = await find_s2_paper_id(title=p.title)
                    if found:
                        p.s2_id = normalize_s2_id(found)
                        apply_identity_to_paper(p, paper_to_work(p))
                        session.add(p)
                        session.commit()
                        path_id = _s2_path_id(p)

                refs: list[dict] = []
                ref_source = REF_SOURCE
                if path_id:
                    # 纯噪声标题：用 S2 规范元数据修复
                    if is_junk_title(p.title):
                        if await _repair_title_from_s2(session, p, path_id):
                            stats["titles_fixed"] += 1
                            idx = _build_local_index(papers)
                    refs = await get_paper_references(path_id, limit=refs_limit)

                if not refs and p.openalex_id:
                    had_junk = is_junk_title(p.title)
                    refs = await _openalex_fallback(session, p, refs_limit)
                    ref_source = REF_SOURCE_OPENALEX
                    if had_junk and not is_junk_title(p.title):
                        stats["titles_fixed"] += 1
                        idx = _build_local_index(papers)

                if not refs:
                    stats["papers_no_identity"] += 1
                    continue

                ref_inputs: list[dict] = []
                for r in refs:
                    cited_id, conf = _resolve_ref_locally(r, idx)
                    if cited_id == p.id:
                        continue  # 自引解析异常保护
                    ref_inputs.append({
                        "cited_doi": r.get("doi"),
                        "cited_arxiv_id": r.get("arxiv_id"),
                        "cited_title": r.get("title"),
                        "cited_paper_id": cited_id,
                        "source": ref_source,
                        "confidence": conf if cited_id else 1.0,
                    })
                saved = upsert_references(session, p.id, ref_inputs)
                stats["refs_saved"] += len(saved)
                stats["refs_linked"] += sum(1 for r in saved if r.cited_paper_id)
                # 兜底：doi/arxiv 强身份匹配全库（覆盖 ws 外论文）
                stats["refs_linked"] += resolve_cited_links(session, citing_paper_id=p.id)
                stats["papers_fetched"] += 1
            except Exception as exc:
                stats["errors"] += 1
                logger.warning("backfill: paper %s failed: %s", p.id, exc)

        prog.update({"phase": "refs", "done": len(papers), **stats})

    logger.info("backfill ws=%s done: %s", ws_id, stats)
    return stats


async def rebuild_workspace_graph(ws_id: int) -> dict:
    """用现行构图代码重建工作区图谱并持久化。返回图 stats。"""
    from ..ai_config import load_config
    from .graph_builder import build_graph
    from .stages.persist import stage_persist

    engine = get_engine()
    with Session(engine) as session:
        papers = _load_ws_papers(session, ws_id)
        run = session.exec(
            select(ResearchRun)
            .where(ResearchRun.workspace_id == ws_id)
            .order_by(ResearchRun.id.desc())  # type: ignore[attr-defined]
        ).first()
        run_id = run.id if run else None

    if not papers:
        return {"error": "workspace has no papers"}

    research_cfg = (load_config().get("research") or {})
    graph = await build_graph(
        ws_id,
        papers,
        similarity_threshold=float(research_cfg.get("similarity_threshold", 0.78)),
        enable_shared_method=False,
        backbone_alpha=float(research_cfg.get("backbone_alpha", 0.25)),
    )
    if run_id is not None:
        await stage_persist(ws_id, run_id, graph)
    else:
        logger.warning("rebuild ws=%s: no research run exists, graph not persisted", ws_id)
    return graph["stats"]
