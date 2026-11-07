"""
即时取消响应与终端中断/断开解耦测试
"""

import asyncio
import time
import uuid
import pytest

from adapter.repository import (
    SQLAlchemyChatRepository,
    SQLAlchemySSHConnectionRepository,
)
from application.ports import ILLMClient, ISSHGateway
from domain.entities import AuthType, Command, ExecutionResult, SSHConnection
from application.react.engine import LangGraphReActEngine
from application.react.managers import InMemoryApprovalManager
from application.react.observation import normalize_observation_payload
from application.react.state import ReActGraphState
from application.usecases.react import ReActUseCase


class MockLLM(ILLMClient):
    def __init__(self, responses: list[str] | None = None):
        self.call_count = 0
        self.responses = responses or ["Thought: 探测\nAction: uptime\nStep_Title: 步骤1"]

    async def chat(self, messages: list, temperature: float = 0.2, tools=None, on_token=None) -> str:
        idx = min(self.call_count, len(self.responses) - 1)
        resp = self.responses[idx]
        self.call_count += 1
        return resp

    async def stream_chat(self, messages: list):
        yield await self.chat(messages)


class MockGateway(ISSHGateway):
    def __init__(self, fixed_output: str = "default output"):
        self.commands: list[str] = []
        self.fixed_output = fixed_output

    async def execute(self, host: SSHConnection, command: Command) -> ExecutionResult:
        self.commands.append(command.raw_command)
        return ExecutionResult(stdout=self.fixed_output, stderr="", exit_status=0)

    async def read_file(self, host: SSHConnection, path: str, offset: int = 1, limit: int = 100) -> str:
        return self.fixed_output

    async def write_file(self, host: SSHConnection, path: str, content: str) -> bool:
        return True

    async def edit_file(self, host: SSHConnection, path: str, target: str, replacement: str) -> bool:
        return True

    async def test_connection(self, host: SSHConnection) -> bool:
        return True


# ====================================================================
# 3. 即时取消响应 (Immediate Cancellation Responsiveness)
# ====================================================================

@pytest.mark.asyncio
async def test_wait_for_decision_aborts_immediately_on_cancel_check():
    """测试审批等待中途检测到 cancel_check 变为 True 时，立即抛出 CancelledError 打断，无需等满 120 秒"""
    mgr = InMemoryApprovalManager()
    appr_id = mgr.create_approval_request("sess_cancel", 1, "rm -rf /tmp/test", "清理临时文件")

    is_cancelled = False

    async def mock_cancel():
        return is_cancelled

    # 启动等待任务
    wait_task = asyncio.create_task(mgr.wait_for_decision(appr_id, timeout=60.0, cancel_check=mock_cancel))

    # 100ms 后触发取消
    await asyncio.sleep(0.1)
    is_cancelled = True

    # 验证等待协程在瞬间打断并抛出 CancelledError
    start_t = time.time()
    with pytest.raises(asyncio.CancelledError):
        await wait_task
    elapsed = time.time() - start_t
    assert elapsed < 1.0, f"应在 1 秒内响应取消打断，实际耗时 {elapsed}s"
@pytest.mark.asyncio
async def test_safe_llm_chat_aborts_in_flight_call_on_cancellation():
    """测试正在进行的长耗时/流式 LLM 调用能被 cancel_check 在百毫秒级即时打断"""
    class SlowLLM(ILLMClient):
        async def chat(self, messages: list, temperature: float = 0.2, tools=None, on_token=None) -> str:
            for i in range(40):
                await asyncio.sleep(0.05)
                if on_token:
                    on_token(f"token_{i}")
            return "done"

        async def stream_chat(self, messages: list):
            yield await self.chat(messages)

    engine = LangGraphReActEngine(llm_client=SlowLLM())

    is_cancelled = False
    async def mock_cancel():
        return is_cancelled

    # 150ms 后触发取消
    async def schedule_cancel():
        await asyncio.sleep(0.15)
        nonlocal is_cancelled
        is_cancelled = True

    asyncio.create_task(schedule_cancel())

    t0 = time.time()
    with pytest.raises(asyncio.CancelledError):
        await engine._safe_llm_chat(
            conversation=[{"role": "user", "content": "hi"}],
            cancel_check=mock_cancel,
        )
    elapsed = time.time() - t0
    assert elapsed < 0.8, f"应在 0.8s 内即时中断正在运行的 LLM 调用，实际耗时 {elapsed:.2f}s"
