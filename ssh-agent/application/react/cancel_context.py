"""应用层 - ReAct 运行期取消信号上下文（并发安全）"""

import contextvars
from typing import Any, Callable, Optional

CancelCheck = Callable[[], Any]

_current_cancel_check: contextvars.ContextVar[Optional[CancelCheck]] = contextvars.ContextVar(
    "react_cancel_check",
    default=None,
)


def set_cancel_check(cancel_check: Optional[CancelCheck]):
    """为当前异步任务绑定取消检查回调"""
    return _current_cancel_check.set(cancel_check)


def reset_cancel_check(token) -> None:
    """恢复当前任务此前的取消检查上下文"""
    _current_cancel_check.reset(token)


def get_cancel_check() -> Optional[CancelCheck]:
    """读取当前异步任务的取消检查回调"""
    return _current_cancel_check.get()
