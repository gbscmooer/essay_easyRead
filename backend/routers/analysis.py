"""
Analysis 路由

处理论文解析结果的查询和流式解析。
"""

import asyncio
import logging
import httpx
from typing import Optional
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


_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})
    return _engine


# ============================================================
# GET/POST /api/analysis/config
# ============================================================

class AIModelConfig(BaseModel):
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    system_prompt: str = ""
    temperature: float = 0.1
    max_tokens: int = 4096

class UIConfig(BaseModel):
    highlight_duration: int = 3000
    zoom_step: float = 0.2
    zoom_min: float = 0.4
    zoom_max: float = 3.0
    pinch_zoom_step: float = 0.02
    theme: str = "punk"
    font: str = "sans"
    custom_font: str = ""

class EmbeddingConfig(BaseModel):
    provider: str = "local"
    model: str = "BAAI/bge-m3"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    batch_size: int = 32
    device: str = "auto"

class ResearchConfig(BaseModel):
    provider: str = "openalex"  # openalex | semantic_scholar | local
    email: str = ""
    api_key: str = ""
    similarity_threshold: float = 0.9

class RerankerConfig(BaseModel):
    enabled: bool = True
    provider: str = "siliconflow"
    model: str = "BAAI/bge-reranker-v2-m3"
    api_key: str = ""           # 空时回退到 embedding 的 api_key
    base_url: str = ""          # 空时回退到 embedding 的 base_url
    candidate_k: int = 30       # 向量检索召回数
    top_n: int = 5              # rerank 后送 LLM 的条数

class SimilarityConfig(BaseModel):
    """论文级相似度 provider 配置。"""
    provider: str = "bge_centroid"

class ArxivExpandConfig(BaseModel):
    """arXiv 引用列表扩展配置。"""
    enabled: bool = True
    top_k: int = 5
    use_llm_title: bool = True
    max_llm_refs: int = 15

class FindpapersConfig(BaseModel):
    """findpapers 多源学术搜索配置。"""
    enabled: bool = False
    databases: list[str] = ["arxiv", "openalex", "pubmed", "semantic_scholar"]
    max_results: int = 10

class AppConfig(BaseModel):
    translate: AIModelConfig = AIModelConfig()
    analysis: AIModelConfig = AIModelConfig()
    ui: UIConfig = UIConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    research: ResearchConfig = ResearchConfig()
    reranker: RerankerConfig = RerankerConfig()
    similarity: SimilarityConfig = SimilarityConfig()
    arxiv_expand: ArxivExpandConfig = ArxivExpandConfig()
    findpapers: FindpapersConfig = FindpapersConfig()


@router.get("/config", response_model=AppConfig)
async def get_config():
    """获取全部配置"""
    from ..services.ai_config import load_config
    config = load_config()
    _AE_FIELDS = set(ArxivExpandConfig.model_fields.keys())
    _FP_FIELDS = set(FindpapersConfig.model_fields.keys())
    return AppConfig(
        translate=AIModelConfig(**config.get("translate", {})),
        analysis=AIModelConfig(**config.get("analysis", {})),
        ui=UIConfig(**config.get("ui", {})),
        embedding=EmbeddingConfig(**config.get("embedding", {})),
        research=ResearchConfig(**config.get("research", {})),
        reranker=RerankerConfig(**config.get("reranker", {})),
        similarity=SimilarityConfig(**{k: v for k, v in config.get("similarity", {}).items() if k in ("provider",)}),
        arxiv_expand=ArxivExpandConfig(**{k: v for k, v in config.get("arxiv_expand", {}).items() if k in _AE_FIELDS}),
        findpapers=FindpapersConfig(**{k: v for k, v in config.get("findpapers", {}).items() if k in _FP_FIELDS}),
    )


@router.get("/similarity/providers")
async def list_similarity_providers():
    """暴露所有已注册的 similarity provider，供 Settings 下拉使用。"""
    from ..services.deep_research.similarity import list_providers
    return {"providers": list_providers()}


@router.post("/config")
async def update_config(req: AppConfig):
    """保存全部配置"""
    from ..services.ai_config import save_config
    from ..services.ai_config import load_config as _load
    prior = _load()
    # similarity：保留 provider 之外的 provider-specific 子对象
    prior_sim = prior.get("similarity", {})
    sim = {**prior_sim, **req.similarity.model_dump()}
    config = {
        "translate": req.translate.model_dump(),
        "analysis": req.analysis.model_dump(),
        "ui": req.ui.model_dump(),
        "embedding": req.embedding.model_dump(),
        "research": req.research.model_dump(),
        "reranker": req.reranker.model_dump(),
        "similarity": sim,
        "arxiv_expand": req.arxiv_expand.model_dump(),
        "findpapers": req.findpapers.model_dump(),
    }
    save_config(config)
    return {"message": "配置已保存"}


