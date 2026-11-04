"""
任务取消管理器 (Task Cancellation Manager)
解耦 HTTP 传输层网络抖动与任务业务取消，支持显式任务取消事件与长指令优雅中断
"""

import asyncio
from typing import Dict, Optional


class TaskCancellationManager:
    """会话与任务级主动取消管理器"""

    def __init__(self) -> None:
        self._cancel_events: Dict[str, asyncio.Event] = {}

    def get_or_create_cancel_event(self, task_key: str) -> asyncio.Event:
        if task_key not in self._cancel_events:
            self._cancel_events[task_key] = asyncio.Event()
        return self._cancel_events[task_key]

    def cancel_task(self, task_key: str) -> bool:
        if task_key in self._cancel_events:
            self._cancel_events[task_key].set()
            return True
        ev = asyncio.Event()
        ev.set()
        self._cancel_events[task_key] = ev
        return True

    def is_cancelled(self, task_key: str) -> bool:
        ev = self._cancel_events.get(task_key)
        return ev.is_set() if ev else False

    def unregister(self, task_key: str) -> None:
        self._cancel_events.pop(task_key, None)
