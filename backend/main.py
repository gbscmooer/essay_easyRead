"""
FastAPI 应用入口

配置中间件、挂载路由、启动事件。
"""

import logging
import os
import shutil
import subprocess
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from .config import settings
from .services.database import init_db, reconcile_interrupted_runs
from .routers.papers import router as papers_router
from .routers.analysis import router as analysis_router
from .routers.code import router as code_router
from .routers.workspaces import router as workspaces_router
from .routers.research import router as research_router
from .routers.tags import router as tags_router
from .routers.notes import router as notes_router
from .routers.annotations import router as annotations_router
from .routers.agent_tools import router as agent_tools_router


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
limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"])

app = FastAPI(
    title="Essay Read API",
    description="AI-powered academic paper reading and analysis platform",
    version="1.8.1",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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
app.include_router(tags_router)
app.include_router(notes_router)
app.include_router(annotations_router)
app.include_router(agent_tools_router)


@app.get("/")
async def root():
    """健康检查端点"""
    return {"message": "Essay Read API is running", "version": "1.8.1"}


@app.get("/api/logs/stream")
async def stream_logs():
    """SSE 端点：实时推送后端日志"""
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
    return host in ("127.0.0.1", "::1", "localhost")


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


@app.websocket("/api/terminal/ws")
async def terminal_websocket(websocket: WebSocket):
    """WebSocket 端点：真实 PTY 终端（仅限 localhost，最多 3 个并发连接）"""
    if not _is_localhost(websocket):
        await websocket.close(code=4003, reason="Terminal access restricted to localhost")
        return
    if len(_terminal_connections) >= _MAX_TERMINAL_CONNECTIONS:
        await websocket.close(code=4029, reason="Too many terminal connections")
        return
    await websocket.accept()
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
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost"):
        return {"error": "仅限本地访问"}

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
