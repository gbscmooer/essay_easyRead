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


async def _download_and_register(ref: dict, parent: Paper, ws_id: int, next_level: int) -> tuple[Paper | None, bool]:
    """返回 (paper, is_new)。is_new=False 表示之前就有，但仍需进入图谱。"""
    ext = ref.get("ext_id") or ref.get("doi") or ref.get("arxiv_id")

    # 先查去重：已经存在过的不重复下载（也不重复扣下载配额）
    if ext:
        with Session(_get_engine(), expire_on_commit=False) as s:
            existing = s.exec(select(Paper).where(Paper.external_id == ext)).first()
            if existing:
                wp = s.exec(select(WorkspacePaper)
                            .where(WorkspacePaper.workspace_id == ws_id)
                            .where(WorkspacePaper.paper_id == existing.id)).first()
                if not wp:
                    s.add(WorkspacePaper(workspace_id=ws_id, paper_id=existing.id))
                    s.commit()
                return existing, False

    if not ref.get("pdf_url"):
        return None, False
    async with SEM:
        local = await download_pdf(ref["pdf_url"])
    if not local:
        return None, False

    with Session(_get_engine(), expire_on_commit=False) as s:
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
        return p, True


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
        # 拉更多候选：OpenAlex 只对开放获取论文给出 pdf_url，付费墙占多数，
        # top_k*3 通常被过滤到不足，把候选放大到 top_k*8 + 兜底 30
        candidate_limit = max(top_k * 8, 30)
        refs = await provider.get_paper_references(ext_paper_id, limit=candidate_limit)
        total_refs = len(refs)
        refs = [r for r in refs if r.get("pdf_url")]
        refs.sort(key=lambda r: r.get("citation_count") or 0, reverse=True)
        logger.info(
            f"expand[seed={seed.id}] {total_refs} refs from provider, {len(refs)} with pdf_url"
        )

        added_for_seed = 0
        for ref in refs:
            if added_for_seed >= top_k or expanded_count >= total_limit:
                break
            p, is_new = await _download_and_register(ref, seed, ws_id, next_level)
            if not p:
                continue
            # 不管新建还是已存在，都加入图谱节点池
            expanded.append(p)
            if is_new:
                expanded_count += 1
            added_for_seed += 1
            await on_paper_added(
                "paper_added",
                paper_id=p.id, title=p.title, level=p.level,
                source_paper_id=seed.id, total_expanded=expanded_count,
                is_new=is_new,
            )

    return expanded
