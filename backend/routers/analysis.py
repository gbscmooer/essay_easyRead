"""
Analysis 路由

处理论文解析结果的查询和流式解析。
"""

import asyncio
import logging
import httpx
from datetime import datetime, timezone
from typing import Optional, Literal
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session

logger = logging.getLogger(__name__)

from ..models.schemas import AnalysisResponse, PaperResponse, StreamEvent
from ..services.database import get_paper, get_analysis, update_paper_status, get_engine
from ..services.bibtex import format_bibtex
from ..utils.streaming import format_sse

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


# ============================================================
# GET/POST /api/analysis/config
# ============================================================

class AIModelConfig(BaseModel):
    provider: str = "openai"
    api_format: Literal["openai_chat", "anthropic_messages"] = "openai_chat"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    temperature: float = 0.2
    max_tokens: int = 16384
    # 思考强度 off | low | medium | high，翻译成各协议的思考参数
    # （Claude thinking.budget_tokens / OpenAI reasoning_effort / DeepSeek thinking.type）。
    # 仅对支持的推理模型生效；Anthropic 协议开启思考时强制 temperature=1。
    thinking_effort: str = "off"
    timeout_seconds: float = Field(default=150.0, ge=1.0, le=600.0)

class UIConfig(BaseModel):
    highlight_duration: int = 3000
    zoom_step: float = 0.2
    zoom_min: float = 0.4
    zoom_max: float = 3.0
    pinch_zoom_step: float = 0.02
    theme: str = "light"
    font: str = "sans"
    custom_font: str = ""
    custom_themes: list[dict] = []  # 用户自定义主题 [{id, name, colors:{...}}]

class EmbeddingConfig(BaseModel):
    provider: str = "local"
    model: str = "BAAI/bge-m3"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    batch_size: int = 32
    device: str = "auto"
    # —— 远程 embedding 高级参数（不同模型上下文长度不同，可手动调整）——
    # 单条输入按 token 估算的截断上限。默认 7000 适配 bge-m3 的 8192；
    # 若换 512-token 模型（如 bge-large-en-v1.5）调到 ~480；更大上下文的模型可调更高。
    max_input_tokens: int = 7000
    # 可选的硬字符上限（0=不启用）。token 估算之外再加一道保险，极端长文也不会超。
    max_input_chars: int = 0

class RecallWeightsConfig(BaseModel):
    """多路召回 RRF 各通道权重。"""
    pdf_ref: float = 1.0
    backward: float = 1.0
    forward: float = 1.0
    recommend: float = 1.0
    graph_ppr: float = 1.0


class RecallConfig(BaseModel):
    """联网扩展多路召回 / RRF 融合配置（research.recall）。"""
    method: str = "weighted_rrf_v1"
    enabled_sources: list[str] = ["backward", "forward", "recommend", "pdf_ref", "graph_ppr"]
    rrf_k: int = 60
    weights: RecallWeightsConfig = RecallWeightsConfig()
    candidate_limit: int = 50


class HybridWeightsConfig(BaseModel):
    dense: float = 1.0
    bm25: float = 1.0
    graph_ppr: float = 1.0


class GraphRetrievalConfig(BaseModel):
    ppr_alpha: float = 0.85
    top_k: int = 50
    bridge_weight_factor: float = 0.0


class HybridRetrievalConfig(BaseModel):
    method: str = "weighted_rrf_v1"
    rrf_k: int = 60
    weights: HybridWeightsConfig = HybridWeightsConfig()
    graph: GraphRetrievalConfig = GraphRetrievalConfig()


class SynthesizeConfig(BaseModel):
    """领域认知综合（stage_synthesize）：图谱后注入学术语义。"""
    enabled: bool = True


class ResearchConfig(BaseModel):
    provider: str = "auto"        # auto | scopus | openalex | semantic_scholar | local
    email: str = ""
    api_key: str = ""
    elsevier_api_key: str = ""    # Elsevier / Scopus API Key（校园网内可留空）
    map_density: float = 8.0       # 兼容旧配置，当前骨架算法忽略
    backbone_alpha: float = 0.05   # Disparity Filter 局部显著性阈值，不是准确率
    backbone_method: str = "mutual_knn_with_disparity_annotation_v1"
    link_distance: int = 50        # 图谱节点边长基准
    auto_digest: bool = True       # 深度研究时自动「图谱速读」未解读论文
    discovery_v2: bool = False     # 发布门禁通过前默认关闭，可显式启用 discovery v2
    synthesize: SynthesizeConfig = SynthesizeConfig()  # 领域认知综合（stage_synthesize）
    recall: RecallConfig = RecallConfig()
    hybrid_retrieval: HybridRetrievalConfig = HybridRetrievalConfig()

class RerankerConfig(BaseModel):
    enabled: bool = True
    provider: str = "siliconflow"
    model: str = "BAAI/bge-reranker-v2-m3"
    api_key: str = ""           # 空时回退到 embedding 的 api_key
    base_url: str = ""          # 空时回退到 embedding 的 base_url
    candidate_k: int = 30       # 向量检索召回数
    top_n: int = 5              # rerank 后送 LLM 的条数

class RelationWeightsConfig(BaseModel):
    """论文关联度的八个通道权重（见 graphkit/relatedness.py）。"""
    topic: float = 0.125
    purpose: float = 0.125
    method: float = 0.125
    term: float = 0.125
    dataset: float = 0.125
    baseline: float = 0.125
    bibcoupling: float = 0.125
    cocitation: float = 0.125

