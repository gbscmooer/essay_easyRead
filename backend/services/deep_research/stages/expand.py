"""
Stage 2 — expand

联网增量：通过可插拔的 research provider 抽取 seed 论文的 references，
按引用数排序下载 Top-K，仅做 PDF 下载 + 元数据登记。

**不再调用 LLM 进行浅分析**——下载的论文标记为：
- source = "expanded"
- level  = seed_level + 1
- status = "indexed"  # 表示 PDF 已落盘，等待 chunk + embed

LLM 解读（论文分析页面）走单独的入口，按需触发，不在此处消耗 token。

Provider 由 ai_config["research"]["provider"] 控制：
- openalex（默认）
- semantic_scholar
- local（关闭联网扩展）
"""

import asyncio
import logging
from sqlmodel import Session, create_engine, select

from ....config import settings
from ....models.paper import Paper
from ....models.workspace import WorkspacePaper
from ..providers import get_provider
from ..pdf_downloader import download_pdf
from ...ai_config import get_research_config

logger = logging.getLogger(__name__)

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})
    return _engine


SEM = asyncio.Semaphore(3)  # 并发下载上限


async def _download_and_register(ref: dict, parent: Paper, ws_id: int, next_level: int) -> Paper | None:
    if not ref.get("pdf_url"):
        return None
    async with SEM:
        local = await download_pdf(ref["pdf_url"])
    if not local:
        return None

    with Session(_get_engine(), expire_on_commit=False) as s:
        # 去重：external_id / DOI 已存在则跳过
        ext = ref.get("ext_id") or ref.get("doi") or ref.get("arxiv_id")
        if ext:
            existing = s.exec(select(Paper).where(Paper.external_id == ext)).first()
            if existing:
                # 已存在：只确保挂在该工作区即可
                wp = s.exec(select(WorkspacePaper)
                            .where(WorkspacePaper.workspace_id == ws_id)
                            .where(WorkspacePaper.paper_id == existing.id)).first()
                if not wp:
                    s.add(WorkspacePaper(workspace_id=ws_id, paper_id=existing.id))
                    s.commit()
                return None

        p = Paper(
            filename=f"{(ref.get('title') or 'untitled')[:60]}.pdf",
            pdf_path=local,
            file_size=0,
            title=ref.get("title"),
            year=ref.get("year"),
            status="indexed",            # ← 不走 LLM 分析，标记为 indexed
            source="expanded",
            level=next_level,
            parent_paper_id=parent.id,
            external_id=ext,
        )
        s.add(p)
        s.commit()
        s.add(WorkspacePaper(workspace_id=ws_id, paper_id=p.id))
        s.commit()
        return p


async def stage_expand(
    ws_id: int,
    seeds: list,
    params: dict,
    on_paper_added,
    *,
    next_level: int = 2,
) -> list:
    """对每篇 seed 找 references，下载 Top-K 开放获取 PDF。

    返回值：新增的 expanded 论文列表（不含 seeds）。
    LLM 不参与此阶段，下载完即返回，后续走 chunk/embed/graph。
    """
    top_k = int(params.get("expand_top_k", 5))
    total_limit = int(params.get("expand_total_limit", 50))

    provider = get_provider(get_research_config())
    logger.info(f"expand stage (level→{next_level}) using provider={provider.name}")

    if provider.name == "local":
        return []

    expanded: list[Paper] = []
    expanded_count = 0

    for seed in seeds:
        if expanded_count >= total_limit:
            break
        ext_paper_id = await provider.find_paper_id(
            title=seed.title or seed.filename,
            doi="",
            arxiv_id="",
        )
        if not ext_paper_id:
            continue
        refs = await provider.get_paper_references(ext_paper_id, limit=top_k * 3)
        refs = [r for r in refs if r.get("pdf_url")]
        refs.sort(key=lambda r: r.get("citation_count") or 0, reverse=True)

        added_for_seed = 0
        for ref in refs:
            if added_for_seed >= top_k or expanded_count >= total_limit:
                break
            p = await _download_and_register(ref, seed, ws_id, next_level)
            if p:
                expanded.append(p)
                expanded_count += 1
                added_for_seed += 1
                await on_paper_added(
                    "paper_added",
                    paper_id=p.id, title=p.title, level=p.level,
                    source_paper_id=seed.id, total_expanded=expanded_count,
                )

    return expanded
