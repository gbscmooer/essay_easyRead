"""
Code 路由

处理代码仓库的 clone、文件浏览和代码分析。
"""

import asyncio
import os
import logging
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from ..services.database import get_engine
from ..models.schemas import StreamEvent, CodeAnalysisResponse, CodeSummaryResponse
from ..utils.streaming import format_sse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/code", tags=["code"])


# ============================================================
# POST /api/code/{paper_id}/extract-url
# ============================================================
@router.post("/{paper_id}/extract-url")
async def extract_url(paper_id: int) -> dict:
    """重新从论文中提取代码仓库 URL（优先使用数据库中的 full_text，避免重复解析 PDF）"""
    engine = get_engine()

    from ..models.paper import Paper as PaperModel
    from ..services.ai_analyzer import extract_code_url

    with Session(engine) as session:
        paper = session.get(PaperModel, paper_id)
        if not paper:
            raise HTTPException(status_code=404, detail={"detail": "论文不存在", "code": "NOT_FOUND"})

        # 优先使用数据库中已有的 full_text，避免重新解析 PDF
        if paper.full_text:
            code_url = extract_code_url(paper.full_text)
            if code_url:
                paper.code_url = code_url
                session.add(paper)
                session.commit()
                return {"code_url": code_url}

        # 回退：解析 PDF
        pdf_path = paper.pdf_path
        if not pdf_path or not os.path.exists(pdf_path):
            raise HTTPException(status_code=404, detail={"detail": "PDF文件不存在", "code": "NOT_FOUND"})

        from ..services.pdf_parser import extract_pdf_content

        try:
            content = await asyncio.to_thread(extract_pdf_content, pdf_path)
        except Exception as e:
            logger.warning(f"PDF extraction failed for {paper_id}: {e}")
            return {"code_url": None}

        code_url = extract_code_url(content.full_text)

        if code_url:
            paper.code_url = code_url
            session.add(paper)
            session.commit()

        return {"code_url": code_url or None}


# ============================================================
# POST /api/code/{paper_id}/clone
# ============================================================
@router.post("/{paper_id}/clone")
async def clone_repo(paper_id: int) -> dict:
    """Clone 论文的代码仓库"""
    engine = get_engine()

    # 获取论文信息
    from ..services.database import get_paper
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"detail": "论文不存在", "code": "NOT_FOUND"})

    code_url = paper.code_url
    if not code_url:
        raise HTTPException(status_code=400, detail={"detail": "该论文未找到代码仓库 URL", "code": "NOT_FOUND"})

    # 更新状态为 cloning
    from ..services.database import update_paper_status
    with Session(engine) as session:
        from ..models.paper import Paper
        db_paper = session.get(Paper, paper_id)
        if db_paper:
            db_paper.code_status = "cloning"
            session.add(db_paper)
            session.commit()

    try:
        from ..services.code_analyzer import clone_repository
        clone_path = await clone_repository(code_url, paper_id)

        # 更新状态为 ready
        with Session(engine) as session:
            db_paper = session.get(Paper, paper_id)
            if db_paper:
                db_paper.code_status = "ready"
                db_paper.code_path = clone_path
                session.add(db_paper)
                session.commit()

        return {"message": "克隆成功", "path": clone_path}

    except Exception as e:
        logger.error(f"Clone failed: {e}")
        with Session(engine) as session:
            db_paper = session.get(Paper, paper_id)
            if db_paper:
                db_paper.code_status = "failed"
                session.add(db_paper)
                session.commit()
        raise HTTPException(status_code=500, detail={"detail": f"克隆失败: {str(e)}", "code": "INTERNAL_ERROR"})


# ============================================================
# GET /api/code/{paper_id}/tree
# ============================================================
@router.get("/{paper_id}/tree")
async def get_file_tree(paper_id: int) -> dict:
    """获取代码仓库的文件树（优先从 code_analyses 缓存读取）"""
    engine = get_engine()

    from ..models.code_analysis import CodeAnalysis
    from sqlmodel import select

    with Session(engine) as session:
        # 优先从 code_analyses 表读取已缓存的文件树，避免重复遍历磁盘
        analysis = session.exec(
            select(CodeAnalysis).where(CodeAnalysis.paper_id == paper_id)
        ).first()
        if analysis and analysis.file_tree:
            return analysis.file_tree

    # 回退：从磁盘构建
    from ..services.database import get_paper
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"detail": "论文不存在", "code": "NOT_FOUND"})

    code_status = paper.code_status or "none"
    if code_status != "ready":
        raise HTTPException(status_code=400, detail={"detail": f"代码仓库状态: {code_status}", "code": "NOT_READY"})

    code_path = paper.code_path
    if not code_path:
        raise HTTPException(status_code=400, detail={"detail": "代码路径不存在", "code": "NOT_FOUND"})

    from ..services.code_analyzer import build_file_tree
    tree = await asyncio.to_thread(build_file_tree, code_path)
    return tree


