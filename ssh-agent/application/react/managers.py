"""应用层 - 人机协同审批与即时插话管理器"""

import asyncio
from abc import ABC, abstractmethod
import logging
import time
from typing import Any, Callable
import uuid

from application.react.cancel_context import get_cancel_check

logger = logging.getLogger("smartops.application.react.managers")


class IApprovalManager(ABC):
    """
    人机协同审批管理器端口契约
    管理高危指令审批会话的挂起、异步决断与恢复
    """

    @abstractmethod
    def create_approval_request(
        self,
        session_id: str,
        step_idx: int,
        command: str,
        reason: str,
    ) -> str:
        """注册待审批高危指令，返回全局唯一 approval_id"""
        pass

    @abstractmethod
    async def wait_for_decision(
        self,
        approval_id: str,
        timeout: float = 120.0,
        cancel_check: Callable[[], Any] | None = None,
    ) -> bool | None:
        """异步等待用户审批裁定结果 (True: 允许, False: 拒绝, None: 超时)"""
        pass

    @abstractmethod
    def submit_decision(
        self,
        approval_id: str,
        approved: bool,
        scope: str = "once",
    ) -> bool:
        """提交用户审批决断 (scope: once 仅本次, session 当前会话放行同类指令)"""
        pass

    @abstractmethod
    def is_session_approved(self, session_id: str, command: str) -> bool:
        """查询指定会话是否已放行同类或该指令"""
        pass


class InMemoryApprovalManager(IApprovalManager):
    """内存级人机协同审批管理器实现（线程与协程安全）"""

    def __init__(self):
        self._pending: dict[str, dict[str, Any]] = {}
        self._session_approved: dict[str, set[str]] = {}

    def create_approval_request(
        self,
        session_id: str,
        step_idx: int,
        command: str,
        reason: str,
    ) -> str:
        approval_id = f"appr-{uuid.uuid4().hex[:12]}"
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending[approval_id] = {
            "session_id": session_id,
            "step_idx": step_idx,
            "command": command,
            "reason": reason,
            "future": future,
            "created_at": time.time(),
        }
        return approval_id

    async def wait_for_decision(
        self,
        approval_id: str,
        timeout: float = 120.0,
        cancel_check: Callable[[], Any] | None = None,
    ) -> bool | None:
        entry = self._pending.get(approval_id)
        if not entry:
            return None
        future: asyncio.Future = entry["future"]
        start = time.time()
        try:
            while not future.done():
                remaining = timeout - (time.time() - start)
                if remaining <= 0:
                    logger.warning("Approval request '%s' timed out after %.1fs", approval_id, timeout)
                    return None
                eff_cancel_check = cancel_check or get_cancel_check()
                if eff_cancel_check and await eff_cancel_check():
                    logger.info("Approval request '%s' aborted due to client cancellation", approval_id)
                    raise asyncio.CancelledError("客户端已主动取消排障流程")
                wait_slice = min(remaining, 0.5)
                try:
                    return await asyncio.wait_for(asyncio.shield(future), timeout=wait_slice)
                except (asyncio.TimeoutError, TimeoutError):
                    continue
            return future.result()
        finally:
            self._pending.pop(approval_id, None)

    def submit_decision(
        self,
        approval_id: str,
        approved: bool,
        scope: str = "once",
    ) -> bool:
        entry = self._pending.get(approval_id)
        if not entry:
            return False
        if scope == "session" and approved:
            sid = entry["session_id"]
            if sid not in self._session_approved:
                self._session_approved[sid] = set()
            cmd = entry.get("command", "").strip()
            if cmd:
                # 仅将用户审批的具体命令精确加入当前会话白名单，严禁将 base_cmd (如 kill, rm) 全局泛化免审
                self._session_approved[sid].add(cmd)
        future: asyncio.Future = entry["future"]
        if not future.done():
            future.set_result(approved)
            return True
        return False

    def is_session_approved(self, session_id: str, command: str) -> bool:
        if not session_id or session_id not in self._session_approved:
            return False
        clean_cmd = (command or "").strip()
        return clean_cmd in self._session_approved[session_id]


class ISteeringManager(ABC):
    """
    即时插话与转向管理器契约 (Steering Message Manager)
    允许用户在排障推演中途注入方向纠偏指令，在下一个思考轮次动态调整策略
    """

    @abstractmethod
    def push_steering_message(self, session_id: str, message: str) -> None:
        """向指定排障会话推送即时插话消息"""
        pass

    @abstractmethod
    def pop_steering_messages(self, session_id: str) -> list[str]:
        """提取并清空指定排障会话累积的插话消息列表"""
        pass


class InMemorySteeringManager(ISteeringManager):
    """内存级即时插话管理器实现（线程与协程安全）"""

    def __init__(self):
        self._queues: dict[str, list[str]] = {}

    def push_steering_message(self, session_id: str, message: str) -> None:
        clean = (message or "").strip()
        if clean and session_id:
            self._queues.setdefault(session_id, []).append(clean)

    def pop_steering_messages(self, session_id: str) -> list[str]:
        if not session_id or session_id not in self._queues:
            return []
        return self._queues.pop(session_id, [])
