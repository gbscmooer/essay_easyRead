"""
Papers 路由

处理论文的上传、列表、查询、删除等操作。
"""

import os
import uuid
import asyncio
from fastapi import APIRouter, UploadFile, File, Query, HTTPException
from fastapi.responses import FileResponse, Response
from sqlmodel import Session

from ..models.schemas import PaperResponse, PaperListResponse, ErrorResponse
from ..config import settings
from ..services.database import (
    create_paper,
    get_paper,
    list_papers,
    delete_paper,
    update_paper_status,
)
from ..services.pdf_parser import extract_pdf_content
from ..utils.streaming import format_analysis_as_markdown

router = APIRouter(prefix="/api/papers", tags=["papers"])


def _get_session():
    """创建数据库会话"""
    from sqlmodel import create_engine
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    with Session(engine) as session:
        yield session


async def _run_analysis(paper_id: int, pdf_path: str):
    """后台运行分析任务"""
    from sqlmodel import create_engine
    from ..services.ai_analyzer import analyze_paper_stream
    from ..services.database import update_paper_status, update_paper_metadata, save_analysis_result

    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})

    try:
        # 更新状态为 extracting
        with Session(engine) as session:
            update_paper_status(session, paper_id, "extracting")

        # 提取 PDF 文本
        content = extract_pdf_content(pdf_path)

        # 更新状态为 analyzing
        with Session(engine) as session:
            update_paper_status(session, paper_id, "analyzing")

        # 调用 AI 分析
        result = {}
        async for event in analyze_paper_stream(
            content.full_text,
            settings.mimo_api_key,
            settings.mimo_base_url,
            settings.mimo_model,
        ):
            if event.event == "field" and event.field_name:
                if event.field_name == "mermaid_chart":
                    result["method"] = result.get("method", {})
                    result["method"]["mermaid_chart"] = event.data.get("value")
                else:
                    result[event.field_name] = event.data.get("value")
            elif event.event == "error":
                raise Exception(event.data.get("message", "Analysis failed"))

        # 保存结果
        with Session(engine) as session:
            # 更新论文元信息
            update_paper_metadata(
                session,
                paper_id,
                result.get("title", ""),
                result.get("venue", "Unknown"),
                result.get("year", 0),
            )
            # 保存分析结果
            save_analysis_result(session, paper_id, result)
            # 更新状态为 done
            update_paper_status(session, paper_id, "done")

    except Exception as e:
        with Session(engine) as session:
            update_paper_status(session, paper_id, "failed", str(e))


# ============================================================
# POST /api/papers/upload
# ============================================================
@router.post("/upload", response_model=PaperResponse)
async def upload_paper(file: UploadFile = File(...)):
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
    os.makedirs(settings.upload_dir, exist_ok=True)

    # 生成唯一文件名并保存
    file_id = str(uuid.uuid4())
    filename = f"{file_id}.pdf"
    pdf_path = os.path.join(settings.upload_dir, filename)

    with open(pdf_path, "wb") as f:
        f.write(content)

    # 创建数据库记录
    from sqlmodel import create_engine
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    with Session(engine) as session:
        paper_id = create_paper(session, file.filename, pdf_path, len(content))
        paper = get_paper(session, paper_id)

    # 后台启动分析任务
    asyncio.create_task(_run_analysis(paper_id, pdf_path))

    return PaperResponse(
        id=paper["id"],
        filename=paper["filename"],
        title=paper["title"],
        venue=paper["venue"],
        year=paper["year"],
        status=paper["status"],
        error_message=paper["error_message"],
        created_at=paper["created_at"],
        updated_at=paper["updated_at"],
    )


