"""
Papers 路由

处理论文的上传、列表、查询、删除等操作。
"""

import os
import re
import shutil
import uuid
import asyncio
import logging
from fastapi import APIRouter, UploadFile, File, Query, HTTPException
from fastapi.responses import FileResponse, Response
from sqlmodel import Session

logger = logging.getLogger(__name__)

from ..models.schemas import PaperResponse, PaperListResponse, ErrorResponse
from ..config import settings
from ..services.database import (
    create_paper,
    get_paper,
    get_engine,
    list_papers,
    delete_paper,
    update_paper_status,
)
from ..services.pdf_parser import extract_pdf_content
from ..utils.streaming import format_analysis_as_markdown

router = APIRouter(prefix="/api/papers", tags=["papers"])


# ============================================================
# POST /api/papers/upload
# ============================================================
@router.post("/upload", response_model=PaperResponse)
async def upload_paper(file: UploadFile = File(...)) -> PaperResponse:
    """上传 PDF 文件并触发解析流程"""
    # 验证文件格式
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail={"detail": "Only PDF files are accepted", "code": "INVALID_FORMAT"},
        )

    # 读取文件内容
    content = await file.read()

    # 验证文件大小
    if len(content) > settings.max_file_size:
        raise HTTPException(
            status_code=400,
            detail={"detail": "File too large, max 50MB", "code": "FILE_TOO_LARGE"},
        )

    # 确保上传目录存在
    upload_dir = settings.resolved_upload_dir
    os.makedirs(upload_dir, exist_ok=True)

    # 生成唯一文件名并保存
    file_id = str(uuid.uuid4())
    filename = f"{file_id}.pdf"
    pdf_path = os.path.join(upload_dir, filename)

    def _write_pdf():
        with open(pdf_path, "wb") as f:
            f.write(content)

    await asyncio.to_thread(_write_pdf)

    # 创建数据库记录
    engine = get_engine()
    with Session(engine) as session:
        paper_id = create_paper(session, file.filename, pdf_path, len(content))
        paper = get_paper(session, paper_id)

    # 自动索引：后台提取全文 + 基本元数据（不跑 LLM，不阻塞上传响应）
    from ..services.auto_indexer import auto_index
    asyncio.create_task(auto_index(paper_id, pdf_path))

    # 不自动启动分析，用户在阅读页面手动点击"开始分析"

    return PaperResponse(
        id=paper["id"],
        filename=paper["filename"],
        title=paper["title"],
        authors=paper.get("authors"),
        venue=paper["venue"],
        year=paper["year"],
        category=paper.get("category"),
        status=paper["status"],
        index_status=paper.get("index_status", "none"),
        error_message=paper["error_message"],
        code_url=paper.get("code_url"),
        code_status=paper.get("code_status", "none"),
        created_at=paper["created_at"],
        updated_at=paper["updated_at"],
    )


