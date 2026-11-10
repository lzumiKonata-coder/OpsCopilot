"""
原生 Tool Calling 协议与钩子拦截测试
验证 ToolCall、LLMResponse、原生工具定义及 ReAct 状态机工具调用分发
"""

from typing import Any, AsyncGenerator
import pytest

from adapter.repository.db_engine import get_session_factory
from adapter.repository import (
    SQLAlchemyChatRepository,
    SQLAlchemySSHConnectionRepository,
)
from application.ports import (
    ICommandAuditor,
    ILLMClient,
    ISSHGateway,
    LLMResponse,
    ToolCall,
)
from adapter.tools.builtin import build_default_registry
from application.usecases.react import ReActUseCase
from domain.entities import (
    AuditDecision,
    AuthType,
    Command,
    ExecutionResult,
    RiskLevel,
    SSHConnection,
)


class ToolCallingMockLLM(ILLMClient):
    """用于测试的原生 Tool Calling Mock 驱动"""

    def __init__(self, responses: list[LLMResponse]):
        self.responses = responses
        self.call_count = 0
        self.received_tools: list[list[dict[str, Any]] | None] = []

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.2,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[str, None]:
        res = await self.chat(messages, temperature=temperature, tools=tools)
        yield str(res)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.2,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        self.received_tools.append(tools)
        idx = min(self.call_count, len(self.responses) - 1)
        resp = self.responses[idx]
        self.call_count += 1
        return resp


class FakeAudit(ICommandAuditor):
    """自定义安全审查器"""

    def __init__(self, block_command: str | None = None):
        self.block_command = block_command

    async def audit(self, command: Command) -> AuditDecision:
        if self.block_command and self.block_command in command.raw_command:
            return AuditDecision(
                risk_level=RiskLevel.FORBIDDEN,
                hit_rules=["FORBIDDEN_RULE"],
                reason=f"测试安全规则阻断: {command.raw_command}",
            )
        return AuditDecision(risk_level=RiskLevel.SAFE, reason="安全指令")


class FakeSSHGateway(ISSHGateway):
    async def execute(self, host: SSHConnection, command: Command) -> ExecutionResult:
        if "uptime" in command.raw_command:
            return ExecutionResult(stdout=" 20:55:00 up 100 days, 1 user, load average: 0.05, 0.02, 0.00\n", exit_status=0)
        return ExecutionResult(stdout="OK\n", exit_status=0)

    async def test_connection(self, host: SSHConnection) -> bool:
        return True


def test_llm_response_and_tool_call_types():
    """验证 LLMResponse 继承自 str 且携带结构化 ToolCall"""
    tc = ToolCall(id="call_1", name="execute_ssh_command", arguments={"command": "df -h", "step_title": "检查磁盘"})
    resp = LLMResponse(content="正在分析系统磁盘水位", tool_calls=[tc])

    # 验证向后兼容为字符串
    assert isinstance(resp, str)
    assert resp == "正在分析系统磁盘水位"
    assert "磁盘水位" in resp

    # 验证携带结构化属性
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "execute_ssh_command"
    assert resp.tool_calls[0].arguments["command"] == "df -h"
    assert resp.tool_calls[0].arguments["step_title"] == "检查磁盘"


@pytest.mark.asyncio
async def test_react_engine_with_native_tool_calling():
    """验证 ReAct 状态机直接消费 execute_ssh_command 工具调用并推进，不依赖文本正则"""
    async with get_session_factory()() as session:
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)

        host = SSHConnection(
            name="tool-host-1",
            host="192.168.1.200",
            port=22,
            username="root",
            auth_type=AuthType.PASSWORD,
            password="pwd",
        )
        saved_host = await host_repo.save(host)

        # 模拟模型第一步调用 execute_ssh_command，第二步调用 complete_troubleshooting
        step1_call = ToolCall(
            id="call_1",
            name="execute_ssh_command",
            arguments={"command": "uptime", "step_title": "获取系统负载", "thought": "先查看系统运行时间与平均负载"},
        )
        step2_call = ToolCall(
            id="call_2",
            name="complete_troubleshooting",
            arguments={"final_answer": "系统负载极低（0.05），运行状态健康稳定。", "thought": "指标正常，完成排查"},
        )

        mock_llm = ToolCallingMockLLM([
            LLMResponse(content="", tool_calls=[step1_call]),
            LLMResponse(content="", tool_calls=[step2_call]),
        ])

        gateway = FakeSSHGateway()
        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=mock_llm,
            ssh_gateway=gateway,
            session_repo=chat_repo,
            tool_registry=build_default_registry(),
        )

        events = []
        async for ev in uc.execute(saved_host.connection_id, "检查服务器负载"):
            events.append(ev)

        # 验证动态路由后仍保留命令执行与任务收敛基线工具
        assert len(mock_llm.received_tools) >= 1
        routed_names = {
            tool["function"]["name"] for tool in mock_llm.received_tools[0] or []
        }
        registered_names = {spec["function"]["name"] for spec in build_default_registry().specs()}
        assert {"execute_ssh_command", "complete_troubleshooting"} <= routed_names
        assert routed_names <= registered_names

        # 验证步骤事件正确产出
        thought_events = [e for e in events if e.get("type") == "thought"]
        action_events = [e for e in events if e.get("type") == "action"]
        finish_events = [e for e in events if e.get("type") == "finish"]

        assert len(thought_events) >= 2
        assert thought_events[0]["title"] == "获取系统负载"
        assert action_events[0]["command"] == "uptime"

        assert len(finish_events) == 1
        assert "运行状态健康稳定" in finish_events[0]["conclusion"]


@pytest.mark.asyncio
async def test_react_engine_tool_calling_audit_interception():
    """验证通过原生 Tool Calling 下发高危违规命令时，安全审计钩子在执行前严密拦截"""
    async with get_session_factory()() as session:
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)

        host = SSHConnection(
            name="tool-host-audit",
            host="192.168.1.201",
            port=22,
            username="root",
            auth_type=AuthType.PASSWORD,
            password="pwd",
        )
        saved_host = await host_repo.save(host)

        danger_call = ToolCall(
            id="call_danger",
            name="execute_ssh_command",
            arguments={"command": "rm -rf /", "step_title": "清理全盘", "thought": "尝试清理全部文件"},
        )

        mock_llm = ToolCallingMockLLM([
            LLMResponse(content="", tool_calls=[danger_call]),
        ])

        auditor = FakeAudit(block_command="rm -rf /")
        gateway = FakeSSHGateway()
        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=mock_llm,
            ssh_gateway=gateway,
            session_repo=chat_repo,
            auditor=auditor,
        )

        events = []
        async for ev in uc.execute(saved_host.connection_id, "彻底清理磁盘"):
            events.append(ev)

        # 验证指令在执行前被审计拦截，未实际向网关执行
        action_events = [e for e in events if e.get("type") == "action"]
        obs_events = [e for e in events if e.get("type") == "observation"]

        assert len(action_events) >= 1
        assert action_events[0]["blocked"] is True
        assert any("安全阻断" in o.get("output", "") for o in obs_events)
