"""模块入口点，允许通过 python -m backend 运行"""

import uvicorn

from .config import settings

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.dev,
        access_log=False,  # 由 StreamLogHandler 通过 SSE 推送，无需终端重复输出
        log_level="info",
    )
