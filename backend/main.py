"""
FastAPI 应用入口

配置中间件、挂载路由、启动事件。
"""

import logging
import os
import shutil
import subprocess
from contextlib import asynccontextmanager
from http import HTTPStatus

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from .config import settings
from .services.terminal_access import (
    TERMINAL_TICKET_TTL_SECONDS,
    origin_matches,
    terminal_ticket_store,
    trusted_local_peer,
)
from .services.database import init_db, reconcile_interrupted_runs
from .routers.papers import router as papers_router
from .routers.analysis import router as analysis_router
from .routers.code import router as code_router
from .routers.workspaces import router as workspaces_router
from .routers.research import router as research_router
from .routers.exploration import router as exploration_router
from .routers.tags import router as tags_router
from .routers.notes import router as notes_router
from .routers.annotations import router as annotations_router
from .routers.agent_tools import router as agent_tools_router


APP_VERSION = "6.2.0"
DEFAULT_RATE_LIMIT = "120/minute"

_STATUS_ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
}


def _default_error_code(status_code: int) -> str:
    return _STATUS_ERROR_CODES.get(status_code, f"HTTP_{status_code}")


def _default_error_detail(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Request failed"


def _error_payload(detail: object, status_code: int) -> dict[str, str]:
    code = _default_error_code(status_code)
    message: object = detail
    if isinstance(detail, dict):
        message = detail.get("detail")
        supplied_code = detail.get("code")
        if isinstance(supplied_code, str) and supplied_code:
            code = supplied_code
    if not isinstance(message, str) or not message:
        message = _default_error_detail(status_code)
    return {"detail": message, "code": code}


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(exc.detail, exc.status_code),
        headers=exc.headers,
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.get("loc", ()) if part != "__root__")
        message = str(first.get("msg") or "Request validation failed")
        detail = f"{location}: {message}" if location else message
    else:
        detail = "Request validation failed"
    return JSONResponse(
        status_code=422,
        content={"detail": detail, "code": "VALIDATION_ERROR"},
    )


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    response = JSONResponse(
        status_code=429,
        content={"detail": "请求过于频繁，请稍后重试", "code": "RATE_LIMITED"},
    )
    return request.app.state.limiter._inject_headers(
        response,
        request.state.view_rate_limit,
    )


def register_http_error_handlers(target_app: FastAPI) -> None:
    target_app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    target_app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    target_app.add_exception_handler(RequestValidationError, validation_exception_handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # startup - 先初始化日志流，这样后续初始化日志都能被捕获
    from .services.log_stream import setup_log_streaming
    setup_log_streaming()

    import logging
    logger = logging.getLogger("startup")
    logger.info("正在初始化应用...")

    # 确保数据目录存在
    db_url = settings.resolved_database_url
    db_dir = os.path.dirname(db_url.replace("sqlite:///", ""))
    os.makedirs(db_dir, exist_ok=True)
    logger.info(f"数据库目录: {db_dir}")

    # 确保上传目录存在
    upload_dir = settings.resolved_upload_dir
    os.makedirs(upload_dir, exist_ok=True)
    logger.info(f"上传目录: {upload_dir}")

    # 确保 ChromaDB 目录存在
    chroma_dir = settings.resolved_chroma_dir
    os.makedirs(chroma_dir, exist_ok=True)
    logger.info(f"ChromaDB 目录: {chroma_dir}")

    # 初始化数据库
    init_db(db_url)
    logger.info("数据库初始化完成")

    # 启动自愈：重置上次被杀时残留的 running 研究任务（否则 UI 永久卡「运行中」）
    try:
        n = reconcile_interrupted_runs()
        if n:
            logger.warning(f"已将 {n} 个中断的研究任务标记为失败（上次后端非正常退出残留）")
    except Exception as e:
        logger.warning(f"重置中断研究任务失败（忽略）: {e}")

    # 存量论文自动索引补跑（全文 + 元数据），后台进行不阻塞启动
    import asyncio
    from .services.auto_indexer import reindex_missing
    asyncio.create_task(reindex_missing())

    logger.info("应用初始化完成，服务就绪")

    yield

    # shutdown (如有需要)


# 创建 FastAPI 实例
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[DEFAULT_RATE_LIMIT],
    headers_enabled=True,
)

app = FastAPI(
    title="Essay Read API",
    description="AI-powered academic paper reading and analysis platform",
    version=APP_VERSION,
    lifespan=lifespan,
)
app.state.limiter = limiter
register_http_error_handlers(app)
app.add_middleware(SlowAPIMiddleware)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# 挂载路由
app.include_router(papers_router)
app.include_router(analysis_router)
app.include_router(code_router)
app.include_router(workspaces_router)
app.include_router(research_router)
app.include_router(exploration_router)
app.include_router(tags_router)
app.include_router(notes_router)
app.include_router(annotations_router)
app.include_router(agent_tools_router)