class RelationsConfig(BaseModel):
    """论文关联度配置（全系统唯一一套算法，无 provider 可选）。"""
    method: str = "weighted_rrf_v1"
    # 兼容旧配置；保存和计算均不再启用自动校准
    calibrate: bool = False
    weights: RelationWeightsConfig = RelationWeightsConfig()

class ArxivExpandConfig(BaseModel):
    """arXiv 引用列表扩展配置。"""
    enabled: bool = True
    parser: str = "grobid"                          # grobid | regex
    grobid_url: str = "http://localhost:8070"       # GROBID 服务地址
    top_k: int = 5
    use_llm_title: bool = True
    max_llm_refs: int = 15


class PromptTemplate(BaseModel):
    """解读提示词副本。"""
    id: str
    name: str
    content: str = ""


class AnalysisPromptConfig(BaseModel):
    """解读系统提示词配置。"""
    active: str = "builtin"            # builtin | custom:<id>
    custom: list[PromptTemplate] = []

class FindpapersConfig(BaseModel):
    """findpapers 多源学术搜索配置。"""
    enabled: bool = False
    databases: list[str] = ["arxiv", "openalex", "pubmed", "semantic_scholar"]
    max_results: int = 10

class AppConfig(BaseModel):
    llm: AIModelConfig = AIModelConfig()
    analysis_prompt: AnalysisPromptConfig = AnalysisPromptConfig()
    ui: UIConfig = UIConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    research: ResearchConfig = ResearchConfig()
    reranker: RerankerConfig = RerankerConfig()
    relations: RelationsConfig = RelationsConfig()
    arxiv_expand: ArxivExpandConfig = ArxivExpandConfig()
    findpapers: FindpapersConfig = FindpapersConfig()


@router.get("/config", response_model=AppConfig)
async def get_config() -> AppConfig:
    """获取全部配置（API 密钥被掩码）"""
    from ..services.ai_config import load_config, mask_config, DEFAULT_CONFIG
    config = mask_config(load_config())
    _AE_FIELDS = set(ArxivExpandConfig.model_fields.keys())
    _FP_FIELDS = set(FindpapersConfig.model_fields.keys())
    research_raw = config.get("research", {})
    default_research = DEFAULT_CONFIG.get("research", {})
    recall_raw = {**(default_research.get("recall") or {}), **(research_raw.get("recall") or {})}
    research_payload = {**default_research, **research_raw, "recall": recall_raw}
    llm_payload = config.get("llm") or config.get("analysis") or {}
    return AppConfig(
        llm=AIModelConfig(**llm_payload),
        analysis_prompt=AnalysisPromptConfig(**config.get("analysis_prompt", {})),
        ui=UIConfig(**config.get("ui", {})),
        embedding=EmbeddingConfig(**config.get("embedding", {})),
        research=ResearchConfig(**research_payload),
        reranker=RerankerConfig(**config.get("reranker", {})),
        relations=RelationsConfig(
            method=(config.get("relations") or {}).get("method", DEFAULT_CONFIG["relations"]["method"]),
            calibrate=False,
            weights=RelationWeightsConfig(**(config.get("relations", {}).get("weights") or {})),
        ),
        arxiv_expand=ArxivExpandConfig(**{k: v for k, v in config.get("arxiv_expand", {}).items() if k in _AE_FIELDS}),
        findpapers=FindpapersConfig(**{k: v for k, v in config.get("findpapers", {}).items() if k in _FP_FIELDS}),
    )


@router.get("/llm/providers")
async def list_llm_providers() -> dict:
    """Return provider presets owned by the LLM Core for settings-page rendering."""
    from ..services.llm import PROVIDER_PRESETS

    return {"providers": list(PROVIDER_PRESETS)}


@router.get("/relations/channels")
async def list_relation_channels() -> dict:
    """关联度通道元信息（名称/分组/默认权重/说明），供设置页渲染权重编辑器。"""
    from ..services.deep_research.graphkit import channel_catalog
    return {"channels": channel_catalog()}


@router.post("/config")
async def update_config(req: AppConfig) -> dict:
    """保存全部配置"""
    from ..services.ai_config import save_config, merge_preserving_keys, DEFAULT_CONFIG
    from ..services.ai_config import load_config as _load
    prior = _load()
    # relations：权重子段深合并，避免只改一个通道时丢失其他通道
    default_weights = DEFAULT_CONFIG.get("relations", {}).get("weights", {})
    relations = {
        "method": DEFAULT_CONFIG["relations"]["method"],
        "calibrate": False,
        "weights": {
            **default_weights,
            **((prior.get("relations") or {}).get("weights") or {}),
            **req.relations.weights.model_dump(exclude_unset=True),
        },
    }
    prior_research = prior.get("research", {})
    research_dump = req.research.model_dump(exclude_unset=True)
    research_merged = {**prior_research, **research_dump}
    # 保留部分更新未指定的检索偏好与图参数。
    for name in ("recall", "hybrid_retrieval"):
        defaults = DEFAULT_CONFIG["research"][name]
        before = prior_research.get(name) or {}
        patch = research_dump.get(name) or {}
        merged = {**defaults, **before, **patch, "method": defaults["method"]}
        for nested in ("weights", "graph"):
            if nested not in defaults:
                continue
            merged[nested] = {
                **defaults[nested], **(before.get(nested) or {}), **(patch.get(nested) or {}),
            }
        research_merged[name] = merged
    config = {
        "llm": req.llm.model_dump(),
        "analysis_prompt": req.analysis_prompt.model_dump(),
        "ui": req.ui.model_dump(),
        "embedding": req.embedding.model_dump(),
        "research": research_merged,
        "reranker": req.reranker.model_dump(),
        "relations": relations,
        "arxiv_expand": req.arxiv_expand.model_dump(),
        "findpapers": req.findpapers.model_dump(),
    }
    # 关键：前端回填的 api_key 可能是掩码占位（***xxxx），不能原样写回，否则会毁掉真 key
    config = merge_preserving_keys(config, prior)
    save_config(config)
    return {"message": "配置已保存"}