# ============================================================
# POST /api/analysis/test-connection
# ============================================================

class TestConnectionRequest(BaseModel):
    """测试模型连通性请求"""
    type: str  # "translate" | "analysis" | "embedding"
    # 可选：前端直接传入当前表单配置，不传则用已保存的配置
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None  # embedding 专用


class TestConnectionResponse(BaseModel):
    ok: bool
    message: str
    latency_ms: Optional[int] = None


@router.post("/test-connection", response_model=TestConnectionResponse)
async def test_connection(req: TestConnectionRequest):
    """测试 AI 模型服务是否可达"""
    import time

    if req.type in ("translate", "analysis"):
        return await _test_anthropic_connection(req)
    elif req.type == "embedding":
        return await _test_embedding_connection(req)
    else:
        raise HTTPException(status_code=400, detail="type 必须是 translate / analysis / embedding")


async def _test_anthropic_connection(req: TestConnectionRequest) -> TestConnectionResponse:
    """测试 Anthropic 兼容 API 连通性"""
    import time
    from ..services.ai_analyzer import _create_client
    from ..services.ai_config import get_translate_config, get_analysis_config

    if req.type == "translate":
        cfg = get_translate_config()
    else:
        cfg = get_analysis_config()

    api_key = req.api_key if req.api_key is not None else cfg.get("api_key", "")
    base_url = req.base_url if req.base_url is not None else cfg.get("base_url", "")
    model = req.model if req.model is not None else cfg.get("model", "")

    if not api_key:
        return TestConnectionResponse(ok=False, message="API Key 未填写")
    if not base_url:
        return TestConnectionResponse(ok=False, message="Base URL 未填写")
    if not model:
        return TestConnectionResponse(ok=False, message="模型名称未填写")

    t0 = time.monotonic()
    try:
        client = _create_client(api_key, base_url)
        try:
            message = await client.messages.create(
                model=model,
                max_tokens=5,
                system="Reply with OK.",
                messages=[{"role": "user", "content": "Hi"}],
            )
            latency = int((time.monotonic() - t0) * 1000)
            return TestConnectionResponse(ok=True, message=f"连接成功 ({model})", latency_ms=latency)
        finally:
            await client.close()
    except Exception as e:
        latency = int((time.monotonic() - t0) * 1000)
        err_msg = str(e)
        # 截取关键错误信息，避免过长
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        return TestConnectionResponse(ok=False, message=f"连接失败: {err_msg}", latency_ms=latency)


async def _test_embedding_connection(req: TestConnectionRequest) -> TestConnectionResponse:
    """测试 Embedding API 连通性"""
    import time
    from ..services.ai_config import get_embedding_config

    cfg = get_embedding_config()
    provider = req.provider if req.provider is not None else cfg.get("provider", "local")
    api_key = req.api_key if req.api_key is not None else cfg.get("api_key", "")
    base_url = req.base_url if req.base_url is not None else cfg.get("base_url", "")
    model = req.model if req.model is not None else cfg.get("model", "")

    if provider == "local":
        # 本地模型：测试 sentence-transformers 是否可加载
        t0 = time.monotonic()
        try:
            from sentence_transformers import SentenceTransformer
            m = SentenceTransformer(model, device="cpu")
            vec = m.encode(["test"], normalize_embeddings=True)
            latency = int((time.monotonic() - t0) * 1000)
            dim = len(vec[0])
            return TestConnectionResponse(ok=True, message=f"本地模型加载成功 (维度={dim})", latency_ms=latency)
        except Exception as e:
            latency = int((time.monotonic() - t0) * 1000)
            return TestConnectionResponse(ok=False, message=f"本地模型加载失败: {str(e)[:200]}", latency_ms=latency)

    # 远程 API（openai / siliconflow）
    if not api_key:
        return TestConnectionResponse(ok=False, message="API Key 未填写")
    if not base_url:
        return TestConnectionResponse(ok=False, message="Base URL 未填写")

    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "input": ["test"]},
            )
            r.raise_for_status()
            data = r.json()
            dim = len(data["data"][0]["embedding"])
            latency = int((time.monotonic() - t0) * 1000)
            return TestConnectionResponse(ok=True, message=f"连接成功 (维度={dim})", latency_ms=latency)
    except httpx.HTTPStatusError as e:
        latency = int((time.monotonic() - t0) * 1000)
        return TestConnectionResponse(ok=False, message=f"HTTP {e.response.status_code}: {e.response.text[:150]}", latency_ms=latency)
    except Exception as e:
        latency = int((time.monotonic() - t0) * 1000)
        return TestConnectionResponse(ok=False, message=f"连接失败: {str(e)[:200]}", latency_ms=latency)


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
    temperature = cfg.get("temperature", 0.1)
    max_tokens = cfg.get("max_tokens", 2048)

    if not api_key:
        raise HTTPException(status_code=400, detail="请先在配置页面设置 API Key")

    try:
        client = _create_client(api_key, base_url)
        try:
            message = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": req.text}],
                temperature=temperature,
            )
            result = message.content[0].text.strip()
            return TextActionResponse(result=result)
        finally:
            await client.close()
    except Exception as e:
        logger.error(f"Text action failed: {e}")
        raise HTTPException(status_code=500, detail=f"处理失败: {str(e)}")


