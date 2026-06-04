"""
FastAPI 应用入口

配置中间件、挂载路由、启动事件。
"""

import os
import shutil
import subprocess
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .config import settings
from .services.database import init_db
from .routers.papers import router as papers_router
from .routers.analysis import router as analysis_router
from .routers.code import router as code_router


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

    # 初始化数据库
    init_db(db_url)
    logger.info("数据库初始化完成")

    logger.info("应用初始化完成，服务就绪")

    yield

    # shutdown (如有需要)


# 创建 FastAPI 实例
app = FastAPI(
    title="Essay Read API",
    description="AI-powered academic paper reading and analysis platform",
    version="1.0.0",
    lifespan=lifespan,
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载路由
app.include_router(papers_router)
app.include_router(analysis_router)
app.include_router(code_router)


@app.get("/")
async def root():
    """健康检查端点"""
    return {"message": "Essay Read API is running", "version": "1.0.0"}


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


@app.websocket("/api/terminal/ws")
async def terminal_websocket(websocket: WebSocket):
    """WebSocket 端点：真实 PTY 终端"""
    await websocket.accept()
    cwd = websocket.query_params.get("cwd") or os.getcwd()
    from .services.terminal import handle_terminal_websocket
    try:
        await handle_terminal_websocket(websocket, cwd)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Terminal error: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@app.post("/api/system/open-vscode")
async def open_vscode(req: dict):
    """打开 VSCode"""
    path = req.get("path", "")
    if not path or not os.path.exists(path):
        return {"error": "路径不存在"}

    # 尝试找到 code 命令
    code_cmd = shutil.which("code")
    if not code_cmd:
        # macOS 常见路径
        mac_path = "/usr/local/bin/code"
        if os.path.exists(mac_path):
            code_cmd = mac_path

    if not code_cmd:
        return {"error": "未找到 VSCode，请确保已安装并将 'code' 命令添加到 PATH"}

    try:
        subprocess.Popen([code_cmd, path], start_new_session=True)
        return {"message": f"已打开 VSCode: {path}"}
    except Exception as e:
        return {"error": f"打开失败: {str(e)}"}


@app.post("/api/system/restart")
async def restart_server():
    """重启后端服务"""
    import sys
    import logging
    logger = logging.getLogger(__name__)
    logger.info("正在重启后端服务...")

    # 延迟重启，让响应先返回
    async def _restart():
        import asyncio
        await asyncio.sleep(0.5)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    import asyncio
    asyncio.create_task(_restart())
    return {"message": "正在重启..."}
