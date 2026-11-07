"""
应用层 - 远端主机文件写操作排队锁 (Remote File Mutation Queue)
灵感源自 Pi 的 withFileMutationQueue 机制。
针对相同 (host_id, path) 的写操作实施原子串行化排队，
不同文件或不同主机的写操作保持完全并发。
"""

import asyncio
from typing import Any, Callable


class RemoteFileMutationQueue:
    """
    远端文件变更并发安全排队锁
    """

    def __init__(self):
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    async def get_lock(self, host_id: str, remote_path: str) -> asyncio.Lock:
        norm_path = (remote_path or "").strip().replace("\\", "/")
        key = (host_id, norm_path)
        async with self._global_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    async def run_with_lock(
        self,
        host_id: str,
        remote_path: str,
        coro_fn: Callable[..., Any],
        *args,
        **kwargs,
    ) -> Any:
        """
        在指定目标主机与文件路径排队锁保护下执行写操作
        """
        lock = await self.get_lock(host_id, remote_path)
        async with lock:
            return await coro_fn(*args, **kwargs)
