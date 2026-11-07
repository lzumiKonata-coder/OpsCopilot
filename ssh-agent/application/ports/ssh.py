"""应用层端口 - SSH 通信与交互式终端契约"""

from abc import ABC, abstractmethod
from typing import Any, Callable

from domain.entities import Command, ExecutionResult, SSHConnection


class ISSHGateway(ABC):
    """
    远程 SSH 执行网关端口契约 (Driven Port)
    屏蔽底层网络库细节（如 asyncssh），向应用层提供标准执行能力。
    """

    @abstractmethod
    async def execute(self, host: SSHConnection, command: Command) -> ExecutionResult:
        """
        在指定主机资产上异步非阻塞执行命令。
        必须实施超时熔断、输出截断与 ANSI 清洗。
        """
        pass

    @abstractmethod
    async def test_connection(self, host: SSHConnection) -> bool:
        """
        轻量级探测目标主机 SSH 连通性与凭据正确性。
        """
        pass

    async def read_file(
        self,
        host: SSHConnection,
        path: str,
        offset: int = 1,
        limit: int = 200,
    ) -> str:
        """
        通过只读通道直接读取远端文件内容（默认基于 execute 实现，纯净文本输出）
        """
        end_line = offset + limit - 1
        import shlex
        cmd = f"sed -n '{offset},{end_line}p' {shlex.quote(path)}"
        res = await self.execute(host, Command(raw_command=cmd, timeout_seconds=15))
        if res.exit_status != 0:
            raise FileNotFoundError(res.stderr or f"Failed to read file: {path}")
        return res.stdout

    async def write_file(
        self,
        host: SSHConnection,
        path: str,
        content: str,
    ) -> bool:
        """
        通过安全通道向远端主机原子写入文件内容（默认基于 execute + base64 实现）
        """
        import base64
        b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        import shlex
        cmd = f"echo '{b64}' | base64 -d > {shlex.quote(path)}"
        res = await self.execute(host, Command(raw_command=cmd, timeout_seconds=15))
        return res.exit_status == 0

    async def edit_file(
        self,
        host: SSHConnection,
        path: str,
        target: str,
        replacement: str,
    ) -> bool:
        """
        通过安全通道对远端文件内容进行局部单处精确替换（默认基于 read_file + write_file 实现）
        """
        content = await self.read_file(host, path, offset=1, limit=100000)
        if target not in content:
            raise ValueError(f"待替换的目标文本在文件 '{path}' 中未找到，请确认原始内容是否匹配。")
        new_content = content.replace(target, replacement, 1)
        return await self.write_file(host, path, new_content)


class ISSHTerminalGateway(ABC):
    """
    交互式虚拟终端 (PTY / WebSSH) 端口契约
    支持双向长连接字符流与终端窗口尺寸动态重绘 (Window Resize)
    """

    @abstractmethod
    async def open_terminal(
        self,
        host: SSHConnection,
        cols: int = 80,
        rows: int = 24,
    ):
        """开启远程 PTY 交互会话，返回 (connection, process) 句柄元组"""
        pass


class ISSHTerminalSessionManager(ABC):
    """
    交互式 WebSSH PTY 活跃会话管理端口契约
    允许后端业务用例（如 ReActUseCase）直接向工程师当前正在使用的终端会话发送输入并捕获回显，
    实现“左侧实时打字执行，右侧 ReAct 闭环推演”。
    """

    @abstractmethod
    def has_active_terminal(self, identifier: str) -> bool:
        """检查目标主机是否有正在连接的 WebSSH 终端"""
        pass

    @abstractmethod
    def get_recent_buffer(self, identifier: str) -> str | None:
        """获取目标主机活跃终端最近捕获的屏幕回显快照"""
        pass

    @abstractmethod
    async def send_and_capture(
        self,
        identifier: str,
        command: str,
        wait_seconds: float = 1.6,
        idle_timeout: float | None = None,
        max_timeout: float | None = None,
        cancel_check: Callable[[], Any] | None = None,
    ) -> str | None:
        """
        向目标主机的活跃终端输入指令并等待捕获回显文本；若未连接终端则返回 None。
        实现方应在注入前等待终端回到空闲提示符、注入后优先等待提示符回归，
        以避免命令与未完成的输出拼接错位。
        """
        pass
