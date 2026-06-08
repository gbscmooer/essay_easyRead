"""
自动索引：论文上传即提取「全文 + 基本元数据」，不跑 LLM。

- full_text：PyMuPDF 提取的全文（截断），供论文库「全局搜索」做字面匹配。
- 元数据（title/authors/year/venue）：组合回退——
    GROBID processHeaderDocument 优先（最准，需容器在跑）→ 失败回退 PDF 元信息 + 首页正则启发式。
  只「填空补缺」：仅当 Paper 对应字段为空时写入，绝不覆盖完整 LLM 解读得到的结果。

触发点：papers.upload_paper 创建记录后 asyncio.create_task(auto_index(...))；
以及 main.py 启动时对存量 index_status='none' 的论文后台补跑。
"""
import asyncio
import logging
import re

from sqlmodel import Session, create_engine, select

from ..config import settings
from ..models.paper import Paper
from .ai_config import get_arxiv_expand_config
from .pdf_parser import _extract_with_pymupdf, _extract_with_pypdf
from .deep_research.grobid_parser import is_grobid_alive, parse_header

logger = logging.getLogger(__name__)

FULL_TEXT_LIMIT = 300_000  # 单篇全文落库上限，兜底超长 PDF
_YEAR_RE = re.compile(r"(19|20)\d{2}")

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})
    return _engine


def _extract(pdf_path: str) -> tuple[str, dict, str]:
    """返回 (全文截断, pdf元信息, 首页文本)。双路径：PyMuPDF 主、pypdf 备。"""
    res = _extract_with_pymupdf(pdf_path) or _extract_with_pypdf(pdf_path)
    if not res:
        return "", {}, ""
    page_texts, metadata = res
    full = "".join(page_texts)
    first_page = page_texts[0] if page_texts else ""
    return full[:FULL_TEXT_LIMIT], (metadata or {}), first_page


def _heuristic_meta(metadata: dict, first_page: str) -> dict:
    """无 GROBID 时的兜底：PDF 元信息 + 首页正则启发式。"""
    # 标题：PDF 元信息 title 优先（够长才可信），否则取首页首个较长的非空行
    title = (metadata.get("title") or "").strip()
    if len(title) < 6:
        title = ""
        for line in first_page.splitlines():
            s = line.strip()
            if len(s) >= 12 and not s.lower().startswith(("arxiv", "doi", "http")):
                title = s
                break

    authors = (metadata.get("author") or "").strip() or None

    # 年份：首页里第一个 19xx/20xx
    year = None
    m = _YEAR_RE.search(first_page)
    if m:
        year = int(m.group(0))

    return {"title": title or None, "authors": authors, "year": year, "venue": None}


async def _extract_meta(pdf_path: str, metadata: dict, first_page: str) -> dict:
    """组合回退：GROBID header 优先，逐字段用启发式补缺。"""
    ae = get_arxiv_expand_config()
    grobid_url = ae.get("grobid_url", "http://localhost:8070")
    meta = {"title": None, "authors": None, "year": None, "venue": None}

    if await is_grobid_alive(grobid_url):
        try:
            header = await parse_header(pdf_path, grobid_url)
            if header:
                meta.update({k: v for k, v in header.items() if v})
        except Exception as ex:
            logger.warning(f"auto_index: GROBID header 失败，回退启发式: {ex}")

    fallback = _heuristic_meta(metadata, first_page)
    for k, v in fallback.items():
        if not meta.get(k):
            meta[k] = v
    return meta


async def auto_index(paper_id: int, pdf_path: str) -> None:
    """对单篇论文做自动索引（全文 + 元数据填空）。异常吞掉，置 failed。"""
    engine = _get_engine()
    with Session(engine) as s:
        p = s.get(Paper, paper_id)
        if p is None:
            return
        had_title = bool(p.title)  # 已有标题（如联网扩展论文）则跳过元数据提取，只取全文
        p.index_status = "indexing"
        s.add(p)
        s.commit()

    try:
        full, metadata, first_page = await asyncio.to_thread(_extract, pdf_path)
        # 仅当标题缺失时才提取元数据，避免对已有元数据的论文做无谓 GROBID 调用
        meta = {} if had_title else await _extract_meta(pdf_path, metadata, first_page)

        with Session(engine) as s:
            p = s.get(Paper, paper_id)
            if p is None:
                return
            if full:
                p.full_text = full
            # 只填空补缺，不覆盖解读已写入的字段
            if not p.title and meta.get("title"):
                p.title = meta["title"]
            if not p.authors and meta.get("authors"):
                p.authors = meta["authors"]
            if not p.venue and meta.get("venue"):
                p.venue = meta["venue"]
            if not p.year and meta.get("year"):
                p.year = meta["year"]
            p.index_status = "indexed"
            s.add(p)
            s.commit()
        logger.info(f"auto_index: paper {paper_id} indexed (title={meta.get('title')!r})")
    except Exception as ex:
        logger.warning(f"auto_index: paper {paper_id} 失败: {ex}")
        with Session(engine) as s:
            p = s.get(Paper, paper_id)
            if p is not None:
                p.index_status = "failed"
                s.add(p)
                s.commit()


async def reindex_missing(concurrency: int = 2) -> None:
    """对 index_status='none' 且 PDF 在本地的论文后台补跑索引。

    覆盖「加入论文库」(uploaded) 与「联网扩展下载」(expanded) 两类——后者的正文
    也需要 full_text 才能被全局搜索命中。启动时 + 每次研究运行结束后调用，幂等。
    """
    import os

    engine = _get_engine()
    with Session(engine) as s:
        rows = s.exec(
            select(Paper.id, Paper.pdf_path).where(Paper.index_status == "none")
        ).all()
    rows = [(pid, path) for pid, path in rows if path and os.path.exists(path)]
    if not rows:
        return
    logger.info(f"auto_index: 补跑 {len(rows)} 篇未索引论文（含联网扩展）")
    sem = asyncio.Semaphore(concurrency)

    async def _one(pid: int, path: str):
        async with sem:
            await auto_index(pid, path)

    await asyncio.gather(*[_one(pid, path) for pid, path in rows], return_exceptions=True)
