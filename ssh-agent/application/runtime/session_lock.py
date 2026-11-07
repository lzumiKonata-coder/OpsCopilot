"""
应用层 - 会话运行并发守卫

同一会话（thread_id）在同一时刻只允许一个 ReAct 任务运行，
避免两个请求共享 LangGraph 检查点导致状态互相覆盖。
"""

import logging

logger = logging.getLogger("smartops.application")


class SessionConcurrencyGuard:
    """进程级会话运行互斥守卫（基于事件循环单线程原子性）"""

    def __init__(self):
        self._active: set[str] = set()

    def try_acquire(self, key: str) -> bool:
        """尝试占用会话运行权，已被占用时返回 False"""
        if key in self._active:
            return False
        self._active.add(key)
        return True

    def release(self, key: str) -> None:
        self._active.discard(key)
