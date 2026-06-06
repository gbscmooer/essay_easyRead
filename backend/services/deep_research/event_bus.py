"""
EventBus — 进程内事件总线

用于深度研究 pipeline 的 SSE 事件分发。
仅支持单进程部署（与现有架构一致）。
"""

import asyncio
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
        return self._iter(q)

    async def _iter(self, q):
        while True:
            evt = await q.get()
            if evt is None:
                return
            yield evt

    async def publish(self, evt: StreamEvent):
        for q in self._subscribers:
            await q.put(evt)

    async def close(self):
        self._closed = True
        for q in self._subscribers:
            await q.put(None)


def get_event_bus(run_id: int) -> EventBus:
    if run_id not in _buses:
        _buses[run_id] = EventBus()
    return _buses[run_id]


def drop_event_bus(run_id: int):
    _buses.pop(run_id, None)