# ============================================================
# GET /api/code/{paper_id}/file
# ============================================================
@router.get("/{paper_id}/file")
async def get_file_content(paper_id: int, path: str = Query(..., description="文件相对路径")) -> dict:
    """获取代码文件内容"""
    engine = get_engine()

    from ..services.database import get_paper
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"detail": "论文不存在", "code": "NOT_FOUND"})

    code_path = paper.code_path
    if not code_path:
        raise HTTPException(status_code=400, detail={"detail": "代码路径不存在", "code": "NOT_FOUND"})

    from ..services.code_analyzer import read_file_content, read_notebook_content
    try:
        # Path traversal guard: resolved path must stay within code_path
        resolved = os.path.realpath(os.path.join(code_path, path))
        repo_real = os.path.realpath(code_path)
        if os.path.commonpath([resolved, repo_real]) != repo_real:
            raise HTTPException(status_code=400, detail={"detail": "不安全的文件路径", "code": "INVALID_FORMAT"})

        if path.endswith(".ipynb"):
            cells = read_notebook_content(code_path, path)
            return {"path": path, "content": None, "cells": cells}
        content = read_file_content(code_path, path)
        return {"path": path, "content": content}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail={"detail": "文件不存在", "code": "NOT_FOUND"})
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"detail": str(e), "code": "INVALID_FORMAT"})


