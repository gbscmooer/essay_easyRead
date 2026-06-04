"""
Analysis 路由

处理论文解析结果的查询和流式解析。
"""

import asyncio
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, create_engine

logger = logging.getLogger(__name__)

from ..models.schemas import AnalysisResponse, StreamEvent
from ..config import settings
from ..services.database import get_paper, get_analysis, update_paper_status
from ..utils.streaming import format_sse

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def _get_engine():
    return create_engine(settings.database_url, connect_args={"check_same_thread": False})


# ============================================================
# GET/POST /api/analysis/config
# ============================================================

class AIModelConfig(BaseModel):
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    system_prompt: str = ""

class AppConfig(BaseModel):
    translate: AIModelConfig = AIModelConfig()
    analysis: AIModelConfig = AIModelConfig()


@router.get("/config", response_model=AppConfig)
async def get_config():
    """获取 AI 模型配置"""
    from ..services.ai_config import load_config
    config = load_config()
    return AppConfig(
        translate=AIModelConfig(**config.get("translate", {})),
        analysis=AIModelConfig(**config.get("analysis", {})),
    )


@router.post("/config")
async def update_config(req: AppConfig):
    """保存 AI 模型配置"""
    from ..services.ai_config import save_config
    config = {
        "translate": req.translate.model_dump(),
        "analysis": req.analysis.model_dump(),
    }
    save_config(config)
    return {"message": "配置已保存"}


# ============================================================
# POST /api/analysis/text-action
# ============================================================

class TextActionRequest(BaseModel):
    text: str
    action: str  # "translate" | "summarize"

class TextActionResponse(BaseModel):
    result: str


@router.post("/text-action", response_model=TextActionResponse)
async def text_action(req: TextActionRequest):
    """对选中文本进行翻译或 AI 总结"""
    from ..services.ai_analyzer import _create_client
    from ..services.ai_config import get_translate_config, get_analysis_config

    if not req.text.strip():
        raise HTTPException(status_code=400, detail="文本不能为空")

    if req.action not in ("translate", "summarize"):
        raise HTTPException(status_code=400, detail="action 必须是 translate 或 summarize")

    # 根据 action 使用不同配置
    if req.action == "translate":
        cfg = get_translate_config()
        system = cfg.get("system_prompt") or "你是一个专业的学术翻译助手。将用户提供的英文论文片段翻译为中文。只输出翻译结果，不要添加任何解释。保持学术术语的准确性。"
    else:
        cfg = get_analysis_config()
        system = "你是一个专业的学术论文分析助手。用中文简洁地总结用户提供的论文片段的核心要点。用 2-3 个要点概括，每个要点一行，以 • 开头。"

    api_key = cfg.get("api_key", "")
    base_url = cfg.get("base_url", "")
    model = cfg.get("model", "")

    if not api_key:
        raise HTTPException(status_code=400, detail="请先在配置页面设置 API Key")

    try:
        client = _create_client(api_key, base_url)
        message = await client.messages.create(
            model=model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": req.text}],
            temperature=0.1,
        )
        result = message.content[0].text.strip()
        return TextActionResponse(result=result)
    except Exception as e:
        logger.error(f"Text action failed: {e}")
        raise HTTPException(status_code=500, detail=f"处理失败: {str(e)}")


