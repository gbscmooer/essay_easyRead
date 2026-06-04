"""模块入口点，允许通过 python -m backend 运行"""

import uvicorn

from .config import settings

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
