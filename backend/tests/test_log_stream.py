import asyncio
import json
import logging
import pytest
from backend.services.log_stream import (
    StreamLogHandler,
    log_event_generator,
    _log_buffer,
    _subscribers,
    _lock,
)


@pytest.mark.asyncio
async def test_log_stream_snapshot_and_no_mutation_error():
    """验证历史日志快照不会因为并发日志写入触发 RuntimeError: deque mutated during iteration"""
    with _lock:
        _log_buffer.clear()

    handler = StreamLogHandler()
    formatter = logging.Formatter("%(levelname)s:\t%(message)s")
    handler.setFormatter(formatter)

    test_logger = logging.getLogger("test_logger")
    test_logger.handlers = [handler]
    test_logger.setLevel(logging.INFO)
    test_logger.propagate = False

    # 预填充多条日志
    for i in range(10):
        test_logger.info(f"Initial log {i}")

    gen = log_event_generator()

    # 接收第一条
    first_item = await anext(gen)
    assert "Initial log 0" in first_item

    # 在消费生成器过程中并发写入新日志（模拟 uvicorn.access 或其他线程打日志）
    for i in range(10, 30):
        test_logger.info(f"Concurrent log {i}")

    # 继续消费剩余快照，绝不应抛出 RuntimeError
    items = [first_item]
    for _ in range(9):
        items.append(await anext(gen))

    assert len(items) == 10
    assert "Initial log 9" in items[-1]

    # 消费新推入的日志
    next_item = await anext(gen)
    assert "Concurrent log 10" in next_item

    # 退出生成器并确保清理
    await gen.aclose()
    with _lock:
        assert len(_subscribers) == 0


@pytest.mark.asyncio
async def test_log_stream_filters_stream_access_logs():
    """验证 /api/logs/stream 自身的访问日志被过滤，避免无限自引用刷屏"""
    with _lock:
        _log_buffer.clear()

    handler = StreamLogHandler()
    formatter = logging.Formatter("%(levelname)s:\t%(message)s")
    handler.setFormatter(formatter)

    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = [handler]
    access_logger.setLevel(logging.INFO)
    access_logger.propagate = False

    access_logger.info('127.0.0.1:1234 - "GET /api/logs/stream HTTP/1.1" 200')
    with _lock:
        assert len(_log_buffer) == 0

    access_logger.info('127.0.0.1:1234 - "GET /api/papers HTTP/1.1" 200')
    with _lock:
        assert len(_log_buffer) == 1
        assert "GET /api/papers" in _log_buffer[0]["message"]
