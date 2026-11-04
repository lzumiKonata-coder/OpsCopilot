"""应用层 - ReAct 引擎 PTY 命令执行"""

import asyncio
import logging
import re
from typing import Any, Callable, Optional

from domain.entities import Command, SSHConnection

logger = logging.getLogger("smartops.application.react.engine")


class PTYExecMixin:
    """在受管机 PTY 中执行命令"""

    async def _execute_command_in_pty(
        self,
        host: SSHConnection,
        command: str,
        wait_seconds: float = 1.6,
        max_timeout: Optional[float] = None,
        cancel_check: Optional[Callable[[], Any]] = None,
    ) -> str:
        """在受管机 PTY 中执行命令，优先通过前端活跃终端管理器注入，兜底走物理连接执行"""
        if self.terminal_manager and self.terminal_manager.has_active_terminal(host.connection_id):
            captured = await self.terminal_manager.send_and_capture(
                identifier=host.connection_id,
                command=command,
                wait_seconds=wait_seconds,
                max_timeout=max_timeout,
                cancel_check=cancel_check,
            )
            if captured is not None:
                return captured

        if self.ssh_gateway:
            # 区分长耗时指令（如安装、下载、编译、解包）与普通检测指令，避免非 PTY 模式下被 30 秒盲目截断
            is_long_running = bool(re.search(
                r"\b(yum|dnf|apt|apt-get|docker\s+(?:pull|build|run)|git\s+clone|wget|curl|pip|npm|pnpm|yarn|cargo|make|tar)\b",
                command,
                re.IGNORECASE,
            ))
            base_timeout = 300 if is_long_running else max(int(wait_seconds or 60), 60)
            if max_timeout is not None:
                fallback_timeout = max(1, min(base_timeout, int(max_timeout)))
            else:
                fallback_timeout = base_timeout

            async def _check_cancellation() -> bool:
                if not cancel_check:
                    return False
                try:
                    res = cancel_check()
                    if asyncio.iscoroutine(res):
                        return bool(await res)
                    return bool(res)
                except Exception:
                    return False

            if cancel_check is None:
                res = await self.ssh_gateway.execute(
                    host=host,
                    command=Command(raw_command=command, timeout_seconds=fallback_timeout),
                )
            else:
                exec_task = asyncio.create_task(
                    self.ssh_gateway.execute(
                        host=host,
                        command=Command(raw_command=command, timeout_seconds=fallback_timeout),
                    )
                )

                async def _watch_cancel():
                    while True:
                        if await _check_cancellation():
                            return True
                        await asyncio.sleep(0.2)

                watch_task = asyncio.create_task(_watch_cancel())
                done, pending = await asyncio.wait([exec_task, watch_task], return_when=asyncio.FIRST_COMPLETED)
                for p in pending:
                    p.cancel()
                    try:
                        await p
                    except (asyncio.CancelledError, Exception):
                        pass

                if watch_task in done:
                    logger.info("Non-PTY execution of command '%s' cancelled by user", command[:40])
                    raise asyncio.CancelledError("Command execution cancelled by user")

                res = exec_task.result()

            out = res.stdout
            if res.stderr:
                out = (out + "\n" + res.stderr).strip() if out else res.stderr
            return out if out else "(命令执行完成，无标准输出回显)"

        return "(终端执行完成)"