# ============================================================
# GET /api/papers
# ============================================================
@router.get("", response_model=PaperListResponse)
async def get_papers_list(
    search: str = Query(None, description="Search by title"),
    category: str = Query(None, description="按 arXiv 学科方向筛选，如 cs.LG"),
    year: int = Query(None, description="Filter by year"),
    date_added: str = Query(None, description="按加入论文库日期筛选，YYYY-MM-DD"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    source: str = Query("uploaded", description="uploaded | expanded | all"),
) -> PaperListResponse:
    """获取论文列表，支持筛选和搜索"""
    engine = get_engine()
    with Session(engine) as session:
        papers, total = list_papers(
            session, search=search, category=category, year=year,
            date_added=date_added, page=page, per_page=per_page, source=source,
        )

    paper_responses = [
        PaperResponse(
            id=p["id"],
            filename=p["filename"],
            title=p["title"],
            authors=p.get("authors"),
            venue=p["venue"],
            year=p["year"],
            category=p.get("category"),
            status=p["status"],
            index_status=p.get("index_status", "none"),
            error_message=p["error_message"],
            code_url=p.get("code_url"),
            code_status=p.get("code_status", "none"),
            source=p.get("source", "uploaded"),
            level=p.get("level", 1),
            created_at=p["created_at"],
            updated_at=p["updated_at"],
        )
        for p in papers
    ]

    return PaperListResponse(papers=paper_responses, total=total)


# ============================================================
# GET /api/papers/smart-search
# 智能搜索：标题/作者/刊物/年份 + 解读内容（+ 可选全局正文全文），统一返回命中标签
# 注意：必须定义在 /{paper_id} 之前，否则 "smart-search" 会被当成 paper_id → 422
# ============================================================
def _match_content_fields(ar, ql: str) -> list[dict]:
    """在解读结果（AnalysisResult）里做大小写不敏感命中，返回 matched_fields。"""
    import json as _json

    out: list[dict] = []
    if ar.one_sentence and ql in ar.one_sentence.lower():
        out.append({"field": "one_sentence", "snippet": ar.one_sentence})
    if ar.problem and ql in ar.problem.lower():
        out.append({"field": "problem", "snippet": ar.problem[:200]})
    if ar.limitations and ql in ar.limitations.lower():
        out.append({"field": "limitations", "snippet": ar.limitations[:200]})

    for field, obj in (("method", ar.method), ("experiments", ar.experiments)):
        s = _json.dumps(obj or {}, ensure_ascii=False)
        sl = s.lower()
        if ql in sl:
            idx = sl.find(ql)
            start = max(0, idx - 80)
            end = min(len(s), idx + len(ql) + 80)
            out.append({"field": field, "snippet": s[start:end]})
    return out


@router.get("/smart-search")
async def smart_search(
    q: str = Query(..., min_length=1, description="搜索关键词（大小写不敏感）"),
    category: str = Query(None, description="按 arXiv 学科方向筛选，如 cs.LG"),
    year: int = Query(None, description="按年份筛选"),
    date_added: str = Query(None, description="按加入论文库日期筛选，YYYY-MM-DD"),
    global_: bool = Query(False, alias="global", description="开启则额外匹配论文正文全文"),
    include_expanded: bool = Query(False, description="开启则一并搜索深度研究联网下载的相关论文"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """论文库统一智能搜索：元数据 + 解读内容 + 可选全文，命中并集 + 命中标签。"""
    from ..models.paper import Paper as PaperModel
    from ..models.analysis import AnalysisResult as AR

    ql = q.lower()
    engine = _get_engine()
    results: list[dict] = []

    with Session(engine) as session:
        stmt = select(PaperModel)
        # 默认只搜「加入论文库」（uploaded）；开启后并入联网扩展下载的相关论文（expanded）
        if not include_expanded:
            stmt = stmt.where(PaperModel.source == "uploaded")
        if category:
            stmt = stmt.where(PaperModel.category == category)
        if year is not None:
            stmt = stmt.where(PaperModel.year == year)
        if date_added:
            from sqlalchemy import func as _func
            stmt = stmt.where(_func.date(PaperModel.created_at) == date_added)
        papers = session.exec(stmt).all()

        for p in papers:
            matched: list[dict] = []
            # 元数据
            if p.title and ql in p.title.lower():
                matched.append({"field": "title", "snippet": p.title})
            # 文件名兜底：当 title 为空或 title 未命中时，额外检查原始文件名
            if p.filename and ql in p.filename.lower():
                # 避免 title 和 filename 重复命中（如文件名就是标题）
                if not (p.title and ql in p.title.lower()):
                    matched.append({"field": "filename", "snippet": p.filename})
            if p.authors and ql in p.authors.lower():
                matched.append({"field": "authors", "snippet": p.authors})
            if p.venue and ql in p.venue.lower():
                matched.append({"field": "venue", "snippet": p.venue})
            if p.year and ql in str(p.year):
                matched.append({"field": "year", "snippet": str(p.year)})

            # 解读内容（加异常保护，避免单篇论文 JSON 异常导致整个搜索崩溃）
            try:
                ar = session.exec(select(AR).where(AR.paper_id == p.id)).first()
                if ar:
                    matched.extend(_match_content_fields(ar, ql))
            except Exception as ex:
                logger.warning(f"smart_search: paper {p.id} 解读内容匹配异常: {ex}")

            # 全局：正文全文字面匹配
            if global_ and p.full_text and ql in p.full_text.lower():
                fl = p.full_text.lower()
                idx = fl.find(ql)
                start = max(0, idx - 80)
                end = min(len(p.full_text), idx + len(ql) + 80)
                # 计算匹配位置所在行号
                line_num = p.full_text[:idx].count('\n') + 1
                matched.append({
                    "field": "fulltext",
                    "snippet": "…" + p.full_text[start:end] + "…",
                    "line": line_num,
                })

            if matched:
                results.append({
                    "paper_id": p.id,
                    "title": p.title or p.filename,
                    "authors": p.authors,
                    "venue": p.venue,
                    "year": p.year,
                    "source": p.source,
                    "matched_fields": matched,
                })

    # 命中字段越多越靠前
    results.sort(key=lambda r: len(r["matched_fields"]), reverse=True)
    total = len(results)
    start_idx = (page - 1) * per_page
    return {"results": results[start_idx:start_idx + per_page], "total": total, "query": q}


# ============================================================
# POST /api/papers/reindex
# 批量重新索引：用 GROBID 强制重提元数据，覆盖未解读论文的脏数据。
# 注意：必须定义在 /{paper_id} 之前，否则 "reindex" 会被当成 paper_id → 422
# ============================================================
@router.post("/reindex")
async def reindex_library_endpoint(
    include_expanded: bool = Query(False, description="是否一并重索引联网扩展下载的论文"),
):
    """重新索引：对范围内每篇论文逐篇清空旧索引并用 GROBID 重做（不跳过已索引的）。

    默认处理「加入论文库」的所有论文；include_expanded=True 时一并处理深度研究发现的论文。
    后台异步执行，立即返回处理数量；前端可轮询论文列表的 index_status 观察进度。
    """
    from ..services.auto_indexer import count_reindex_targets, reindex_library

    queued = await asyncio.to_thread(count_reindex_targets, include_expanded)
    if queued:
        asyncio.create_task(reindex_library(include_expanded))
    return {"queued": queued}


# ============================================================
# GET /api/papers/{paper_id}
# ============================================================
@router.get("/{paper_id}", response_model=PaperResponse)
async def get_paper_detail(paper_id: int) -> PaperResponse:
    """获取单篇论文详情"""
    engine = get_engine()
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"detail": f"Paper {paper_id} not found", "code": "NOT_FOUND"},
        )

    return PaperResponse(
        id=paper["id"],
        filename=paper["filename"],
        title=paper["title"],
        authors=paper.get("authors"),
        venue=paper["venue"],
        year=paper["year"],
        category=paper.get("category"),
        status=paper["status"],
        index_status=paper.get("index_status", "none"),
        error_message=paper["error_message"],
        code_url=paper.get("code_url"),
        code_status=paper.get("code_status", "none"),
        created_at=paper["created_at"],
        updated_at=paper["updated_at"],
    )


# ============================================================
# DELETE /api/papers/{paper_id}
# ============================================================
@router.delete("/{paper_id}")
async def delete_paper_endpoint(paper_id: int) -> dict:
    """删除论文及其解析结果"""
    engine = get_engine()
    try:
        with Session(engine) as session:
            pdf_path, code_path = delete_paper(session, paper_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"detail": f"Paper {paper_id} not found", "code": "NOT_FOUND"},
        )

    # 删除 PDF 文件
    if os.path.exists(pdf_path):
        os.remove(pdf_path)

    # 删除代码仓库目录
    if code_path and os.path.isdir(code_path):
        shutil.rmtree(code_path, ignore_errors=True)

    return {"message": "Paper deleted successfully"}