# ============================================================
# Pydantic models for import endpoint (defined early, used later)
# ============================================================
class ImportPaperItem(BaseModel):
    title: str
    doi: str = ""
    url: str = ""


class ImportRelatedPapersRequest(BaseModel):
    workspace_id: int
    papers: list[ImportPaperItem]


# ============================================================
# POST /api/analysis/import-related-papers
# ============================================================
@router.post("/import-related-papers")
async def import_related_papers(req: ImportRelatedPapersRequest):
    """批量导入相关论文到工作区。

    对每篇论文：
    1. 按标题查重本地论文库
    2. 若已存在 → 直接关联到工作区
    3. 若不存在 → 通过 arXiv（优先）/ S2（备选）解析 PDF URL → 下载 → 创建记录 → 关联
    每篇下载之间设置 3 秒延迟以符合 arXiv 访问规范。
    """
    import os
    from ..models.paper import Paper
    from ..models.workspace import Workspace, WorkspacePaper
    from ..services.deep_research.pdf_downloader import download_pdf
    from sqlmodel import select

    engine = _get_engine()
    results = []

    # 验证工作区存在
    with Session(engine) as session:
        ws = session.get(Workspace, req.workspace_id)
        if not ws:
            raise HTTPException(status_code=404, detail="工作区不存在")

    for i, paper_item in enumerate(req.papers):
        title = paper_item.title.strip()
        if not title:
            results.append({"title": title, "status": "failed", "message": "标题为空"})
            continue

        # 延迟：第一篇不等待，后续每篇等待 3 秒（arXiv ToS 要求 ≥ 3 秒）
        if i > 0:
            await asyncio.sleep(3.0)

        # 1) 按标题精确查重
        with Session(engine, expire_on_commit=False) as session:
            existing = session.exec(
                select(Paper).where(Paper.title == title)
            ).first()

            if existing:
                # 已存在 → 关联到工作区（不重复添加）
                wp = session.exec(
                    select(WorkspacePaper)
                    .where(WorkspacePaper.workspace_id == req.workspace_id)
                    .where(WorkspacePaper.paper_id == existing.id)
                ).first()
                if not wp:
                    session.add(WorkspacePaper(workspace_id=req.workspace_id, paper_id=existing.id))
                    session.commit()
                results.append({
                    "title": title,
                    "status": "local",
                    "paper_id": existing.id,
                    "message": "从本地论文库导入",
                })
                continue

        # 2) 尝试获取 PDF URL
        pdf_url = await _resolve_pdf_url(paper_item)

        if not pdf_url:
            results.append({
                "title": title,
                "status": "failed",
                "message": "未找到可下载的 PDF",
            })
            continue

        # 3) 下载 PDF
        local_path = await download_pdf(pdf_url)
        if not local_path:
            results.append({
                "title": title,
                "status": "failed",
                "message": "PDF 下载失败",
            })
            continue

        # 4) 创建 Paper 记录并关联到工作区
        file_size = os.path.getsize(local_path) if os.path.exists(local_path) else 0

        with Session(engine, expire_on_commit=False) as session:
            # 再次查重（防止并发导入重复）
            dup = session.exec(
                select(Paper).where(Paper.title == title)
            ).first()
            if dup:
                wp = session.exec(
                    select(WorkspacePaper)
                    .where(WorkspacePaper.workspace_id == req.workspace_id)
                    .where(WorkspacePaper.paper_id == dup.id)
                ).first()
                if not wp:
                    session.add(WorkspacePaper(workspace_id=req.workspace_id, paper_id=dup.id))
                    session.commit()
                results.append({
                    "title": title,
                    "status": "local",
                    "paper_id": dup.id,
                    "message": "从本地论文库导入（并发去重）",
                })
                continue

            ext_id = ""
            if paper_item.doi:
                ext_id = f"DOI:{paper_item.doi}"

            p = Paper(
                filename=f"{title[:60]}.pdf",
                pdf_path=local_path,
                file_size=file_size,
                title=title,
                status="pending",
                source="uploaded",
                level=1,
                external_id=ext_id or None,
            )
            session.add(p)
            session.commit()
            session.refresh(p)

            session.add(WorkspacePaper(workspace_id=req.workspace_id, paper_id=p.id))
            session.commit()

        results.append({
            "title": title,
            "status": "downloaded",
            "paper_id": p.id,
            "message": "已下载并导入",
        })

    return {"results": results}


