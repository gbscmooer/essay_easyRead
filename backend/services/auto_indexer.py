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
from .database import get_engine
from .ai_config import get_arxiv_expand_config
from .pdf_parser import _extract_with_pymupdf, _extract_with_pypdf
from .deep_research.grobid_parser import is_grobid_alive, parse_header

logger = logging.getLogger(__name__)

FULL_TEXT_LIMIT = 300_000  # 单篇全文落库上限，兜底超长 PDF
_YEAR_RE = re.compile(r"(19|20)\d{2}")
# arXiv 首页戳：arXiv:2201.00072v2 [cs.LG] 10 Apr 2022
_ARXIV_ID_RE = re.compile(r"arxiv[:\s]*(\d{4}\.\d{4,5})", re.IGNORECASE)
# 学科分类如 cs.LG / stat.ML / eess.IV / cond-mat.stat-mech（取方括号内首个）
_CATEGORY_RE = re.compile(r"\[([a-z][a-z\-]*\.[A-Za-z\-]{2,})\]")
# arXiv 戳上印的提交/版本日期里的年份：arXiv:<id>[vN] [cat] DD Mon YYYY → 取那个年份。
# 锚定在 arXiv ID 之后取年份，避免误抓正文里出现的年份（如 “In 1904, ...”）。
_ARXIV_STAMP_YEAR_RE = re.compile(
    r"arxiv[:\s]*\d{4}\.\d{4,5}(?:v\d+)?[\s\S]{0,40}?\b((?:19|20)\d{2})\b",
    re.IGNORECASE,
)


def _arxiv_stamp_year(first_page: str):
    """从首页 arXiv 戳的日期里取年份（用户在 PDF 上看到的那个年份）。无则 None。"""
    m = _ARXIV_STAMP_YEAR_RE.search(first_page or "")
    return int(m.group(1)) if m else None

# 写回时只填空/可覆盖的元数据字段（不含 full_text）
_META_FIELDS = ("title", "authors", "venue", "year", "category")
_MIN_VALID_YEAR = 1900


def _valid_year(y) -> bool:
    """4 位合理年份才算有效。过滤 LLM/启发式抽出的脏年份（如 20、2）。"""
    from datetime import datetime, timezone
    return isinstance(y, int) and _MIN_VALID_YEAR <= y <= datetime.now(timezone.utc).year + 1

def _get_engine():
    return get_engine()


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

    # 年份：优先 arXiv 戳上的日期年份；没有戳再退而取首页第一个 19xx/20xx
    year = _arxiv_stamp_year(first_page)
    if year is None:
        m = _YEAR_RE.search(first_page)
        if m:
            year = int(m.group(0))

    # arXiv ID 与学科分类：直接从首页的 arXiv 戳抓取
    aid_m = _ARXIV_ID_RE.search(first_page)
    arxiv_id = aid_m.group(1) if aid_m else None
    cat_m = _CATEGORY_RE.search(first_page)
    category = cat_m.group(1) if cat_m else None

    return {
        "title": title or None, "authors": authors, "year": year,
        "venue": None, "category": category, "arxiv_id": arxiv_id,
    }


async def _extract_meta(pdf_path: str, metadata: dict, first_page: str) -> dict:
    """组合回退：GROBID header 优先，逐字段用启发式补缺；学科分类经 arXiv API 兜底。

    GROBID 给结构化的 title/authors/year/venue（最准），但不给 arXiv 学科分类；
    分类优先从首页 arXiv 戳正则抓 [cs.LG]，仍缺且有 arXiv ID 时调 arXiv API 取主分类。
    """
    ae = get_arxiv_expand_config()
    grobid_url = ae.get("grobid_url", "http://localhost:8070")
    meta = {"title": None, "authors": None, "year": None,
            "venue": None, "category": None, "arxiv_id": None}

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

    # 年份以 arXiv 戳上印的日期为准（用户在 PDF 上看到的那个），优先于 GROBID/正文猜测
    stamp_year = _arxiv_stamp_year(first_page)
    if stamp_year:
        meta["year"] = stamp_year

    # 学科分类兜底：有 arXiv ID 但分类仍缺时，查 arXiv API 拿权威主分类（同时补全年份）
    if not meta.get("category") and meta.get("arxiv_id"):
        try:
            from .deep_research.arxiv_client import get_metadata
            am = await get_metadata(meta["arxiv_id"])
            if am:
                if am.get("category"):
                    meta["category"] = am["category"]
                if not meta.get("year") and am.get("year"):
                    meta["year"] = am["year"]
        except Exception as ex:
            logger.debug(f"auto_index: arXiv API 取分类失败 ({meta.get('arxiv_id')}): {ex}")

    return meta