# ============================================================
# POST /api/papers/{paper_id}/promote
# ============================================================
@router.post("/{paper_id}/promote")
async def promote_paper(paper_id: int) -> dict:
    """将深度研究扩展论文提升为普通用户论文（移入论文库）"""
    from datetime import datetime, timezone
    from sqlmodel import Session as S
    from ..models.paper import Paper as PaperModel

    engine = get_engine()
    with S(engine) as session:
        p = session.get(PaperModel, paper_id)
        if p is None:
            raise HTTPException(
                status_code=404,
                detail={"detail": f"Paper {paper_id} not found", "code": "NOT_FOUND"},
            )
        p.source = "uploaded"
        p.level = 1
        p.parent_paper_id = None
        p.updated_at = datetime.now(timezone.utc)
        session.add(p)
        session.commit()
    return {"message": "Paper promoted to library"}


# ============================================================
# GET /api/papers/{paper_id}/export
# ============================================================
@router.get("/{paper_id}/export")
async def export_paper(paper_id: int) -> Response:
    """导出论文解析结果为 Markdown"""
    from ..services.database import get_analysis

    engine = get_engine()
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"detail": f"Paper {paper_id} not found", "code": "NOT_FOUND"},
        )

    if paper["status"] != "done":
        raise HTTPException(
            status_code=400,
            detail={"detail": "Paper analysis is not completed yet", "code": "NOT_READY"},
        )

    try:
        with Session(engine) as session:
            analysis = get_analysis(session, paper_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"detail": f"Analysis for paper {paper_id} not found", "code": "NOT_FOUND"},
        )

    markdown = format_analysis_as_markdown(analysis)
    title = analysis.get("title", "paper")
    safe_title = re.sub(r'[^\w\s\-.]', '', title).strip() or 'paper'

    return Response(
        content=markdown,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.md"'},
    )