# ====================================================================
# 5. 长指令中断与断开解耦验证 (PTY / Non-PTY Cancellation & Disconnect Decoupling)
# ====================================================================

@pytest.mark.asyncio
async def test_terminal_manager_mid_flight_cancellation_sends_ctrl_c():
    """验证 PTY 终端在执行长时间命令时，一旦 cancel_check 触发，立即发送 \\x03 (Ctrl+C) 并抛出 CancelledError"""
    from adapter.ssh.terminal_manager import InMemorySSHTerminalSessionManager
    from unittest.mock import MagicMock

    tm = InMemorySSHTerminalSessionManager()
    mock_process = MagicMock()
    mock_process.stdin = MagicMock()

    tm.register("test_host", mock_process)
    tm.on_stdout_chunk("test_host", "[root@host ~]# ")

    is_cancelled = False

    def check_cancel():
        return is_cancelled

    async def cancel_later():
        nonlocal is_cancelled
        await asyncio.sleep(0.05)
        is_cancelled = True

    # 启动后台取消触发
    cancel_task = asyncio.create_task(cancel_later())

    with pytest.raises(asyncio.CancelledError):
        await tm.send_and_capture(
            identifier="test_host",
            command="yum install -y gcc",
            wait_seconds=0.01,
            idle_timeout=0.01,
            max_timeout=60.0,
            cancel_check=check_cancel,
        )

    await cancel_task
    # 验证是否写入了 Ctrl+C (\x03)
    mock_process.stdin.write.assert_any_call("\x03")


@pytest.mark.asyncio
async def test_non_pty_fallback_timeout_scaled_for_long_commands():
    """验证非 PTY 模式下，安装/下载等耗时指令根据类型自动扩展至 300s 超时，而不是被 30s 粗暴截断"""
    captured_commands = []

    class TimeoutRecordingGateway(ISSHGateway):
        async def execute(self, host: SSHConnection, command: Command) -> ExecutionResult:
            captured_commands.append(command)
            return ExecutionResult(stdout="Installed successfully", stderr="", exit_status=0)

        async def test_connection(self, host: SSHConnection) -> bool:
            return True

    engine = LangGraphReActEngine(llm_client=MockLLM(), ssh_gateway=TimeoutRecordingGateway())
    host = SSHConnection(name="h", host="1.1.1.1", port=22, username="root", auth_type=AuthType.PASSWORD)

    # 1. 耗时包管理器安装命令：应获得 300 秒超时
    await engine._execute_command_in_pty(host, "yum install -y nginx", wait_seconds=1.6)
    assert len(captured_commands) == 1
    assert captured_commands[0].timeout_seconds == 300

    # 2. 普通检测命令：应获得 60 秒默认超时（而不是 30s）
    await engine._execute_command_in_pty(host, "ls -la /var/log", wait_seconds=1.6)
    assert len(captured_commands) == 2
    assert captured_commands[1].timeout_seconds == 60

    # 3. 若整场任务剩余预算小于 300s，应被预算上限安全裁剪
    await engine._execute_command_in_pty(host, "docker pull redis:latest", wait_seconds=1.6, max_timeout=150.0)
    assert len(captured_commands) == 3
    assert captured_commands[2].timeout_seconds == 150