async def _resolve_pdf_url(paper_item: ImportPaperItem) -> str | None:
    """解析论文的可下载 PDF URL。

    当 findpapers 启用时，优先使用 findpapers 多源搜索（8 库并行 + 三层下载回退）。
    否则走默认链：arXiv → OpenAlex → Semantic Scholar。
    """
    title = paper_item.title.strip()
    if not title:
        return None

    # ---- findpapers 优先（如已启用）----
    try:
        from ..services.ai_config import get_findpapers_config
        fp_cfg = get_findpapers_config()
        if fp_cfg.get("enabled"):
            from ..services.findpapers_service import resolve_pdf_url
            url = await resolve_pdf_url(title, fp_cfg)
            if url:
                return url
            # findpapers 没找到，继续走下面的默认链
    except Exception:
        pass

    # ---- 1. arXiv 精确标题搜索 ----
    try:
        from ..services.deep_research.arxiv_client import search_by_title, ARXIV_PDF_BASE
        result = await search_by_title(title)
        if result and result.get("pdf_url"):
            return result["pdf_url"]
    except Exception:
        pass

    # ---- 2. OpenAlex 模糊搜索（可解析出 arXiv ID / PDF URL）----
    try:
        from ..services.deep_research.providers.openalex import (
            OpenAlexProvider, _extract_arxiv, _extract_pdf,
        )
        oa = OpenAlexProvider()
        try:
            data = await oa._get("/works", {
                "search": title,
                "per-page": 3,
                "select": "id,title,doi,ids,open_access,primary_location,locations",
            })
            for work in (data.get("results") or []):
                work_title = (work.get("title") or "").strip().lower()
                # 模糊匹配：标题的前 40 字符相同即可
                if work_title[:40] == title.lower()[:40]:
                    # 优先拿 arXiv ID → 构造 arXiv PDF 链接
                    arxiv_id = _extract_arxiv(work)
                    if arxiv_id:
                        return ARXIV_PDF_BASE.format(arxiv_id)
                    # 否则用 OpenAlex 提供的 PDF URL
                    pdf = _extract_pdf(work)
                    if pdf:
                        return pdf
        finally:
            await oa.aclose()
    except Exception:
        pass

    # ---- 3. Semantic Scholar 最后备选 ----
    try:
        from ..services.semantic_scholar import _rate_limited_get, S2_BASE
    except Exception:
        return None

    if paper_item.doi:
        try:
            data = await _rate_limited_get(
                f"{S2_BASE}/paper/DOI:{paper_item.doi}",
                params={"fields": "openAccessPdf,url"},
            )
            oa_pdf = data.get("openAccessPdf") or {}
            if oa_pdf.get("url"):
                return oa_pdf["url"]
        except Exception:
            pass

    try:
        data = await _rate_limited_get(
            f"{S2_BASE}/paper/search",
            params={"query": title, "fields": "openAccessPdf,title", "limit": 3},
        )
        for hit in data.get("data") or []:
            hit_title = (hit.get("title") or "").strip().lower()
            if hit_title == title.lower():
                oa_pdf = hit.get("openAccessPdf") or {}
                if oa_pdf.get("url"):
                    return oa_pdf["url"]
    except Exception:
        pass

    return None


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
                        value = dict(value)
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
            yield format_sse(StreamEvent(event="status", data={"status": "extracting", "message": "正在提取 PDF 文本..."}))

            # 提取 PDF 文本
            from ..services.pdf_parser import extract_pdf_content
            content = await asyncio.to_thread(extract_pdf_content, pdf_path)

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
                temperature=ai_cfg.get("temperature", 0.1),
                max_tokens=ai_cfg.get("max_tokens", 16384),
            ):
                yield format_sse(event)

                # 收集结果用于保存
                if event.event == "field" and event.field_name:
                    if event.field_name == "mermaid_chart":
                        result["method"] = result.get("method", {})
                        result["method"]["mermaid_chart"] = event.data.get("value")
                    else:
                        result[event.field_name] = event.data.get("value")

            # 搜索相关论文（findpapers 或 Semantic Scholar）
            try:
                paper_title = result.get("title", "")
                if paper_title:
                    yield format_sse(StreamEvent(
                        event="status",
                        data={"status": "analyzing", "message": "正在搜索相关论文..."},
                    ))
                    from ..services.ai_config import get_findpapers_config as _get_fp
                    from ..services.semantic_scholar import generate_relevance_descriptions
                    _fp = _get_fp()
                    if _fp.get("enabled"):
                        from ..services.findpapers_service import search_related_papers as _fp_search
                        related = await _fp_search(paper_title, _fp)
                    else:
                        from ..services.semantic_scholar import search_related_papers
                        related = await search_related_papers(paper_title)
                    if related:
                        related = await generate_relevance_descriptions(
                            paper_title,
                            result.get("one_sentence", ""),
                            related,
                            ai_cfg["api_key"],
                            ai_cfg["base_url"],
                            ai_cfg["model"],
                        )
                    result["related_papers"] = related
                    yield format_sse(StreamEvent(
                        event="field",
                        field_name="related_papers",
                        data={"value": related},
                    ))
            except Exception as e:
                logger.warning(f"Semantic Scholar lookup failed: {e}")
                result["related_papers"] = []

            # 提取代码仓库 URL（正则匹配）
            from ..services.ai_analyzer import extract_code_url
            code_url = extract_code_url(content.full_text)
            if code_url:
                yield format_sse(StreamEvent(
                    event="status",
                    data={"status": "analyzing", "message": f"找到代码仓库: {code_url}"},
                ))

            # 保存结果到数据库
            if result:
                # 兜底必填字段，防止 AI 返回不完整 JSON 时 500
                for key, default in [
                    ("title", ""), ("venue", "Unknown"), ("year", 0),
                    ("one_sentence", ""), ("problem", ""),
                    ("key_contributions", []), ("outline", []),
                    ("method", {"overview": "", "pipeline": "", "key_modules": []}),
                    ("experiments", {"datasets": [], "baselines": [], "main_results": "", "ablation": "", "result_table": [], "figures": []}),
                    ("code_mapping", []), ("interview_qa", []),
                    ("limitations", ""), ("related_papers", []),
                ]:
                    result.setdefault(key, default)

                from ..services.database import update_paper_metadata, save_analysis_result, update_paper_code_url
                with Session(engine) as session:
                    update_paper_metadata(
                        session,
                        paper_id,
                        result.get("title", ""),
                        result.get("venue", "Unknown"),
                        result.get("year", 0),
                    )
                    save_analysis_result(session, paper_id, result)
                    if code_url:
                        update_paper_code_url(session, paper_id, code_url)
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
        code_url=paper.get("code_url"),
        code_status=paper.get("code_status", "none"),
        created_at=paper["created_at"],
        updated_at=paper["updated_at"],
    )