# ============================================================
# GET /api/papers/{paper_id}/pdf
# ============================================================
@router.get("/{paper_id}/pdf")
async def get_paper_pdf(paper_id: int) -> FileResponse:
    """获取论文 PDF 文件"""
    engine = get_engine()
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"detail": f"Paper {paper_id} not found", "code": "NOT_FOUND"},
        )

    # 通过数据库记录获取实际文件路径
    with Session(engine) as session:
        from sqlmodel import select
        from ..models.paper import Paper as PaperModel
        db_paper = session.get(PaperModel, paper_id)
        if db_paper is None:
            raise HTTPException(
                status_code=404,
                detail={"detail": f"Paper {paper_id} not found", "code": "NOT_FOUND"},
            )
        pdf_path = db_paper.pdf_path

    if not os.path.exists(pdf_path):
        raise HTTPException(
            status_code=404,
            detail={"detail": "PDF file not found on disk", "code": "FILE_NOT_FOUND"},
        )

    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline"},
    )


# ============================================================
# GET /api/papers
# ============================================================
# 获取论文列表，支持筛选和搜索。
#
# 查询参数：
#   - search: str = None      # 按标题/关键词搜索
#   - venue: str = None       # 按会议筛选
#   - year: int = None        # 按年份筛选
#   - page: int = 1           # 页码
#   - per_page: int = 20      # 每页数量
#
# 返回 200: PaperListResponse
#   - papers: list[PaperResponse]  # 当前页的论文列表
#   - total: int                   # 总数


# ============================================================
# GET /api/papers/{paper_id}
# ============================================================
# 获取单篇论文详情。
#
# 路径参数：
#   - paper_id: int
#
# 返回 200: PaperResponse
#
# 返回 404: ErrorResponse
#   - code="NOT_FOUND": 论文不存在


# ============================================================
# DELETE /api/papers/{paper_id}
# ============================================================
# 删除一篇论文及其解析结果。
#
# 路径参数：
#   - paper_id: int
#
# 返回 200: {"message": "Paper deleted successfully"}
#
# 业务流程：
#   1. 删除 AnalysisResult 记录（如果有）
#   2. 删除 Paper 记录
#   3. 删除上传的 PDF 文件
#
# 返回 404: ErrorResponse
#   - code="NOT_FOUND": 论文不存在


# ============================================================
# GET /api/papers/{paper_id}/export
# ============================================================
# 导出论文解析结果为 Markdown。
#
# 路径参数：
#   - paper_id: int
#
# 返回 200: text/markdown
#   - Content-Disposition: attachment; filename="{title}.md"
#   - 返回格式化的 Markdown 文本
#
# 返回 404: ErrorResponse
#   - code="NOT_FOUND": 论文不存在
#
# 返回 400: ErrorResponse
#   - code="NOT_READY": 论文尚未解析完成


# ============================================================
# GET /api/papers/{paper_id}/pdf
# ============================================================
# 获取论文 PDF 文件（用于前端 PdfViewer 预览）。
#
# 路径参数：
#   - paper_id: int
#
# 返回 200: application/pdf
#   - 返回 PDF 文件流
#   - Content-Disposition: inline（浏览器内预览，不下载）
#
# 返回 404: ErrorResponse
#   - code="NOT_FOUND": 论文不存在
#
# 实现要点：
#   1. 根据 paper_id 查询 Paper 记录获取 pdf_path
#   2. 使用 FileResponse 返回文件
#   3. media_type="application/pdf"
#   4. 前端通过此 URL 传给 react-pdf 的 Document 组件