# ============================================================
# GET /api/papers
# ============================================================
@router.get("", response_model=PaperListResponse)
async def get_papers_list(
    search: str = Query(None, description="Search by title"),
    venue: str = Query(None, description="Filter by venue"),
    year: int = Query(None, description="Filter by year"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
):
    """获取论文列表，支持筛选和搜索"""
    from sqlmodel import create_engine
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    with Session(engine) as session:
        papers, total = list_papers(session, search, venue, year, page, per_page)

    paper_responses = [
        PaperResponse(
            id=p["id"],
            filename=p["filename"],
            title=p["title"],
            venue=p["venue"],
            year=p["year"],
            status=p["status"],
            error_message=p["error_message"],
            created_at=p["created_at"],
            updated_at=p["updated_at"],
        )
        for p in papers
    ]

    return PaperListResponse(papers=paper_responses, total=total)


# ============================================================
# GET /api/papers/{paper_id}
# ============================================================
@router.get("/{paper_id}", response_model=PaperResponse)
async def get_paper_detail(paper_id: int):
    """获取单篇论文详情"""
    from sqlmodel import create_engine
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
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
        venue=paper["venue"],
        year=paper["year"],
        status=paper["status"],
        error_message=paper["error_message"],
        created_at=paper["created_at"],
        updated_at=paper["updated_at"],
    )


# ============================================================
# DELETE /api/papers/{paper_id}
# ============================================================
@router.delete("/{paper_id}")
async def delete_paper_endpoint(paper_id: int):
    """删除论文及其解析结果"""
    from sqlmodel import create_engine
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    try:
        with Session(engine) as session:
            pdf_path = delete_paper(session, paper_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"detail": f"Paper {paper_id} not found", "code": "NOT_FOUND"},
        )

    # 删除 PDF 文件
    if os.path.exists(pdf_path):
        os.remove(pdf_path)

    return {"message": "Paper deleted successfully"}


# ============================================================
# GET /api/papers/{paper_id}/export
# ============================================================
@router.get("/{paper_id}/export")
async def export_paper(paper_id: int):
    """导出论文解析结果为 Markdown"""
    from sqlmodel import create_engine
    from ..services.database import get_analysis

    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
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

    return Response(
        content=markdown,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{title}.md"'},
    )


# ============================================================
# GET /api/papers/{paper_id}/pdf
# ============================================================
@router.get("/{paper_id}/pdf")
async def get_paper_pdf(paper_id: int):
    """获取论文 PDF 文件"""
    from sqlmodel import create_engine
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
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
# Query Parameters:
#   - search: str = None      # 按标题/关键词搜索
#   - venue: str = None       # 按会议筛选
#   - year: int = None        # 按年份筛选
#   - page: int = 1           # 页码
#   - per_page: int = 20      # 每页数量
#
# Response 200: PaperListResponse
#   - papers: list[PaperResponse]  # 当前页的论文列表
#   - total: int                   # 总数


# ============================================================
# GET /api/papers/{paper_id}
# ============================================================
# 获取单篇论文详情。
#
# Path Parameters:
#   - paper_id: int
#
# Response 200: PaperResponse
#
# Response 404: ErrorResponse
#   - code="NOT_FOUND": 论文不存在


# ============================================================
# DELETE /api/papers/{paper_id}
# ============================================================
# 删除一篇论文及其解析结果。
#
# Path Parameters:
#   - paper_id: int
#
# Response 200: {"message": "Paper deleted successfully"}
#
# 业务流程：
#   1. 删除 AnalysisResult 记录（如果有）
#   2. 删除 Paper 记录
#   3. 删除上传的 PDF 文件
#
# Response 404: ErrorResponse
#   - code="NOT_FOUND": 论文不存在


# ============================================================
# GET /api/papers/{paper_id}/export
# ============================================================
# 导出论文解析结果为 Markdown。
#
# Path Parameters:
#   - paper_id: int
#
# Response 200: text/markdown
#   - Content-Disposition: attachment; filename="{title}.md"
#   - 返回格式化的 Markdown 文本
#
# Response 404: ErrorResponse
#   - code="NOT_FOUND": 论文不存在
#
# Response 400: ErrorResponse
#   - code="NOT_READY": 论文尚未解析完成


# ============================================================
# GET /api/papers/{paper_id}/pdf
# ============================================================
# 获取论文 PDF 文件（用于前端 PdfViewer 预览）。
#
# Path Parameters:
#   - paper_id: int
#
# Response 200: application/pdf
#   - 返回 PDF 文件流
#   - Content-Disposition: inline（浏览器内预览，不下载）
#
# Response 404: ErrorResponse
#   - code="NOT_FOUND": 论文不存在
#
# 实现要点：
#   1. 根据 paper_id 查询 Paper 记录获取 pdf_path
#   2. 使用 FileResponse 返回文件
#   3. media_type="application/pdf"
#   4. 前端通过此 URL 传给 react-pdf 的 Document 组件
