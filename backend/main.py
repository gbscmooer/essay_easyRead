"""
FastAPI 应用入口

配置中间件、挂载路由、启动事件。
"""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .services.database import init_db
from .routers.papers import router as papers_router
from .routers.analysis import router as analysis_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # startup
    # 确保数据目录存在
    os.makedirs(os.path.dirname(settings.database_url.replace("sqlite:///", "")), exist_ok=True)
    # 确保上传目录存在
    os.makedirs(settings.upload_dir, exist_ok=True)
    # 初始化数据库
    init_db(settings.database_url)

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


@app.get("/")
async def root():
    """健康检查端点"""
    return {"message": "Essay Read API is running", "version": "1.0.0"}