@router.get("/config/file-info")
async def get_config_file_info_endpoint() -> dict:
    """获取当前生效的配置文件信息（路径、格式、存在性）。"""
    from ..services.ai_config import get_config_file_info
    return get_config_file_info()


@router.get("/config/export")
async def export_config_endpoint(
    request: Request,
    format: str = Query(default="yaml", pattern="^(yaml|yml|toml|json)$"),
    include_secrets: bool = Query(default=True),
) -> Response:
    """导出系统配置（支持 yaml、toml、json），供迁移与备份。
    安全策略：本地环境可导出含密钥的配置文件；非本地请求自动强制脱敏。
    """
    from ..services.ai_config import export_config
    from ..services.terminal_access import trusted_local_peer

    client_host = request.client.host if request.client else ""
    forwarded = request.headers.get("x-forwarded-for")
    is_local = (client_host in ("127.0.0.1", "localhost", "testclient")) or trusted_local_peer(client_host, forwarded)

    mask = not (include_secrets and is_local)
    content, filename, media_type = export_config(fmt=format, mask_keys=mask)
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Config-Masked": "true" if mask else "false",
        },
    )


@router.post("/config/import")
async def import_config_endpoint(request: Request) -> dict:
    """导入系统配置（支持上传 YAML / TOML / JSON 文件，或提交对应文本内容）。"""
    from ..services.ai_config import import_config, get_config_file_info
    from ..services.terminal_access import trusted_local_peer

    # 安全检查：仅允许本地调用导入接口，避免外部网络非法篡改系统配置
    client_host = request.client.host if request.client else ""
    forwarded = request.headers.get("x-forwarded-for")
    is_local = (client_host in ("127.0.0.1", "localhost", "testclient")) or trusted_local_peer(client_host, forwarded)
    if not is_local:
        raise HTTPException(status_code=403, detail="出于安全原因，配置导入仅支持从本地环境发起")

    content_type = request.headers.get("content-type", "")
    text = ""
    fmt = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("file")
        if upload and hasattr(upload, "read"):
            text_bytes = await upload.read()
            text = text_bytes.decode("utf-8", errors="replace")
            fname = getattr(upload, "filename", "") or ""
            if fname.endswith((".yaml", ".yml")):
                fmt = "yaml"
            elif fname.endswith(".toml"):
                fmt = "toml"
            elif fname.endswith(".json"):
                fmt = "json"
        elif "content" in form:
            text = str(form.get("content", ""))
            fmt = str(form.get("format", "")) or None
    elif "application/json" in content_type:
        try:
            body = await request.json()
            if isinstance(body, dict):
                text = body.get("content", "")
                fmt = body.get("format")
            else:
                raise ValueError("JSON body must be an object")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"无法解析 JSON 请求体: {e}")
    else:
        raw_body = await request.body()
        text = raw_body.decode("utf-8", errors="replace")

    if not text.strip():
        raise HTTPException(status_code=400, detail="导入内容为空，请提供有效的配置文件内容")

    try:
        import_config(text, fmt=fmt)
    except Exception as e:
        logger.warning(f"Failed to import config: {e}")
        raise HTTPException(status_code=400, detail=f"导入失败: {e}")

    return {
        "message": "配置导入成功并已即时生效",
        "file_info": get_config_file_info(),
    }


# ============================================================
# 解读提示词：内置只读
# ============================================================
@router.get("/builtin-analysis-prompt")
async def get_builtin_analysis_prompt() -> dict:
    """返回内置（只读）解读系统提示词，供前端展示与「复制为副本」。"""
    from ..services.ai_analyzer import SYSTEM_PROMPT
    return {"content": SYSTEM_PROMPT}


# ============================================================
# GROBID 服务：状态查询 + 镜像下载
# ============================================================
GROBID_IMAGE = "grobid/grobid:0.9.0-crf"

# 镜像拉取状态（进程内）
_grobid_pull = {"running": False, "ok": None, "log": "", "image": GROBID_IMAGE}


