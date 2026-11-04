"""
适配层 - 运行期事件缓冲与订阅 (Run Event Broker)
支持浏览器刷新/网络断线后重新接续正在后台运行的排障任务：
1. 按 task_key 缓冲运行期 SSE 事件，供刷新后的客户端回放；
2. 已结束任务保留一段时间，便于客户端拉取收尾事件；
3. 内存占用有上限，避免长时间任务无限堆积事件。
"""

import asyncio
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

MAX_BUFFERED_EVENTS = 2000
FINISHED_RETENTION_SECONDS = 120.0


class RunEventBroker:
    """单个排障任务的事件总线（缓冲 + 多订阅者扇出）"""

    def __init__(self, key: str):
        self.key = key
        self._events: List[Dict[str, Any]] = []
        self._subscribers: set[asyncio.Queue] = set()
        self._finished = False
        self.finished_at: Optional[float] = None

    @property
    def finished(self) -> bool:
        return self._finished

    def publish(self, event: Dict[str, Any]) -> None:
        """缓冲事件并广播给所有在线订阅者"""
        if self._finished:
            return
        self._events.append(event)
        if len(self._events) > MAX_BUFFERED_EVENTS:
            self._events = self._events[-MAX_BUFFERED_EVENTS:]
        for queue in list(self._subscribers):
            queue.put_nowait(event)

    def finish(self) -> None:
        """任务结束：通知订阅者收尾，并保留缓冲供短时间内的重连回放"""
        if self._finished:
            return
        self._finished = True
        self.finished_at = time.time()
        for queue in list(self._subscribers):
            queue.put_nowait(None)
        self._subscribers.clear()

    async def subscribe(self, after: int = 0) -> AsyncGenerator[Dict[str, Any], None]:
        """回放 after 之后的历史事件，随后持续推送新事件，直至任务结束"""
        queue: asyncio.Queue = asyncio.Queue()
        start = max(0, after)
        if self._finished:
            # 任务已结束：仅回放缓冲，不再等待新事件
            for event in list(self._events[start:]):
                yield event
            return

        # 注册订阅者与快照之间无 await，保证不丢事件也不会重复推送
        self._subscribers.add(queue)
        snapshot = list(self._events[start:])
        try:
            for event in snapshot:
                yield event
            while True:
                item = await queue.get()
                if item is None:
                    return
                yield item
        finally:
            self._subscribers.discard(queue)


class RunEventBrokerRegistry:
    """运行期事件总线注册表（按 task_key 索引）"""

    def __init__(self):
        self._brokers: Dict[str, RunEventBroker] = {}

    def create(self, key: str) -> RunEventBroker:
        self._prune()
        broker = RunEventBroker(key)
        self._brokers[key] = broker
        return broker

    def get(self, key: str) -> Optional[RunEventBroker]:
        self._prune()
        return self._brokers.get(key)

    def is_running(self, key: str) -> bool:
        broker = self.get(key)
        return bool(broker and not broker.finished)

    def _prune(self) -> None:
        now = time.time()
        for key, broker in list(self._brokers.items()):
            if broker.finished and broker.finished_at and now - broker.finished_at > FINISHED_RETENTION_SECONDS:
                self._brokers.pop(key, None)