# ============================================================
# GET /api/analysis/{paper_id}
# ============================================================
@router.get("/{paper_id}", response_model=AnalysisResponse)
async def get_analysis_result(paper_id: int):
    """获取论文的完整解析结果"""
    engine = _get_engine()
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"detail": f"Paper {paper_id} not found", "code": "NOT_FOUND"},
        )

    if paper["status"] == "failed":
        raise HTTPException(
            status_code=400,
            detail={"detail": paper.get("error_message", "Analysis failed"), "code": "PARSING_FAILED"},
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

    return AnalysisResponse(**analysis)


# ============================================================
# GET /api/analysis/{paper_id}/stream
# ============================================================
@router.get("/{paper_id}/stream")
async def stream_analysis(paper_id: int):
    """SSE 流式解析端点"""
    engine = _get_engine()

    # 获取论文状态
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"detail": f"Paper {paper_id} not found", "code": "NOT_FOUND"},
        )

    async def event_generator():
        """SSE 事件生成器"""

        # 根据论文状态决定行为
        if paper["status"] == "done":
            # 已完成：直接发送已存储的结果
            try:
                with Session(engine) as session:
                    analysis = get_analysis(session, paper_id)
            except ValueError:
                yield format_sse(StreamEvent(event="error", data={"message": "Analysis result not found"}))
                return

            # 逐字段发送
            fields_order = [
                "title", "venue", "year", "one_sentence", "problem",
                "key_contributions", "outline", "method", "experiments",
                "code_mapping", "interview_qa", "limitations", "related_papers",
            ]
            for field_name in fields_order:
                if field_name in analysis:
                    value = analysis[field_name]
                    # method 中的 mermaid_chart 需要单独处理
                    if field_name == "method" and isinstance(value, dict):
                        mermaid = value.pop("mermaid_chart", None)
                        yield format_sse(StreamEvent(
                            event="field",
                            field_name=field_name,
                            data={"value": value},
                        ))
                        if mermaid:
                            yield format_sse(StreamEvent(
                                event="field",
                                field_name="mermaid_chart",
                                data={"value": mermaid},
                            ))
                    else:
                        yield format_sse(StreamEvent(
                            event="field",
                            field_name=field_name,
                            data={"value": value},
                        ))

            yield format_sse(StreamEvent(event="done", data={"message": "解析完成"}))
            return

        if paper["status"] == "failed":
            yield format_sse(StreamEvent(
                event="error",
                data={"message": paper.get("error_message", "Analysis failed")},
            ))
            return

        # 状态为 pending/extracting/analyzing：重新启动分析
        pdf_path = paper.get("pdf_path")
        if not pdf_path:
            # 需要从数据库获取完整的 paper 记录
            from ..models.paper import Paper as PaperModel
            with Session(engine) as session:
                db_paper = session.get(PaperModel, paper_id)
                if db_paper:
                    pdf_path = db_paper.pdf_path

        if not pdf_path:
            yield format_sse(StreamEvent(event="error", data={"message": "PDF file path not found"}))
            return

        # 启动新的分析流程
        try:
            # 更新状态为 extracting
            with Session(engine) as session:
                update_paper_status(session, paper_id, "extracting")
            yield format_sse(StreamEvent(event="status", data={"message": "正在提取 PDF 文本..."}))

            # 提取 PDF 文本
            from ..services.pdf_parser import extract_pdf_content
            content = extract_pdf_content(pdf_path)

            # 更新状态为 analyzing
            with Session(engine) as session:
                update_paper_status(session, paper_id, "analyzing")

            # 调用 AI 分析并流式推送（使用解读配置）
            from ..services.ai_analyzer import analyze_paper_stream
            from ..services.ai_config import get_analysis_config
            ai_cfg = get_analysis_config()
            result = {}
            async for event in analyze_paper_stream(
                content.full_text,
                ai_cfg["api_key"],
                ai_cfg["base_url"],
                ai_cfg["model"],
            ):
                yield format_sse(event)

                # 收集结果用于保存
                if event.event == "field" and event.field_name:
                    if event.field_name == "mermaid_chart":
                        result["method"] = result.get("method", {})
                        result["method"]["mermaid_chart"] = event.data.get("value")
                    else:
                        result[event.field_name] = event.data.get("value")

            # 保存结果到数据库
            if result:
                from ..services.database import update_paper_metadata, save_analysis_result
                with Session(engine) as session:
                    update_paper_metadata(
                        session,
                        paper_id,
                        result.get("title", ""),
                        result.get("venue", "Unknown"),
                        result.get("year", 0),
                    )
                    save_analysis_result(session, paper_id, result)
                    update_paper_status(session, paper_id, "done")

        except Exception as e:
            with Session(engine) as session:
                update_paper_status(session, paper_id, "failed", str(e))
            yield format_sse(StreamEvent(
                event="error",
                data={"message": f"解析失败: {str(e)}"},
            ))

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
# POST /api/analysis/{paper_id}/reparse
# ============================================================
@router.post("/{paper_id}/reparse")
async def reparse_paper(paper_id: int):
    """重新解析一篇论文"""
    engine = _get_engine()
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"detail": f"Paper {paper_id} not found", "code": "NOT_FOUND"},
        )

    # 重置状态为 pending
    with Session(engine) as session:
        update_paper_status(session, paper_id, "pending", None)

    # 获取更新后的论文信息
    with Session(engine) as session:
        paper = get_paper(session, paper_id)

    from ..models.schemas import PaperResponse
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