# ============================================================
# POST /api/analysis/{paper_id}/related-papers
# ============================================================
@router.post("/{paper_id}/related-papers")
async def refresh_related_papers(paper_id: int):
    """刷新相关论文（根据设置使用 findpapers 或 Semantic Scholar）"""
    engine = _get_engine()

    # 获取已有分析结果
    try:
        with Session(engine) as session:
            analysis = get_analysis(session, paper_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="分析结果不存在")

    paper_title = analysis.get("title", "")
    if not paper_title:
        raise HTTPException(status_code=400, detail="论文标题为空，无法搜索")

    from ..services.ai_config import get_analysis_config, get_findpapers_config

    ai_cfg = get_analysis_config()
    fp_cfg = get_findpapers_config()

    try:
        if fp_cfg.get("enabled"):
            # findpapers 多源搜索
            from ..services.findpapers_service import search_related_papers as fp_search
            related = await fp_search(paper_title, fp_cfg)
            logger.info(f"findpapers search returned {len(related)} papers")
        else:
            # 原有 Semantic Scholar 搜索
            from ..services.semantic_scholar import search_related_papers
            related = await search_related_papers(paper_title)

        if related:
            from ..services.semantic_scholar import generate_relevance_descriptions
            related = await generate_relevance_descriptions(
                paper_title,
                analysis.get("one_sentence", ""),
                related,
                ai_cfg["api_key"],
                ai_cfg["base_url"],
                ai_cfg["model"],
            )
    except Exception as e:
        logger.warning(f"Related papers refresh failed: {e}")
        related = []

    # 更新数据库中的 related_papers 字段
    from ..services.database import update_related_papers
    with Session(engine) as session:
        update_related_papers(session, paper_id, related)

    return related