async def _docker_image_present(image: str) -> bool:
    """docker image inspect 判断镜像是否已存在。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "image", "inspect", image,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return proc.returncode == 0
    except FileNotFoundError:
        logger.debug("Docker command not found")
        return False
    except Exception as e:
        logger.debug(f"Docker image inspect failed for {image}: {e}")
        return False


@router.get("/grobid/status")
async def grobid_status() -> dict:
    """查询 GROBID 服务情况：服务是否可达 + 镜像是否已下载 + 是否正在拉取。"""
    from ..services.ai_config import get_arxiv_expand_config
    from ..services.deep_research.grobid_parser import is_grobid_alive
    cfg = get_arxiv_expand_config()
    url = cfg.get("grobid_url", "http://localhost:8070")
    alive = False
    try:
        alive = await is_grobid_alive(url)
    except Exception as e:
        logger.debug(f"GROBID health check failed: {e}")
        alive = False
    image_present = await _docker_image_present(GROBID_IMAGE)
    return {
        "url": url,
        "alive": alive,
        "image": GROBID_IMAGE,
        "image_present": image_present,
        "pulling": _grobid_pull["running"],
        "pull_ok": _grobid_pull["ok"],
        "pull_log": _grobid_pull["log"][-2000:],
    }


async def _run_grobid_pull():
    """后台执行 docker pull，记录输出与结果。"""
    _grobid_pull.update(running=True, ok=None, log=f"$ docker pull {GROBID_IMAGE}\n")
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "pull", GROBID_IMAGE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout is not None
        async for raw in proc.stdout:
            _grobid_pull["log"] += raw.decode(errors="replace")
            _grobid_pull["log"] = _grobid_pull["log"][-4000:]
        await proc.wait()
        _grobid_pull["ok"] = (proc.returncode == 0)
        if proc.returncode != 0:
            _grobid_pull["log"] += f"\n[docker pull 退出码 {proc.returncode}]"
    except FileNotFoundError:
        _grobid_pull["ok"] = False
        _grobid_pull["log"] += "\n[未找到 docker 命令，请先安装并启动 Docker]"
    except Exception as e:
        _grobid_pull["ok"] = False
        _grobid_pull["log"] += f"\n[拉取失败: {e}]"
    finally:
        _grobid_pull["running"] = False


@router.post("/grobid/pull")
async def grobid_pull() -> dict:
    """后台下载 GROBID 镜像（docker pull）。幂等：已在拉取则返回当前状态。"""
    if not _grobid_pull["running"]:
        asyncio.create_task(_run_grobid_pull())
    return {"running": True, "image": GROBID_IMAGE}


# ============================================================
# POST /api/analysis/findpapers/test-databases
# ============================================================

class FindpapersTestRequest(BaseModel):
    """findpapers 数据库功能检测请求。

    databases：要测的库（传 1 个=单独测，传多个=测全部）。省略时用已保存配置里启用的库。
    """
    databases: Optional[list[str]] = None


class DatabaseTestResult(BaseModel):
    database: str
    label: str
    searchable: bool                  # 能否用 findpapers 搜到结果
    downloadable: bool                # 能否真正下载到 PDF（已验证 PDF 头）
    needs_key: bool = False
    latency_ms: Optional[int] = None
    message: str


class FindpapersTestResponse(BaseModel):
    findpapers_installed: bool        # findpapers 库是否已 pip 安装
    results: list[DatabaseTestResult]


@router.post("/findpapers/test-databases", response_model=FindpapersTestResponse)
async def findpapers_test_databases(req: FindpapersTestRequest) -> FindpapersTestResponse:
    """逐库实测 findpapers 数据库：能否「搜索」+ 能否「下载 PDF」。

    不是简单 ping——真用 findpapers 搜该库、取一条结果的 PDF 并流式下载头部字节验证。
    传单个 database = 单独测；传多个 = 并行测全部。
    """
    from ..services.findpapers_service import test_databases, is_findpapers_installed
    from ..services.ai_config import get_findpapers_config

    databases = req.databases
    if databases is None:
        databases = get_findpapers_config().get("databases", [])

    results = await test_databases(databases)
    return FindpapersTestResponse(
        findpapers_installed=is_findpapers_installed(),
        results=[DatabaseTestResult(**r) for r in results],
    )


# ============================================================
# POST /api/analysis/test-connection
# ============================================================

class TestConnectionRequest(BaseModel):
    """测试模型连通性请求"""
    type: str  # "llm" | "translate" | "analysis" | "embedding"
    # 可选：前端直接传入当前表单配置，不传则用已保存的配置
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    api_format: Optional[Literal["openai_chat", "anthropic_messages"]] = None
    timeout_seconds: Optional[float] = Field(default=None, ge=1.0, le=600.0)


class TestConnectionResponse(BaseModel):
    ok: bool
    message: str
    latency_ms: Optional[int] = None
    request_id: Optional[str] = None
    api_format: Optional[str] = None


@router.post("/test-connection", response_model=TestConnectionResponse)
async def test_connection(req: TestConnectionRequest) -> TestConnectionResponse:
    """测试 AI 模型服务是否可达"""
    import time
    from ..services.ai_config import is_masked_key

    # 表单回填的 api_key 可能是掩码占位（***xxxx）。掩码值不是真 key，置 None 让各 test
    # 函数回退到盘上已保存的真实 key，否则会拿着 *** 去鉴权必然失败。
    if req.api_key is not None and is_masked_key(req.api_key):
        req.api_key = None

    if req.type in ("llm", "translate", "analysis"):
        return await _test_llm_connection(req)
    elif req.type == "embedding":
        return await _test_embedding_connection(req)
    else:
        raise HTTPException(status_code=400, detail={"detail": "type 必须是 llm / translate / analysis / embedding", "code": "INVALID_FORMAT"})


async def _test_llm_connection(req: TestConnectionRequest) -> TestConnectionResponse:
    """Test the explicit provider protocol through the shared LLM Core."""
    from ..services.ai_config import get_llm_config, get_translate_config, get_analysis_config
    from ..services.llm import test_connection as test_llm_connection

    if req.type == "translate":
        cfg = get_translate_config()
    elif req.type == "analysis":
        cfg = get_analysis_config()
    else:
        cfg = get_llm_config()

    api_key = req.api_key if req.api_key is not None else cfg.get("api_key", "")
    base_url = req.base_url if req.base_url is not None else cfg.get("base_url", "")
    model = req.model if req.model is not None else cfg.get("model", "")
    result = await test_llm_connection({
        **cfg,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "provider": req.provider if req.provider is not None else cfg.get("provider", "custom"),
        "api_format": req.api_format if req.api_format is not None else cfg.get("api_format"),
        "timeout_seconds": req.timeout_seconds if req.timeout_seconds is not None else cfg.get("timeout_seconds", 150),
    })
    message = result["message"] if result["ok"] else f"连接失败: {result['message']}"
    return TestConnectionResponse(**{**result, "message": message})


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
    action: Literal["translate", "summarize"]  # "translate" | "summarize"

class TextActionResponse(BaseModel):
    result: str


@router.post("/text-action", response_model=TextActionResponse)
async def text_action(req: TextActionRequest) -> TextActionResponse:
    """对选中文本进行翻译或 AI 总结"""
    from ..services.text_actions import run_text_action

    if not req.text.strip():
        raise HTTPException(status_code=400, detail={"detail": "文本不能为空", "code": "INVALID_FORMAT"})

    if req.action not in ("translate", "summarize"):
        raise HTTPException(status_code=400, detail={"detail": "action 必须是 translate 或 summarize", "code": "INVALID_FORMAT"})

    try:
        return TextActionResponse(result=await run_text_action(req.text, req.action))
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"detail": str(e), "code": "INVALID_FORMAT"})
    except Exception as e:
        logger.error(f"Text action failed: {e}")
        raise HTTPException(status_code=500, detail={"detail": f"处理失败: {str(e)}", "code": "INTERNAL_ERROR"})


# ============================================================
# POST /api/analysis/digest/... —— 图谱速读（轻量解读）
# ============================================================
class DigestBatchRequest(BaseModel):
    workspace_id: int
    force: bool = False        # True 则连已速读的也重做（已精读的始终跳过）
    max_papers: int = 200      # 单次批量安全上限


async def _run_digest(text: str, title: str, cfg: dict) -> dict:
    from ..services.ai_analyzer import generate_graph_digest
    return await generate_graph_digest(
        text, cfg["api_key"], cfg.get("base_url", ""), cfg["model"], title=title,
        thinking_effort=cfg.get("thinking_effort"),
        provider=cfg.get("provider", "custom"),
        api_format=cfg.get("api_format"),
    )


# 批量速读后台任务进度（按 workspace_id 记在内存）。单进程 uvicorn 足够；
# 结果本身逐篇持久化到 Paper.graph_digest，故即使进程重启丢了进度，再点一次会
# 自动跳过已速读的论文、从断点继续（断点续跑）。
_DIGEST_JOBS: dict[int, dict] = {}


def _digest_job_view(ws_id: int) -> dict:
    """对外暴露的任务进度快照。"""
    job = _DIGEST_JOBS.get(ws_id)
    if not job:
        return {"workspace_id": ws_id, "running": False, "total": 0,
                "done": 0, "failed": 0, "skipped_no_text": 0, "last_error": None}
    return {
        "workspace_id": ws_id,
        "running": job["running"],
        "total": job["total"],
        "done": job["done"],
        "failed": job["failed"],
        "skipped_no_text": job["skipped_no_text"],
        "last_error": job["last_error"],
    }


async def _digest_worker(ws_id: int, todo: list[dict], cfg: dict) -> None:
    """后台批量速读：逐篇调用 LLM 并**立即落盘**，是断点续跑的关键。"""
    from ..models.paper import Paper

    job = _DIGEST_JOBS[ws_id]
    engine = get_engine()
    sem = asyncio.Semaphore(4)   # 限并发，避免触发限流

    async def _one(item: dict):
        async with sem:
            try:
                d = await _run_digest(item["text"], item["title"], cfg)
            except Exception as e:
                logger.warning(f"digest batch: paper {item['id']} failed: {e}")
                job["failed"] += 1
                job["last_error"] = str(e)[:200]
                return
            # 逐篇立即持久化（独立短事务，不跨 await 持有 session）
            try:
                with Session(engine) as session:
                    p = session.get(Paper, item["id"])
                    if p:
                        p.graph_digest = d
                        p.updated_at = datetime.now(timezone.utc)
                        session.add(p)
                        session.commit()
                job["done"] += 1
            except Exception as e:
                logger.warning(f"digest batch: persist paper {item['id']} failed: {e}")
                job["failed"] += 1
                job["last_error"] = str(e)[:200]

    try:
        await asyncio.gather(*[_one(it) for it in todo])
    finally:
        job["running"] = False


@router.post("/digest/batch")
async def digest_batch(req: DigestBatchRequest) -> dict:
    """批量「图谱速读」一个工作区里所有未解读的论文（精读优先，已精读跳过）。

    只抽知识图谱需要的 facet + 简短总结，token 成本远低于全量精读。后台异步执行、
    逐篇立即落盘，可断点续跑；前端轮询 GET /digest/status/{ws} 看进度。速读完成后
    需重新构建图谱（前端触发）才会让新 facet 关系生效。
    """
    from sqlmodel import select
    from ..services.ai_config import get_analysis_config
    from ..models.workspace import WorkspacePaper
    from ..models.paper import Paper
    from ..models.analysis import AnalysisResult

    cfg = get_analysis_config()
    if not cfg.get("api_key"):
        raise HTTPException(status_code=400, detail={"detail": "请先在设置页配置解读模型的 API Key", "code": "NO_API_KEY"})

    # 已有任务在跑：直接回传当前进度，不重复启动
    existing = _DIGEST_JOBS.get(req.workspace_id)
    if existing and existing["running"]:
        return {"started": False, "already_running": True, **_digest_job_view(req.workspace_id)}

    engine = get_engine()
    with Session(engine) as session:
        papers = session.exec(
            select(Paper).join(WorkspacePaper, WorkspacePaper.paper_id == Paper.id)
            .where(WorkspacePaper.workspace_id == req.workspace_id)
        ).all()
        if not papers:
            raise HTTPException(status_code=404, detail={"detail": "工作区不存在或没有论文", "code": "NOT_FOUND"})
        pids = [p.id for p in papers]
        analyzed = set(session.exec(
            select(AnalysisResult.paper_id).where(AnalysisResult.paper_id.in_(pids))
        ).all())
        todo, no_text = [], 0
        for p in papers:
            if p.id in analyzed:
                continue                                  # 已精读
            if p.graph_digest and not req.force:
                continue                                  # 已速读
            if not (p.full_text or "").strip():
                no_text += 1                              # 没全文，无法速读
                continue
            todo.append({"id": p.id, "text": p.full_text, "title": p.title or p.filename or ""})

    todo = todo[:max(0, req.max_papers)]
    if not todo:
        return {"workspace_id": req.workspace_id, "started": False, "candidates": 0,
                "total": 0, "skipped_no_text": no_text}

    _DIGEST_JOBS[req.workspace_id] = {
        "running": True, "total": len(todo), "done": 0, "failed": 0,
        "skipped_no_text": no_text, "last_error": None,
    }
    asyncio.create_task(_digest_worker(req.workspace_id, todo, cfg))
    return {"workspace_id": req.workspace_id, "started": True, "candidates": len(todo),
            "total": len(todo), "skipped_no_text": no_text}


@router.get("/digest/status/{workspace_id}")
async def digest_status(workspace_id: int) -> dict:
    """查询某工作区批量速读的实时进度（前端轮询）。"""
    return _digest_job_view(workspace_id)


@router.post("/digest/{paper_id}")
async def digest_paper(paper_id: int) -> dict:
    """单篇「图谱速读」。"""
    from ..services.ai_config import get_analysis_config
    from ..models.paper import Paper

    cfg = get_analysis_config()
    if not cfg.get("api_key"):
        raise HTTPException(status_code=400, detail={"detail": "请先在设置页配置解读模型的 API Key", "code": "NO_API_KEY"})

    engine = get_engine()
    with Session(engine) as session:
        try:
            paper = get_paper(session, paper_id)
        except Exception:
            raise HTTPException(status_code=404, detail={"detail": "论文不存在", "code": "NOT_FOUND"})
        text = paper.full_text or ""
        title = paper.title or paper.filename or ""
    if not text.strip():
        raise HTTPException(status_code=400, detail={"detail": "该论文没有可用全文（未提取/未下载 PDF），无法速读", "code": "NO_TEXT"})

    try:
        digest = await _run_digest(text, title, cfg)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"detail": f"速读失败: {e}", "code": "DIGEST_FAILED"})

    with Session(engine) as session:
        p = session.get(Paper, paper_id)
        p.graph_digest = digest
        p.updated_at = datetime.now(timezone.utc)
        session.add(p)
        session.commit()
    return {"paper_id": paper_id, "digest": digest}


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
async def import_related_papers(req: ImportRelatedPapersRequest) -> dict:
    """批量导入相关论文到工作区。

    对每篇论文：
    1. 按 paper_identity 匹配本地论文库（canonical/doi/arxiv/…/fuzzy）
    2. 若已存在 → 关联到工作区
    3. 若不存在 → 登记 stub（可无 PDF）→ 尝试下载
    发现与下载解耦：下载失败时元数据仍保留。
    每篇下载之间设置 3 秒延迟以符合 arXiv 访问规范。
    """
    from ..models.workspace import Workspace
    from ..services.paper_download import download_for_paper
    from ..services.paper_identity import PaperWork, find_or_register

    engine = get_engine()
    results = []

    # 验证工作区存在
    with Session(engine) as session:
        ws = session.get(Workspace, req.workspace_id)
        if not ws:
            raise HTTPException(status_code=404, detail={"detail": "工作区不存在", "code": "NOT_FOUND"})

    for i, paper_item in enumerate(req.papers):
        title = paper_item.title.strip()
        if not title:
            results.append({"title": title, "status": "failed", "message": "标题为空"})
            continue

        # 延迟：第一篇不等待，后续每篇等待 3 秒（arXiv ToS 要求 ≥ 3 秒）
        if i > 0:
            await asyncio.sleep(3.0)

        work = PaperWork(
            title=title,
            doi=paper_item.doi or None,
            url=paper_item.url or None,
            filename=f"{title[:60]}.pdf",
        )

        with Session(engine, expire_on_commit=False) as session:
            paper, is_new = find_or_register(session, work, req.workspace_id)

        # 尝试下载（已有 PDF 则跳过）
        with Session(engine, expire_on_commit=False) as download_session:
            local_path = await download_for_paper(download_session, paper.id)

        if local_path:
            results.append({
                "title": title,
                "status": "downloaded" if is_new else "local",
                "paper_id": paper.id,
                "message": "已下载并导入" if is_new else "从本地论文库导入",
            })
        elif not is_new:
            results.append({
                "title": title,
                "status": "local",
                "paper_id": paper.id,
                "message": "从本地论文库导入（无 PDF）",
            })
        else:
            results.append({
                "title": title,
                "status": "registered",
                "paper_id": paper.id,
                "message": "已登记元数据，PDF 暂不可用",
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
    except Exception as e:
        logger.debug(f"findpapers resolve_pdf_url failed for '{title}': {e}")

    # ---- 1. arXiv 精确标题搜索 ----
    try:
        from ..services.deep_research.arxiv_client import search_by_title, ARXIV_PDF_BASE
        result = await search_by_title(title)
        if result and result.get("pdf_url"):
            return result["pdf_url"]
    except Exception as e:
        logger.debug(f"arXiv search_by_title failed for '{title}': {e}")

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
    except Exception as e:
        logger.debug(f"OpenAlex search failed for '{title}': {e}")

    # ---- 3. Semantic Scholar 最后备选 ----
    try:
        from ..services.semantic_scholar import _rate_limited_get, S2_BASE
    except Exception as e:
        logger.debug(f"Failed to import Semantic Scholar module: {e}")
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
        except Exception as e:
            logger.debug(f"S2 DOI lookup failed for '{paper_item.doi}': {e}")

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
    except Exception as e:
        logger.debug(f"S2 title search failed for '{title}': {e}")

    return None


@router.get("/{paper_id}/bibtex")
async def get_analysis_bibtex(paper_id: int) -> Response:
    """Export available paper metadata as BibTeX."""
    engine = get_engine()
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
            try:
                analysis = get_analysis(session, paper_id)
            except ValueError:
                analysis = None
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"detail": f"Paper {paper_id} not found", "code": "NOT_FOUND"},
        )

    title = (analysis.title if analysis and analysis.title else paper.title) or ""
    authors = (analysis.authors if analysis and analysis.authors else paper.authors) or ""
    venue = (analysis.venue if analysis and analysis.venue else paper.venue) or ""
    year = analysis.year if analysis and analysis.year else paper.year

    if not title.strip():
        raise HTTPException(
            status_code=400,
            detail={"detail": "BibTeX metadata is not ready", "code": "NOT_READY"},
        )

    return Response(
        content=format_bibtex(title, authors, venue, year),
        media_type="text/plain; charset=utf-8",
    )


@router.get("/{paper_id}", response_model=AnalysisResponse)
async def get_analysis_result(paper_id: int) -> AnalysisResponse:
    """获取论文的完整解析结果"""
    engine = get_engine()
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"detail": f"Paper {paper_id} not found", "code": "NOT_FOUND"},
        )

    if paper.status == "failed":
        raise HTTPException(
            status_code=400,
            detail={"detail": paper.error_message or "Analysis failed", "code": "PARSING_FAILED"},
        )

    if paper.status != "done":
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

    return AnalysisResponse(
        paper_id=analysis.paper_id,
        title=analysis.title,
        authors=analysis.authors or "",
        venue=analysis.venue,
        year=analysis.year,
        one_sentence=analysis.one_sentence,
        problem=analysis.problem,
        key_contributions=analysis.key_contributions,
        outline=analysis.outline or [],
        method=analysis.method,
        experiments=analysis.experiments,
        code_mapping=analysis.code_mapping,
        interview_qa=analysis.interview_qa,
        limitations=analysis.limitations,
        related_papers=analysis.related_papers,
        mermaid_chart=analysis.mermaid_chart,
    )


# ============================================================
# GET /api/analysis/{paper_id}/stream
# ============================================================
@router.get("/{paper_id}/stream")
async def stream_analysis(paper_id: int) -> StreamingResponse:
    """SSE 流式解析端点"""
    engine = get_engine()

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
        """SSE 事件生成器。

        已完成/失败：直接从数据库回放结果。
        进行中/待分析：订阅后台分析任务（analysis_runner），
        实时接收进度与字段。客户端断开仅取消订阅，不会终止后台分析。
        """

        # 根据论文状态决定行为
        if paper.status == "done":
            # 已完成：直接发送已存储的结果
            try:
                with Session(engine) as session:
                    analysis = get_analysis(session, paper_id)
            except ValueError:
                yield format_sse(StreamEvent(event="error", data={"message": "Analysis result not found"}))
                return

            # 逐字段发送
            fields_order = [
                "title", "authors", "venue", "year", "one_sentence", "problem",
                "key_contributions", "outline", "method", "experiments",
                "code_mapping", "interview_qa", "limitations", "related_papers",
            ]
            for field_name in fields_order:
                value = getattr(analysis, field_name, None)
                if value is not None:
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

        if paper.status == "failed":
            yield format_sse(StreamEvent(
                event="error",
                data={"message": paper.error_message or "Analysis failed"},
            ))
            return

        # 状态为 pending/extracting/analyzing：订阅后台分析任务
        pdf_path = paper.pdf_path
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

        # 确保后台任务在运行（已运行则复用，断线重连会回放已产生的进度）
        from ..services.analysis_runner import get_job, ensure_job
        job = get_job(paper_id)
        if job is None or job.done:
            job = ensure_job(paper_id, pdf_path)

        queue = job.subscribe()
        try:
            while True:
                event = await queue.get()
                yield format_sse(event)
                if event.event in ("done", "error"):
                    break
        finally:
            # 客户端断开（切换页面/刷新）只取消订阅，后台分析继续运行
            job.unsubscribe(queue)

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
# POST /api/analysis/{paper_id}/start
# ============================================================
@router.post("/{paper_id}/start")
async def start_analysis(paper_id: int) -> dict:
    """在后台启动论文分析（不依赖 SSE 连接）。

    幂等：若任务已在运行则直接返回当前进度。前端可先调用本接口启动，
    再连接 /stream 获取实时进度；中途切换页面也不会中断分析。
    """
    engine = get_engine()
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"detail": f"Paper {paper_id} not found", "code": "NOT_FOUND"},
        )

    if paper.status == "done":
        return {"status": "done", "percent": 100, "message": "已完成"}

    pdf_path = paper.pdf_path
    if not pdf_path:
        from ..models.paper import Paper as PaperModel
        with Session(engine) as session:
            db_paper = session.get(PaperModel, paper_id)
            if db_paper:
                pdf_path = db_paper.pdf_path
    if not pdf_path:
        download_status = getattr(paper, "download_status", None) or "none"
        if download_status == "failed":
            detail = "PDF 下载失败，无法开始解析"
            code = "PDF_DOWNLOAD_FAILED"
        elif download_status in {"unavailable", "pending", "none"}:
            detail = "该论文目前只有元数据，PDF 尚不可用"
            code = "PDF_UNAVAILABLE"
        else:
            detail = "PDF file path not found"
            code = "NO_PDF"
        raise HTTPException(status_code=400, detail={"detail": detail, "code": code})

    from ..services.analysis_runner import get_job, ensure_job
    job = get_job(paper_id)
    if job is None or job.done:
        job = ensure_job(paper_id, pdf_path)

    return {"status": job.status, "percent": job.percent, "message": job.message}


# ============================================================
# GET /api/analysis/{paper_id}/progress
# ============================================================
@router.get("/{paper_id}/progress")
async def get_analysis_progress(paper_id: int) -> dict:
    """查询后台分析进度（无需保持 SSE 连接）。"""
    engine = get_engine()
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"detail": f"Paper {paper_id} not found", "code": "NOT_FOUND"},
        )

    if paper.status == "done":
        return {"status": "done", "percent": 100, "message": "已完成", "running": False}
    if paper.status == "failed":
        return {"status": "failed", "percent": 0, "message": paper.error_message or "分析失败", "running": False}

    from ..services.analysis_runner import get_job
    job = get_job(paper_id)
    if job is None:
        return {"status": paper.status, "percent": 0, "message": "", "running": False}
    return {"status": job.status, "percent": job.percent, "message": job.message, "running": not job.done}


# ============================================================
# POST /api/analysis/{paper_id}/reparse
# ============================================================
@router.post("/{paper_id}/reparse")
async def reparse_paper(paper_id: int) -> PaperResponse:
    """重新解析一篇论文"""
    engine = get_engine()
    try:
        with Session(engine) as session:
            paper = get_paper(session, paper_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"detail": f"Paper {paper_id} not found", "code": "NOT_FOUND"},
        )

    # 显式重新解析：清除断点缓存，强制从头分析
    from ..services.analysis_runner import clear_cache
    clear_cache(paper_id)

    # 重置状态为 pending
    with Session(engine) as session:
        update_paper_status(session, paper_id, "pending", None)

    # 获取更新后的论文信息
    with Session(engine) as session:
        paper = get_paper(session, paper_id)

    return PaperResponse(
        id=paper.id,
        filename=paper.filename,
        title=paper.title,
        venue=paper.venue,
        year=paper.year,
        status=paper.status,
        error_message=paper.error_message,
        code_url=paper.code_url,
        code_path=paper.code_path,
        code_status=paper.code_status or "none",
        created_at=paper.created_at,
        updated_at=paper.updated_at,
    )


# ============================================================
# POST /api/analysis/{paper_id}/related-papers
# ============================================================
@router.post("/{paper_id}/related-papers")
async def refresh_related_papers(paper_id: int) -> list:
    """刷新相关论文（根据设置使用 findpapers 或 Semantic Scholar）"""
    engine = get_engine()

    # 获取已有分析结果
    try:
        with Session(engine) as session:
            analysis = get_analysis(session, paper_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"detail": "分析结果不存在", "code": "NOT_FOUND"})

    paper_title = analysis.title or ""
    if not paper_title:
        raise HTTPException(status_code=400, detail={"detail": "论文标题为空，无法搜索", "code": "INVALID_FORMAT"})

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
                analysis.one_sentence or "",
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