@app.get("/")
async def root():
    """健康检查端点"""
    return {"message": "Essay Read API is running", "version": APP_VERSION}


@app.get("/api/logs/stream")
async def stream_logs(request: Request):
    """SSE 端点：实时推送后端日志"""
    origin = request.headers.get("origin")
    if not _is_local_request(request) or (origin and not _origin_is_allowed(origin)):
        raise StarletteHTTPException(
            status_code=403,
            detail={"detail": "日志流仅限可信本地访问", "code": "LOCAL_ACCESS_REQUIRED"},
        )
    from .services.log_stream import log_event_generator
    return StreamingResponse(
        log_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _is_localhost(websocket: WebSocket) -> bool:
    host = websocket.client.host if websocket.client else ""
    return trusted_local_peer(host, websocket.headers.get("x-forwarded-for"))


def _is_local_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return trusted_local_peer(host, request.headers.get("x-forwarded-for"))


def _origin_is_allowed(origin: str | None) -> bool:
    """Only the configured frontend may invoke local desktop capabilities."""
    return origin_matches(origin, settings.frontend_origin)


def _is_safe_cwd(path: str) -> bool:
    real = os.path.realpath(path)
    project_root = os.path.realpath(os.path.dirname(os.path.dirname(__file__)))
    try:
        return os.path.commonpath([real, project_root]) == project_root
    except ValueError:
        return False


# Terminal WebSocket connection limit
_terminal_connections: set = set()
_MAX_TERMINAL_CONNECTIONS = 3


@app.post("/api/terminal/session")
async def create_terminal_session(request: Request) -> JSONResponse:
    """Create a short-lived, one-time ticket for a trusted local frontend."""
    if not _is_local_request(request) or not _origin_is_allowed(request.headers.get("origin")):
        return JSONResponse(
            status_code=403,
            content={"detail": "终端访问来源不受信任", "code": "TERMINAL_FORBIDDEN"},
        )
    return JSONResponse(
        {"ticket": terminal_ticket_store.issue(), "expires_in": TERMINAL_TICKET_TTL_SECONDS},
        headers={"Cache-Control": "no-store"},
    )


@app.websocket("/api/terminal/ws")
async def terminal_websocket(websocket: WebSocket):
    """WebSocket 端点：真实 PTY 终端（可信本地前端，最多 3 个并发连接）"""
    if not _is_localhost(websocket) or not _origin_is_allowed(websocket.headers.get("origin")):
        await websocket.close(code=4003, reason="Terminal origin is not allowed")
        return
    accepted_protocol = terminal_ticket_store.consume_protocol(
        websocket.headers.get("sec-websocket-protocol", ""),
    )
    if not accepted_protocol:
        await websocket.close(code=4003, reason="Terminal session is invalid or expired")
        return
    if len(_terminal_connections) >= _MAX_TERMINAL_CONNECTIONS:
        await websocket.close(code=4029, reason="Too many terminal connections")
        return
    await websocket.accept(subprotocol=accepted_protocol)
    _terminal_connections.add(websocket)
    cwd = websocket.query_params.get("cwd") or os.getcwd()
    if not _is_safe_cwd(cwd):
        cwd = os.getcwd()
    from .services.terminal import handle_terminal_websocket
    try:
        await handle_terminal_websocket(websocket, cwd)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"Terminal error: {e}")
    finally:
        _terminal_connections.discard(websocket)
        try:
            await websocket.close()
        except Exception as e:
            logger.debug(f"WebSocket close failed: {e}")


@app.post("/api/system/open-vscode")
async def open_vscode(request: Request, req: dict):
    """打开 VSCode（仅限 localhost）"""
    if not _is_local_request(request) or not _origin_is_allowed(request.headers.get("origin")):
        raise StarletteHTTPException(
            status_code=403,
            detail={"detail": "打开 VSCode 仅限可信本地前端", "code": "LOCAL_ACCESS_REQUIRED"},
        )

    path = req.get("path", "")
    if not path or not os.path.exists(path):
        return {"error": "路径不存在"}

    real_path = os.path.realpath(path)
    project_root = os.path.realpath(os.path.dirname(os.path.dirname(__file__)))
    try:
        if os.path.commonpath([real_path, project_root]) != project_root:
            return {"error": "路径不在项目目录内"}
    except ValueError:
        return {"error": "路径不在项目目录内"}

    code_cmd = shutil.which("code")
    if not code_cmd:
        mac_path = "/usr/local/bin/code"
        if os.path.exists(mac_path):
            code_cmd = mac_path

    if not code_cmd:
        return {"error": "未找到 VSCode，请确保已安装并将 'code' 命令添加到 PATH"}

    try:
        subprocess.Popen([code_cmd, real_path], start_new_session=True)
        return {"message": f"已打开 VSCode: {real_path}"}
    except Exception as e:
        logger.error(f"Failed to open VSCode: {e}")
        return {"error": "打开失败"}
