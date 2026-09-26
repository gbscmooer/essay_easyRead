"""
EventBus — 进程内事件总线

用于深度研究 pipeline 的 SSE 事件分发。
仅支持单进程部署（与现有架构一致）。
"""

import asyncio
from collections import deque
from contextlib import suppress
from typing import AsyncIterator
from ...models.schemas import StreamEvent

_buses: dict[int, "EventBus"] = {}

# 重连回放的历史上限：报告逐篇提取等高频小事件可到数百条，
# 只需覆盖最近一段即可重建当前进度；更早的事件由 DB result 兜底。
_HISTORY_LIMIT = 500


class EventBus:
    def __init__(self):
        self._subscribers: list[asyncio.Queue[StreamEvent | None]] = []
        self._history: deque[StreamEvent] = deque(maxlen=_HISTORY_LIMIT)
        self._closed = False

    def subscribe(self) -> AsyncIterator[StreamEvent]:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        # 重连（刷新页面/切页返回）时先回放历史事件：新订阅者能立即恢复到
        # 当前进度与最新产物，而不是停在「连接中」等待下一次发布。
        for evt in self._history:
            q.put_nowait(evt)
        if self._closed:
            q.put_nowait(None)
        return self._iter(q)

    async def _iter(self, q):
        try:
            while True:
                evt = await q.get()
                if evt is None:
                    return
                yield evt
        finally:
            with suppress(ValueError):
                self._subscribers.remove(q)

    async def publish(self, evt: StreamEvent):
        if self._closed:
            return
        self._history.append(evt)
        for q in list(self._subscribers):
            await q.put(evt)

    async def close(self):
        self._closed = True
        for q in list(self._subscribers):
            await q.put(None)


def get_event_bus(run_id: int) -> EventBus:
    if run_id not in _buses:
        _buses[run_id] = EventBus()
    return _buses[run_id]


def has_event_bus(run_id: int) -> bool:
    """是否存在该 run 的活跃事件总线（即 pipeline 仍在本进程内运行）。

    用于识别"孤儿 run"：DB 状态仍是 running/pending，但 pipeline 进程
    已不在（后端重启 / 硬崩溃）。这类 run 没有活 bus，订阅会永久阻塞。
    """
    return run_id in _buses


def drop_event_bus(run_id: int):
    _buses.pop(run_id, None)
