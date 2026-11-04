"""
适配层 - SSH 驱动执行网关 (AsyncSSH Gateway)
实现 application.ports 定义的 ISSHGateway。
底层采用 asyncssh 库，具备异步非阻塞连接复用、ANSI 控制符清洗、
输出字节流截断保护 (默认 8KB) 与硬超时熔断 (asyncio.wait_for) 能力。
"""

import asyncio
import os
import logging
import re
import time
from typing import Tuple

import asyncssh

from application.ports import ISSHGateway, ISSHTerminalGateway
from domain.entities import Command, ExecutionResult, SSHConnection
from domain.rules import AuthType

logger = logging.getLogger("smartops.ssh")


class AsyncSSHGateway(ISSHGateway, ISSHTerminalGateway):
    """
    基于 asyncssh 的生产级 SSH 网关实现
    """

    # 正则用于彻底剥除 VT100 / ANSI 转义序列、终端色彩及光标移动字符
    ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

    @classmethod
    def strip_ansi(cls, text: str) -> str:
        """剥离 ANSI 彩色与终端控制符，保障审计数据整洁"""
        if not text:
            return ""
        return cls.ANSI_ESCAPE_RE.sub("", text)

    def _prepare_connection_options(self, host: SSHConnection) -> dict:
        """组装 asyncssh 客户端连接参数与认证凭据"""
        known_hosts_val = None
        cfg = host.config or SSHConnectionConfig()
        if cfg.strict_host_key_check == 0:
            known_hosts_val = None
        elif cfg.known_hosts:
            known_hosts_val = cfg.known_hosts
        else:
            system_known_hosts = os.path.expanduser("~/.ssh/known_hosts")
            if os.path.exists(system_known_hosts):
                known_hosts_val = system_known_hosts
            else:
                logger.warning(
                    "strict_host_key_check is enabled for %s but no known_hosts file configured or found at %s; falling back to None",
                    host.connection_name,
                    system_known_hosts,
                )
                known_hosts_val = None

        conn_kwargs = {
            "host": host.host,
            "port": host.port,
            "username": host.username,
            "known_hosts": known_hosts_val,
            "login_timeout": host.config.connect_timeout,
        }

        if host.auth_type == AuthType.PASSWORD:
            conn_kwargs["password"] = host.password
        elif host.auth_type == AuthType.PRIVATE_KEY:
            if host.private_key:
                try:
                    imported_key = asyncssh.import_private_key(
                        host.private_key,
                        passphrase=host.passphrase,
                    )
                    conn_kwargs["client_keys"] = [imported_key]
                except Exception as e:
                    logger.error("Failed to parse private key for host %s: %s", host.connection_name, str(e))
                    raise ValueError(f"Invalid private key format: {e}") from e

        return conn_kwargs

    async def execute(self, host: SSHConnection, command: Command) -> ExecutionResult:
        """
        异步非阻塞远程命令执行，附带：
        1. 严格超时熔断机制 (asyncio.wait_for)
        2. 内存安全截断保护 (基于 max_bytes 的流式保护)
        3. 纯净文本 ANSI 过滤清洗
        """
        start_time = time.perf_counter()
        conn_kwargs = self._prepare_connection_options(host)

        try:
            # 建立 SSH 物理连接 (带握手超时)
            async with asyncssh.connect(**conn_kwargs) as conn:
                # 开启远程子进程并执行受控命令
                result = await asyncio.wait_for(
                    self._run_process_streamed(conn, command),
                    timeout=command.timeout_seconds,
                )
                duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                return ExecutionResult(
                    exit_status=result.exit_status,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    is_truncated=result.is_truncated,
                    duration_ms=duration_ms,
                )

        except asyncio.TimeoutError:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.error("Command execution timed out after %ds on host %s", command.timeout_seconds, host.host)
            return ExecutionResult(
                exit_status=124,  # 标准 UNIX timeout 退出码
                stdout="",
                stderr=f"[SmartOps Timeout] Execution exceeded hard timeout limit of {command.timeout_seconds} seconds.",
                is_truncated=False,
                duration_ms=duration_ms,
            )

        except (asyncssh.Error, OSError) as ssh_err:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.error("SSH transport failure on host %s (%s): %s", host.connection_name, host.host, str(ssh_err))
            return ExecutionResult(
                exit_status=255,  # SSH 连接级典型失败返回码
                stdout="",
                stderr=f"[SmartOps SSH Error] Connection or authentication failed: {str(ssh_err)}",
                is_truncated=False,
                duration_ms=duration_ms,
            )

        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.exception("Unexpected error executing command on %s: %s", host.host, str(exc))
            return ExecutionResult(
                exit_status=1,
                stdout="",
                stderr=f"[SmartOps Internal Error] {str(exc)}",
                is_truncated=False,
                duration_ms=duration_ms,
            )

    async def _run_process_streamed(
        self,
        conn: asyncssh.SSHClientConnection,
        command: Command,
    ) -> ExecutionResult:
        """
        流式消费输出并执行截断保护，防止远程海量回显冲垮服务内存
        """
        process = await conn.create_process(
            command.raw_command,
            encoding="utf-8",
            errors="replace",
        )

        max_bytes = command.max_bytes

        async def _read_stream(reader) -> Tuple[str, bool]:
            chunks = []
            collected_len = 0
            truncated = False
            while True:
                chunk = await reader.read(2048)
                if not chunk:
                    break
                chunk_bytes = chunk.encode("utf-8", errors="replace")
                remaining = max_bytes - collected_len
                if len(chunk_bytes) > remaining:
                    chunks.append(chunk_bytes[:remaining].decode("utf-8", errors="replace"))
                    truncated = True
                    break
                chunks.append(chunk)
                collected_len += len(chunk_bytes)
                if collected_len >= max_bytes:
                    truncated = True
                    break
            return "".join(chunks), truncated

        # 并发读取标准输出与错误输出
        stdout_task = asyncio.create_task(_read_stream(process.stdout))
        stderr_task = asyncio.create_task(_read_stream(process.stderr))

        stdout_raw, stdout_truncated = await stdout_task
        stderr_raw, stderr_truncated = await stderr_task

        is_truncated = stdout_truncated or stderr_truncated
        if is_truncated:
            # 超出配额限制时立即终止进程，防止远端持续空转
            try:
                process.terminate()
            except Exception:
                pass

        await process.wait()
        exit_status = process.exit_status if process.exit_status is not None else -1

        # ANSI 清洗
        stdout_clean = self.strip_ansi(stdout_raw)
        stderr_clean = self.strip_ansi(stderr_raw)

        if is_truncated:
            stdout_clean += f"\n[SmartOps Warning: Output truncated as it exceeded {max_bytes} bytes limit]"

        return ExecutionResult(
            exit_status=exit_status,
            stdout=stdout_clean,
            stderr=stderr_clean,
            is_truncated=is_truncated,
            duration_ms=0.0,
        )

    async def test_connection(self, host: SSHConnection) -> bool:
        """快速连接与鉴权探活"""
        conn_kwargs = self._prepare_connection_options(host)
        try:
            async with asyncssh.connect(**conn_kwargs):
                return True
        except Exception as e:
            logger.warning("Connection test failed for %s (%s): %s", host.connection_name, host.host, str(e))
            return False

    async def read_file(
        self,
        host: SSHConnection,
        path: str,
        offset: int = 1,
        limit: int = 200,
    ) -> str:
        """优先使用 SFTP 只读通道获取纯净文本内容，异常时平滑降级至标准命令读取"""
        conn_kwargs = self._prepare_connection_options(host)
        try:
            async with asyncssh.connect(**conn_kwargs) as conn:
                try:
                    async with conn.start_sftp_client() as sftp:
                        async with sftp.open(path, "r", encoding="utf-8", errors="replace") as f:
                            lines = []
                            current_line = 0
                            end_line = offset + limit - 1
                            async for line in f:
                                current_line += 1
                                if current_line < offset:
                                    continue
                                if current_line > end_line:
                                    break
                                lines.append(line)
                            return "".join(lines)
                except Exception as sftp_err:
                    logger.debug("SFTP read_file fallback on %s: %s", host.host, sftp_err)
        except Exception as conn_err:
            logger.debug("SSH connect error during SFTP read_file on %s: %s", host.host, conn_err)

        return await super().read_file(host, path, offset=offset, limit=limit)

    async def write_file(
        self,
        host: SSHConnection,
        path: str,
        content: str,
    ) -> bool:
        """优先使用 SFTP 安全写入远端文件，异常时降级至 base64 原子写入"""
        conn_kwargs = self._prepare_connection_options(host)
        try:
            async with asyncssh.connect(**conn_kwargs) as conn:
                try:
                    async with conn.start_sftp_client() as sftp:
                        async with sftp.open(path, "w", encoding="utf-8") as f:
                            await f.write(content)
                        return True
                except Exception as sftp_err:
                    logger.debug("SFTP write_file fallback on %s: %s", host.host, sftp_err)
        except Exception as conn_err:
            logger.debug("SSH connect error during SFTP write_file on %s: %s", host.host, conn_err)

        return await super().write_file(host, path, content)

    async def open_terminal(
        self,
        host: SSHConnection,
        cols: int = 80,
        rows: int = 24,
    ) -> Tuple[asyncssh.SSHClientConnection, asyncssh.SSHClientProcess]:
        """开启远程 PTY 交互会话，分配 xterm-256color 终端"""
        conn_kwargs = self._prepare_connection_options(host)
        conn = await asyncssh.connect(**conn_kwargs)
        try:
            process = await conn.create_process(
                command=None,
                term_type="xterm-256color",
                term_size=(cols, rows),
                encoding="utf-8",
                errors="replace",
            )
            return conn, process
        except Exception:
            conn.close()
            raise
