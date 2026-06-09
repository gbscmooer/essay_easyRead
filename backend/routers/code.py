"""
Code 路由

处理代码仓库的 clone、文件浏览和代码分析。
"""

import asyncio
import logging
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from ..config import settings
from ..models.schemas import StreamEvent, CodeAnalysisResponse, CodeSummaryResponse
from ..services.database import get_engine
from ..utils.streaming import format_sse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/code", tags=["code"])


# ============================================================
# POST /api/code/{paper_id}/extract-url
# ============================================================
@router.post("/{paper_id}/extract-url")
async def extract_url(paper_id: int):
    """重新从论文中提取代码仓库 URL"""
    engine = get_engine()

    from ..services.database import get_paper
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="论文不存在")

    # 获取 PDF 文件路径
    from ..models.paper import Paper as PaperModel
    with Session(engine) as session:
        db_paper = session.get(PaperModel, paper_id)
        if not db_paper:
            raise HTTPException(status_code=404, detail="论文不存在")
        pdf_path = db_paper.pdf_path

    # 提取 PDF 文本并用正则搜索代码 URL
    from ..services.pdf_parser import extract_pdf_content
    from ..services.ai_analyzer import extract_code_url

    try:
        content = await asyncio.to_thread(extract_pdf_content, pdf_path)
    except Exception as e:
        logger.warning(f"PDF extraction failed for {paper_id}: {e}")
        return {"code_url": None}
    code_url = extract_code_url(content.full_text)

    # 更新数据库
    from ..services.database import update_paper_code_url
    with Session(engine) as session:
        update_paper_code_url(session, paper_id, code_url)

    return {"code_url": code_url or None}


# ============================================================
# POST /api/code/{paper_id}/clone
# ============================================================
@router.post("/{paper_id}/clone")
async def clone_repo(paper_id: int):
    """Clone 论文的代码仓库"""
    engine = get_engine()

    # 获取论文信息
    from ..services.database import get_paper
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="论文不存在")

    code_url = paper.get("code_url")
    if not code_url:
        raise HTTPException(status_code=400, detail="该论文未找到代码仓库 URL")

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
        raise HTTPException(status_code=500, detail=f"克隆失败: {str(e)}")


# ============================================================
# GET /api/code/{paper_id}/tree
# ============================================================
@router.get("/{paper_id}/tree")
async def get_file_tree(paper_id: int):
    """获取代码仓库的文件树"""
    engine = get_engine()

    from ..services.database import get_paper
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="论文不存在")

    code_status = paper.get("code_status", "none")
    if code_status != "ready":
        raise HTTPException(status_code=400, detail=f"代码仓库状态: {code_status}")

    code_path = paper.get("code_path")
    if not code_path:
        raise HTTPException(status_code=400, detail="代码路径不存在")

    from ..services.code_analyzer import build_file_tree
    tree = await asyncio.to_thread(build_file_tree, code_path)
    return tree


# ============================================================
# GET /api/code/{paper_id}/file
# ============================================================
@router.get("/{paper_id}/file")
async def get_file_content(paper_id: int, path: str = Query(..., description="文件相对路径")):
    """获取代码文件内容"""
    engine = get_engine()

    from ..services.database import get_paper
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="论文不存在")

    code_path = paper.get("code_path")
    if not code_path:
        raise HTTPException(status_code=400, detail="代码路径不存在")

    from ..services.code_analyzer import read_file_content, read_notebook_content
    try:
        if path.endswith(".ipynb"):
            cells = await asyncio.to_thread(read_notebook_content, code_path, path)
            return {"path": path, "content": None, "cells": cells}
        content = await asyncio.to_thread(read_file_content, code_path, path)
        return {"path": path, "content": content}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="文件不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================
# GET /api/code/{paper_id}/analyze
# ============================================================
@router.get("/{paper_id}/analyze")
async def analyze_code(paper_id: int):
    """触发代码分析（SSE 流式）"""
    engine = get_engine()

    from ..services.database import get_paper
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="论文不存在")

    code_status = paper.get("code_status", "none")
    if code_status != "ready":
        raise HTTPException(status_code=400, detail="代码仓库未就绪")

    code_path = paper.get("code_path")
    if not code_path:
        raise HTTPException(status_code=400, detail="代码路径不存在")

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
            except Exception:
                logger.debug("Failed to read file %s, skipping", f)
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
                    paper.get("title", ""),
                    ai_cfg["api_key"],
                    ai_cfg["base_url"],
                    ai_cfg["model"],
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
                        existing_mapping = analysis.get("code_mapping", [])
                except Exception:
                    logger.debug("Failed to load existing code_mapping for paper %d", paper_id)

                paper_code_mapping = await ai_analyze_paper_code_mapping(
                    file_contents,
                    paper.get("title", ""),
                    analysis.get("one_sentence", "") if analysis else "",
                    existing_mapping,
                    ai_cfg["api_key"],
                    ai_cfg["base_url"],
                    ai_cfg["model"],
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
async def get_code_analysis(paper_id: int):
    """获取代码分析结果"""
    engine = get_engine()

    from ..models.code_analysis import CodeAnalysis
    from sqlmodel import select

    with Session(engine) as session:
        statement = select(CodeAnalysis).where(CodeAnalysis.paper_id == paper_id)
        analysis = session.exec(statement).first()

    if not analysis:
        raise HTTPException(status_code=404, detail="代码分析结果不存在")

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
async def get_code_summary(paper_id: int):
    """获取代码摘要（侧边栏用）"""
    engine = get_engine()

    from ..services.database import get_paper
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="论文不存在")

    return CodeSummaryResponse(
        code_url=paper.get("code_url"),
        code_status=paper.get("code_status", "none"),
        code_summary=paper.get("code_summary"),
    )