@pytest.mark.asyncio
async def test_non_pty_fallback_cancellation_aborts_mid_flight():
    """验证非 PTY 模式下执行耗时指令时，cancel_check 触发能够立即打断执行协程"""
    class SleepingGateway(ISSHGateway):
        async def execute(self, host: SSHConnection, command: Command) -> ExecutionResult:
            await asyncio.sleep(10.0)
            return ExecutionResult(stdout="ok", stderr="", exit_status=0)

        async def test_connection(self, host: SSHConnection) -> bool:
            return True

    engine = LangGraphReActEngine(llm_client=MockLLM(), ssh_gateway=SleepingGateway())
    host = SSHConnection(name="h", host="1.1.1.1", port=22, username="root", auth_type=AuthType.PASSWORD)

    is_cancelled = False

    async def trigger_cancel():
        nonlocal is_cancelled
        await asyncio.sleep(0.05)
        is_cancelled = True

    c_task = asyncio.create_task(trigger_cancel())

    with pytest.raises(asyncio.CancelledError):
        await engine._execute_command_in_pty(
            host,
            "yum install -y heavy-pkg",
            wait_seconds=1.6,
            cancel_check=lambda: is_cancelled,
        )
    await c_task


@pytest.mark.asyncio
async def test_react_cancel_endpoint_and_disconnect_decoupling():
    """测试 TaskCancellationManager 与 main.py 的取消端点，以及断开连接解耦"""
    from starlette.requests import Request
    from unittest.mock import AsyncMock, MagicMock
    from adapter.web.routers.agent_react import cancel_react_agent_task, react_agent_chat
    from adapter.web.schemas import ReActCancelRequest, ReActRequest
    import json

    task_ran_to_end = False

    class SlowUseCase:
        async def execute(self, cancel_check=None, **kwargs):
            nonlocal task_ran_to_end
            for i in range(5):
                if cancel_check and await cancel_check():
                    raise asyncio.CancelledError()
                await asyncio.sleep(0.05)
                yield {"type": "thought", "step": i + 1, "thought": f"step {i+1}"}
            task_ran_to_end = True

    mock_request = MagicMock()
    mock_request.is_disconnected = AsyncMock(return_value=False)

    # 1. 验证 cancel_on_disconnect=False 时，即使客户端 SSE 流退出，后台任务仍平稳运行到底
    req = ReActRequest(prompt="排查", session_id="sess_disconnect_test", cancel_on_disconnect=False)
    resp = await react_agent_chat(
        identifier="host_disconnect_test",
        req=req,
        request=mock_request,
        use_case=SlowUseCase(),
    )
    # 消费 1 个事件后主动模拟客户端关闭 SSE 迭代器
    async for chunk in resp.body_iterator:
        break

    # 等待后台任务运行完
    await asyncio.sleep(0.3)
    assert task_ran_to_end is True, "cancel_on_disconnect=False 时，客户端断开不应杀死后台任务"

    # 2. 验证显式调用 /react/cancel 端点能够精确打断正在运行的任务
    cancelled_saw = False

    class CancellableUseCase:
        async def execute(self, cancel_check=None, **kwargs):
            nonlocal cancelled_saw
            try:
                for i in range(20):
                    if cancel_check and await cancel_check():
                        raise asyncio.CancelledError()
                    await asyncio.sleep(0.05)
                    yield {"type": "thought", "step": i + 1}
            except asyncio.CancelledError:
                cancelled_saw = True
                yield {"type": "cancelled", "reason": "用户主动取消"}

    req2 = ReActRequest(prompt="排查", session_id="sess_cancel_test", cancel_on_disconnect=False)
    resp2 = await react_agent_chat(
        identifier="host_cancel_test",
        req=req2,
        request=mock_request,
        use_case=CancellableUseCase(),
    )

    async def read_stream():
        evs = []
        async for chunk in resp2.body_iterator:
            if chunk.startswith("data: "):
                payload = chunk[6:].strip()
                if payload != "[DONE]":
                    evs.append(json.loads(payload))
        return evs

    stream_task = asyncio.create_task(read_stream())
    await asyncio.sleep(0.08)

    # 调用显式取消接口
    cancel_res = await cancel_react_agent_task(
        identifier="host_cancel_test",
        req=ReActCancelRequest(session_id="sess_cancel_test"),
    )
    assert cancel_res["status"] == "ok"
    assert cancel_res["cancelled"] is True

    events = await stream_task
    assert any(e.get("type") == "cancelled" for e in events)
    assert cancelled_saw is True