"""智能运维 ReAct 测试共享 Mock 与网关"""

from typing import List, Optional

from application.ports import ICommandAuditor, ILLMClient, ISSHGateway
from domain.entities import AuditDecision, AuthType, Command, ExecutionResult, RiskLevel, SSHConnection

class MockLLM(ILLMClient):
    def __init__(self, responses: Optional[List[str]] = None):
        self.responses = responses or [
            "Thought: 当前主机疑似根分区磁盘报警，需要先查看各挂载点的容量分布。\nAction: df -h\nStep_Title: 检查挂载点磁盘占用",
            "Thought: 从 df -h 回显可见 /dev/sda1 处于安全水位 45%，问题已查明无溢出风险。\nFinal_Answer: 目标主机物理磁盘空间充裕，无宕机风险，排障完成。",
        ]
        self.call_count = 0

    async def chat(self, messages: list, temperature: float = 0.2) -> str:
        idx = min(self.call_count, len(self.responses) - 1)
        resp = self.responses[idx]
        self.call_count += 1
        return resp

    async def stream_chat(self, messages: list):
        resp = await self.chat(messages)
        yield resp


class MockAdaptiveSSHGateway(ISSHGateway):
    def __init__(self, command_map: dict):
        self.command_map = command_map

    async def execute(self, host: SSHConnection, command: Command) -> ExecutionResult:
        cmd_str = command.raw_command.strip()
        output = self.command_map.get(cmd_str, f"(执行成功: {cmd_str})")
        return ExecutionResult(stdout=output, stderr="", exit_status=0 if "找不到命令" not in output else 127)

    async def test_connection(self, host: SSHConnection) -> bool:
        return True


class RecordingSSHGateway(ISSHGateway):
    """记录所有下发命令的测试网关"""
    def __init__(self):
        self.executed: List[str] = []

    async def execute(self, host: SSHConnection, command: Command) -> ExecutionResult:
        self.executed.append(command.raw_command)
        return ExecutionResult(stdout=f"(执行成功: {command.raw_command})", stderr="", exit_status=0)

    async def test_connection(self, host: SSHConnection) -> bool:
        return True


class CrashingAuditor(ICommandAuditor):
    """模拟自身崩溃的安全审查器"""
    async def audit(self, command: Command) -> AuditDecision:
        raise RuntimeError("AST auditor internal crash")


class ForbiddenAuditor(ICommandAuditor):
    """始终判定 FORBIDDEN 的安全审查器"""
    async def audit(self, command: Command) -> AuditDecision:
        return AuditDecision(risk_level=RiskLevel.FORBIDDEN, hit_rules=["TEST_RULE"], reason="测试规则拦截")


async def _create_react_host(host_repo, name: str, ip: str):
    host = SSHConnection(
        name=name,
        host=ip,
        port=22,
        username="root",
        auth_type=AuthType.PASSWORD,
        password="pwd",
    )
    return await host_repo.save(host)


class RecordingMockLLM(MockLLM):
    """记录每轮发送给模型的完整 messages，用于断言 System Prompt 动态渲染结果"""

    def __init__(self, responses: Optional[List[str]] = None):
        super().__init__(responses=responses)
        self.captured: List[list] = []

    async def chat(self, messages: list, temperature: float = 0.2) -> str:
        self.captured.append(messages)
        return await super().chat(messages, temperature)


class RootPromptSSHGateway(ISSHGateway):
    """回显末尾携带 root 提示符的测试网关（模拟真实 PTY 回显特征）"""

    def __init__(self):
        self.executed: List[str] = []

    async def execute(self, host: SSHConnection, command: Command) -> ExecutionResult:
        self.executed.append(command.raw_command)
        return ExecutionResult(
            stdout=f"{command.raw_command}\r\n[root@hadoop100 ~]# ",
            stderr="",
            exit_status=0,
        )

    async def test_connection(self, host: SSHConnection) -> bool:
        return True

class PermissionDeniedSSHGateway(ISSHGateway):
    """所有指令均回显权限不足的测试网关"""

    async def execute(self, host: SSHConnection, command: Command) -> ExecutionResult:
        return ExecutionResult(stdout="", stderr="sudo: ops: 权限不够", exit_status=1)

    async def test_connection(self, host: SSHConnection) -> bool:
        return True


class FakePtyProcess:
    """模拟 asyncssh PTY 进程：stdin.write 时同步回吐命令回显 chunk"""

    def __init__(self, manager, identifier: str, response_fn):
        self.stdin = self
        self._manager = manager
        self._identifier = identifier
        self._response_fn = response_fn
        self.written: List[str] = []

    def write(self, data: str) -> None:
        self.written.append(data)
        raw = self._response_fn(data)
        if raw:
            self._manager.on_stdout_chunk(self._identifier, raw)