# ============================================================
# GET /api/code/{paper_id}/analyze
# ============================================================
@router.get("/{paper_id}/analyze")
async def analyze_code(paper_id: int) -> StreamingResponse:
    """触发代码分析（SSE 流式）"""
    engine = get_engine()

    from ..services.database import get_paper
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"detail": "论文不存在", "code": "NOT_FOUND"})

    code_status = paper.code_status or "none"
    if code_status != "ready":
        raise HTTPException(status_code=400, detail={"detail": "代码仓库未就绪", "code": "NOT_READY"})

    code_path = paper.code_path
    if not code_path:
        raise HTTPException(status_code=400, detail={"detail": "代码路径不存在", "code": "NOT_FOUND"})

    async def event_generator():
        from ..services.code_analyzer import build_file_tree, select_core_files, read_file_content, analyze_imports
        from ..services.ai_config import get_analysis_config
        from ..services.database import get_analysis

        ai_cfg = get_analysis_config()

        # 1. 构建文件树
        yield format_sse(StreamEvent(event="status", data={"message": "正在分析文件结构..."}))
        tree = build_file_tree(code_path)

        # 2. 静态分析：依赖图
        yield format_sse(StreamEvent(event="status", data={"message": "正在分析模块依赖..."}))
        core_files = select_core_files(tree, code_path)
        deps, node_metrics = analyze_imports(code_path, core_files)

        # 3. 读取核心文件内容
        yield format_sse(StreamEvent(event="status", data={"message": "正在读取核心文件..."}))
        file_contents = {}
        for f in core_files:
            try:
                content = read_file_content(code_path, f, max_lines=200)
                if content and not content.startswith("["):
                    file_contents[f] = content
            except Exception as e:
                logger.debug(f"Failed to read file {f}: {e}")
                continue

        # 4. AI 分析
        data_flow = ""
        paper_code_mapping = []

        if file_contents and ai_cfg.get("api_key"):
            # 数据流分析
            yield format_sse(StreamEvent(event="status", data={"message": "AI 正在分析数据流..."}))
            try:
                from ..services.code_analyzer import ai_analyze_data_flow
                data_flow = await ai_analyze_data_flow(
                    file_contents,
                    paper.title or "",
                    ai_cfg["api_key"],
                    ai_cfg["base_url"],
                    ai_cfg["model"],
                    provider=ai_cfg.get("provider", ""),
                    api_format=ai_cfg.get("api_format", ""),
                    thinking_effort=ai_cfg.get("thinking_effort", "off"),
                )
            except Exception as e:
                logger.warning(f"Data flow analysis failed: {e}")
                data_flow = "分析失败"

            # 论文-代码映射
            yield format_sse(StreamEvent(event="status", data={"message": "AI 正在映射论文与代码..."}))
            try:
                from ..services.code_analyzer import ai_analyze_paper_code_mapping
                # 获取已有的 code_mapping
                existing_mapping = []
                analysis = None
                existing_mapping = []
                try:
                    with Session(engine) as session:
                        analysis = get_analysis(session, paper_id)
                        existing_mapping = analysis.code_mapping or []
                except Exception as e:
                    logger.debug(f"Failed to load existing code_mapping for paper {paper_id}: {e}")

                paper_code_mapping = await ai_analyze_paper_code_mapping(
                    file_contents,
                    paper.title or "",
                    (analysis.one_sentence or "") if analysis else "",
                    existing_mapping,
                    ai_cfg["api_key"],
                    ai_cfg["base_url"],
                    ai_cfg["model"],
                    provider=ai_cfg.get("provider", ""),
                    api_format=ai_cfg.get("api_format", ""),
                    thinking_effort=ai_cfg.get("thinking_effort", "off"),
                )
            except Exception as e:
                logger.warning(f"Paper-code mapping failed: {e}")

        # 5. 保存结果
        yield format_sse(StreamEvent(event="status", data={"message": "正在保存分析结果..."}))
        try:
            from ..models.code_analysis import CodeAnalysis
            with Session(engine) as session:
                from sqlmodel import select
                statement = select(CodeAnalysis).where(CodeAnalysis.paper_id == paper_id)
                existing = session.exec(statement).first()

                if existing:
                    existing.file_tree = tree
                    existing.data_flow = data_flow
                    existing.dependency_graph = deps
                    existing.paper_code_mapping = paper_code_mapping
                    existing.node_metrics = node_metrics
                    existing.status = "done"
                    session.add(existing)
                else:
                    analysis = CodeAnalysis(
                        paper_id=paper_id,
                        file_tree=tree,
                        data_flow=data_flow,
                        dependency_graph=deps,
                        paper_code_mapping=paper_code_mapping,
                        node_metrics=node_metrics,
                        status="done",
                    )
                    session.add(analysis)
                session.commit()
        except Exception as e:
            logger.error(f"Failed to save code analysis: {e}")
            yield format_sse(StreamEvent(event="error", data={"message": f"保存失败: {str(e)}"}))
            return

        # 6. 生成 README 摘要
        try:
            from ..services.code_analyzer import ai_summarize_readme
            readme_files = [f for f in file_contents if "readme" in f.lower()]
            if readme_files and ai_cfg.get("api_key"):
                summary = await ai_summarize_readme(
                    file_contents[readme_files[0]],
                    ai_cfg["api_key"],
                    ai_cfg["base_url"],
                    ai_cfg["model"],
                    provider=ai_cfg.get("provider", ""),
                    api_format=ai_cfg.get("api_format", ""),
                    thinking_effort=ai_cfg.get("thinking_effort", "off"),
                )
                with Session(engine) as session:
                    from ..models.paper import Paper as PaperModel
                    db_paper = session.get(PaperModel, paper_id)
                    if db_paper:
                        db_paper.code_summary = summary
                        session.add(db_paper)
                        session.commit()
        except Exception as e:
            logger.warning(f"README summary failed: {e}")

        yield format_sse(StreamEvent(event="done", data={"message": "代码分析完成"}))

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# GET /api/code/{paper_id}/analysis
# ============================================================
@router.get("/{paper_id}/analysis", response_model=CodeAnalysisResponse)
async def get_code_analysis(paper_id: int) -> CodeAnalysisResponse:
    """获取代码分析结果"""
    engine = get_engine()

    from ..models.code_analysis import CodeAnalysis
    from sqlmodel import select

    with Session(engine) as session:
        statement = select(CodeAnalysis).where(CodeAnalysis.paper_id == paper_id)
        analysis = session.exec(statement).first()

    if not analysis:
        raise HTTPException(status_code=404, detail={"detail": "代码分析结果不存在", "code": "NOT_FOUND"})

    return CodeAnalysisResponse(
        paper_id=analysis.paper_id,
        file_tree=analysis.file_tree,
        data_flow=analysis.data_flow,
        dependency_graph=analysis.dependency_graph,
        paper_code_mapping=analysis.paper_code_mapping,
        node_metrics=getattr(analysis, 'node_metrics', []) or [],
        status=analysis.status,
        error_message=analysis.error_message,
    )


# ============================================================
# GET /api/code/{paper_id}/summary
# ============================================================
@router.get("/{paper_id}/summary", response_model=CodeSummaryResponse)
async def get_code_summary(paper_id: int) -> CodeSummaryResponse:
    """获取代码摘要（侧边栏用）"""
    engine = get_engine()

    from ..services.database import get_paper
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"detail": "论文不存在", "code": "NOT_FOUND"})

    return CodeSummaryResponse(
        code_url=paper.code_url,
        code_status=paper.code_status or "none",
        code_summary=paper.code_summary,
    )