async def auto_index(
    paper_id: int, pdf_path: str, *, force_meta: bool = False, overwrite: bool = False
) -> None:
    """对单篇论文做自动索引（全文 + 元数据）。异常吞掉，置 failed。

    force_meta：即使已有标题也强制重新提取元数据（重新索引时用）。
    overwrite：未跑 LLM 解读的论文（status != 'done'）允许用 GROBID 结果覆盖旧的脏数据；
               已解读论文始终只填空补缺，不动 LLM 写入的字段。
    """
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
        # 标题已有且非强制时跳过元数据提取，避免无谓 GROBID 调用
        need_meta = force_meta or not had_title
        meta = await _extract_meta(pdf_path, metadata, first_page) if need_meta else {}

        with Session(engine) as s:
            p = s.get(Paper, paper_id)
            if p is None:
                return
            if full:
                p.full_text = full
            # GROBID 是论文库元数据的唯一来源，overwrite=True 时直接覆盖旧值；
            # 脏年份（如 20、2）视同缺失——即使非覆盖模式也允许用 GROBID 的合理年份修复
            can_overwrite = overwrite
            for field in _META_FIELDS:
                val = meta.get(field)
                if not val:
                    continue
                if field == "year" and not _valid_year(val):
                    continue  # 不写入脏年份
                cur = getattr(p, field)
                cur_bad_year = field == "year" and cur is not None and not _valid_year(cur)
                if can_overwrite or not cur or cur_bad_year:
                    setattr(p, field, val)
            p.index_status = "indexed"
            s.add(p)
            s.commit()
        logger.info(f"auto_index: paper {paper_id} indexed (title={meta.get('title')!r}, category={meta.get('category')!r})")
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


def _pick_reindex_targets(include_expanded: bool = False) -> list[tuple[int, str]]:
    """选出重新索引的目标论文（PDF 在本地），返回 (id, path)。

    重新索引会逐篇清空旧索引并用 GROBID 重做，不跳过已索引的论文：
      - 默认：论文库里的所有论文（uploaded）。
      - include_expanded=True：再加上深度研究发现的论文（expanded），即全部论文。
    """
    import os

    engine = _get_engine()
    with Session(engine) as s:
        stmt = select(Paper.id, Paper.pdf_path)
        if not include_expanded:
            stmt = stmt.where(Paper.source == "uploaded")
        rows = s.exec(stmt).all()
    return [(pid, path) for pid, path in rows if path and os.path.exists(path)]


def count_reindex_targets(include_expanded: bool = False) -> int:
    """统计本次重新索引会处理的论文数（供前端展示进度）。"""
    return len(_pick_reindex_targets(include_expanded))


async def reindex_library(include_expanded: bool = False, concurrency: int = 2) -> int:
    """手动重新索引：对范围内每篇论文逐篇清空旧索引并用 GROBID 重做（不跳过已索引的）。

    默认处理论文库里的所有论文（uploaded）；include_expanded=True 时一并处理深度研究
    发现的论文（expanded）。GROBID 作为唯一索引来源，直接覆盖旧元数据（仅在 GROBID
    给出非空值时；脏年份永不写入）。返回本次处理的论文数。
    """
    rows = _pick_reindex_targets(include_expanded)
    if not rows:
        return 0
    logger.info(
        f"reindex_library: 重新索引 {len(rows)} 篇论文"
        f"（含联网扩展={include_expanded}）"
    )
    sem = asyncio.Semaphore(concurrency)

    async def _one(pid: int, path: str):
        async with sem:
            await auto_index(pid, path, force_meta=True, overwrite=True)

    await asyncio.gather(*[_one(pid, path) for pid, path in rows], return_exceptions=True)
    return len(rows)
