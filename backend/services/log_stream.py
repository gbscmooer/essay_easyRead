"""
日志流服务

捕获后端日志并提供 SSE 流式推送。
"""

import asyncio
import json
import logging
import threading
import time
from collections import deque
from typing import AsyncGenerator

# 环形缓冲区，存储最近 500 条日志
_log_buffer: deque[dict] = deque(maxlen=500)
_subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict]]] = []
_lock = threading.Lock()


def _safe_put(q: asyncio.Queue, item: dict) -> None:
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        pass


class StreamLogHandler(logging.Handler):
    """将日志写入环形缓冲区并通知订阅者"""

    def emit(self, record: logging.LogRecord):
        try:
            # 过滤 /api/logs/stream 自身的请求日志，避免 SSE 连接日志无限刷屏
            if record.name == "uvicorn.access" and "/api/logs/stream" in record.getMessage():
                return

            entry = {
                "time": time.strftime("%H:%M:%S", time.localtime(record.created)),
                "level": record.levelname,
                "source": record.name.split(".")[-1] or "app",
                "message": self.format(record),
            }
            with _lock:
                _log_buffer.append(entry)
                for loop, q in _subscribers:
                    try:
                        if not loop.is_closed():
                            loop.call_soon_threadsafe(_safe_put, q, entry)
                    except Exception:
                        pass
        except Exception:
            # 不能在 logging handler 内使用 logger（会无限递归），
            # 使用标准框架方法处理异常
            self.handleError(record)


def setup_log_streaming():
    """初始化日志流：添加 handler 到 root logger 和 uvicorn loggers"""
    # 使用 uvicorn 风格的格式
    formatter = logging.Formatter("%(levelname)s:\t%(message)s")
    handler = StreamLogHandler()
    handler.setFormatter(formatter)
    handler.setLevel(logging.INFO)

    # 添加到 root logger
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)

    # 捕获 uvicorn 的 access log 和 error log
    for logger_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        # 阻止向 root 传播，避免日志被 root handler 再次输出（重叠问题）
        logger.propagate = False

    # 降低 httpx / httpcore 日志级别：屏蔽 routine HTTP 请求 INFO 日志，
    # 只保留 WARNING 及以上（如连接失败等），消除 429 时的 httpx + app 双重输出
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def log_event_generator() -> AsyncGenerator[str, None]:
    """SSE 事件生成器：推送日志条目"""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)
    sub = (loop, queue)

    with _lock:
        # 获取历史日志快照，避免 yield 导致并发修改 deque 触发 RuntimeError
        history = list(_log_buffer)
        _subscribers.append(sub)

    try:
        # 先发送缓冲区中的历史日志
        for entry in history:
            yield f"data: {json.dumps(entry)}\n\n"

        # 持续推送新日志
        while True:
            try:
                entry = await asyncio.wait_for(queue.get(), timeout=30)
                yield f"data: {json.dumps(entry)}\n\n"
            except asyncio.TimeoutError:
                # 发送心跳保持连接
                yield ": heartbeat\n\n"
    except asyncio.CancelledError:
        # 客户端断开连接
        pass
    finally:
        with _lock:
            if sub in _subscribers:
                _subscribers.remove(sub)
