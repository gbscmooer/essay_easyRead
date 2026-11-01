"""进程内运行时：事件总线、后台 worker、协作式暂停旗标。

worker 只在安全点（每次检索 / 每批判定 / 每篇展开之后）观察暂停旗标；路由层看门狗在宽限期后
仍未停下时强制取消 worker，runner 在 CancelledError 收尾里落库 paused。
"""

from __future__ import annotations

import asyncio

from ..event_bus import EventBus

_buses: dict[int, EventBus] = {}
_pause_requests: set[int] = set()
_worker_tasks: dict[int, set[asyncio.Task]] = {}


def get_bus(run_id: int) -> EventBus:
    if run_id not in _buses:
        _buses[run_id] = EventBus()
    return _buses[run_id]


def has_bus(run_id: int) -> bool:
    return run_id in _buses


def drop_bus(run_id: int) -> None:
    _buses.pop(run_id, None)


def start_worker(run_id: int, coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _worker_tasks.setdefault(run_id, set()).add(task)

    def _unregister(done: asyncio.Task) -> None:
        tasks = _worker_tasks.get(run_id)
        if tasks is not None:
            tasks.discard(done)
            if not tasks:
                _worker_tasks.pop(run_id, None)

    task.add_done_callback(_unregister)
    return task


def has_live_worker(run_id: int) -> bool:
    return any(not t.done() for t in _worker_tasks.get(run_id, ()))


def cancel_worker(run_id: int) -> int:
    live = [t for t in _worker_tasks.get(run_id, ()) if not t.done()]
    for t in live:
        t.cancel()
    return len(live)


def request_pause(run_id: int) -> bool:
    """置暂停旗标；返回本进程是否有活跃 worker 能观察到它。"""
    _pause_requests.add(run_id)
    return has_live_worker(run_id)


def pause_requested(run_id: int) -> bool:
    return run_id in _pause_requests


def pause_pending(run_id: int) -> bool:
    return run_id in _pause_requests


def cancel_pause_request(run_id: int) -> None:
    _pause_requests.discard(run_id)
