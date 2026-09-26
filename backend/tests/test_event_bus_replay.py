"""EventBus 重连回放测试。

守住的行为契约：探索/研究运行中刷新页面或切页返回时，新订阅者先收到
历史事件（恢复当前进度与最新产物），再接收后续实时事件——
而不是停在「连接中 8%」直到下一次事件发布。
"""

import asyncio

from backend.models.schemas import StreamEvent
from backend.services.deep_research.event_bus import EventBus


def test_new_subscriber_replays_history_then_receives_live():
    async def run():
        bus = EventBus()
        await bus.publish(StreamEvent(event="stage_start", data={"stage": "recall"}))
        await bus.publish(StreamEvent(event="recall_progress", data={"total_so_far": 12}))

        seen: list[str] = []

        async def consume():
            async for evt in bus.subscribe():
                seen.append(evt.event)
                if len(seen) == 3:
                    break

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0)  # 让订阅者完成注册并取到历史
        await bus.publish(StreamEvent(event="stage_iteration", data={}))
        await asyncio.wait_for(consumer, timeout=2)
        return seen

    assert asyncio.run(run()) == ["stage_start", "recall_progress", "stage_iteration"]


def test_history_keeps_most_recent_events():
    async def run():
        bus = EventBus()
        for i in range(600):  # 超过历史上限，最早的被淘汰
            await bus.publish(StreamEvent(event="tick", data={"i": i}))

        seen: list[int] = []
        async for evt in bus.subscribe():
            seen.append(evt.data["i"])
            if len(seen) >= 500:
                break
        return seen

    assert asyncio.run(run()) == list(range(100, 600))


def test_subscribe_after_close_replays_history_then_ends():
    async def run():
        bus = EventBus()
        await bus.publish(StreamEvent(event="report_progress", data={"done": 5, "total": 32}))
        await bus.publish(StreamEvent(event="done", data={"run_id": 1}))
        await bus.close()
        return [evt.event async for evt in bus.subscribe()]

    assert asyncio.run(run()) == ["report_progress", "done"]


def test_publish_after_close_does_not_crash():
    async def run():
        bus = EventBus()
        await bus.close()
        await bus.publish(StreamEvent(event="late", data={}))
        return [evt.event async for evt in bus.subscribe()]

    assert asyncio.run(run()) == []
