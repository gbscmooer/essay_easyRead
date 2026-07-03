"""
EventBus — 进程内事件总线

用于深度研究 pipeline 的 SSE 事件分发。
仅支持单进程部署（与现有架构一致）。
"""

import asyncio
from contextlib import suppress
from typing import AsyncIterator
from ...models.schemas import StreamEvent

_buses: dict[int, "EventBus"] = {}


class EventBus:
    def __init__(self):
        self._subscribers: list[asyncio.Queue[StreamEvent | None]] = []
        self._closed = False

    def subscribe(self) -> AsyncIterator[StreamEvent]:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
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
