"""
FastAPI 应用入口

配置中间件、挂载路由、启动事件。
"""

# ============================================================
# 应用配置
# ============================================================
# - 创建 FastAPI 实例，title="Essay Read API"
# - 配置 CORS：允许 frontend_origin（localhost:5173）
# - 挂载两个路由：papers_router, analysis_router
# - startup 事件中调用 init_db() 创建数据库表
# - startup 事件中确保 upload_dir 目录存在

# ============================================================
# 路由挂载
# ============================================================
# app.include_router(papers_router)   # /api/papers/*
# app.include_router(analysis_router) # /api/analysis/*

# ============================================================
# 启动命令
# ============================================================
# uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
