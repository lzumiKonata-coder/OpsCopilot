"""
第二阶段：动静通道分离与远端文件排队锁测试
验证 RemoteFileMutationQueue 并发控制、read_remote_file 与 write_remote_file 独立通道执行
"""

import asyncio
from typing import Any, AsyncGenerator, Dict, List, Optional
import pytest

from adapter.repository.database import get_db_session
from adapter.repository import (
    SQLAlchemyChatRepository,
    SQLAlchemySSHConnectionRepository,
)
from adapter.tools.builtin import build_default_registry
from application.runtime import RemoteFileMutationQueue
from application.ports import ILLMClient, ISSHGateway, LLMResponse, ToolCall
from application.usecases.react import ReActUseCase
from domain.entities import AuthType, Command, ExecutionResult, SSHConnection


class ToolCallingMockLLM(ILLMClient):
    def __init__(self, responses: List[LLMResponse]):
        self.responses = responses
        self.call_count = 0

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.2,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncGenerator[str, None]:
        res = await self.chat(messages, temperature=temperature, tools=tools)
        yield str(res)

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.2,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        idx = min(self.call_count, len(self.responses) - 1)
        resp = self.responses[idx]
        self.call_count += 1
        return resp


class MockFileSSHGateway(ISSHGateway):
    """支持静态文件读写与命令记录的测试网关"""

    def __init__(self):
        self.files: Dict[str, str] = {
            "/etc/nginx/nginx.conf": "events {}\nhttp {\n    server {\n        listen 80;\n    }\n}\n",
        }
        self.executed_commands: List[str] = []
        self.file_write_delays: Dict[str, float] = {}

    async def execute(self, host: SSHConnection, command: Command) -> ExecutionResult:
        self.executed_commands.append(command.raw_command)
        return ExecutionResult(stdout="OK\n", exit_status=0)

    async def test_connection(self, host: SSHConnection) -> bool:
        return True

    async def read_file(
        self,
        host: SSHConnection,
        path: str,
        offset: int = 1,
        limit: int = 200,
    ) -> str:
        if path not in self.files:
            raise FileNotFoundError(f"No such file: {path}")
        content = self.files[path]
        lines = content.splitlines(keepends=True)
        start = max(0, offset - 1)
        end = start + limit
        return "".join(lines[start:end])

    async def write_file(
        self,
        host: SSHConnection,
        path: str,
        content: str,
    ) -> bool:
        # 模拟写入延迟以便验证排队锁效果
        delay = self.file_write_delays.get(path, 0.0)
        if delay > 0:
            await asyncio.sleep(delay)
        self.files[path] = content
        return True


@pytest.mark.asyncio
async def test_remote_file_mutation_queue_serializes_same_path():
    """验证 RemoteFileMutationQueue 对相同主机相同路径并发写操作实施严格串行排队"""
    queue = RemoteFileMutationQueue()
    execution_order = []

    async def slow_write(worker_id: int, duration: float):
        execution_order.append(f"start_{worker_id}")
        await asyncio.sleep(duration)
        execution_order.append(f"end_{worker_id}")

    # 针对同一文件 /etc/hosts 的两个写操作并发发起
    task1 = asyncio.create_task(
        queue.run_with_lock("host_1", "/etc/hosts", slow_write, 1, 0.1)
    )
    task2 = asyncio.create_task(
        queue.run_with_lock("host_1", "/etc/hosts", slow_write, 2, 0.05)
    )

    await asyncio.gather(task1, task2)

    # 验证 Worker 1 执行期间 Worker 2 无法抢先执行，必须排队等待 Worker 1 完全结束
    assert execution_order == ["start_1", "end_1", "start_2", "end_2"]


@pytest.mark.asyncio
async def test_remote_file_mutation_queue_allows_different_paths_parallel():
    """验证 RemoteFileMutationQueue 对不同文件路径的并发写操作互不阻塞、并发推进"""
    queue = RemoteFileMutationQueue()
    execution_order = []

    async def concurrent_write(worker_id: int, duration: float):
        execution_order.append(f"start_{worker_id}")
        await asyncio.sleep(duration)
        execution_order.append(f"end_{worker_id}")

    # 分别写入不同路径
    task1 = asyncio.create_task(
        queue.run_with_lock("host_1", "/etc/file_a", concurrent_write, 1, 0.1)
    )
    task2 = asyncio.create_task(
        queue.run_with_lock("host_1", "/etc/file_b", concurrent_write, 2, 0.02)
    )

    await asyncio.gather(task1, task2)

    # 路径不同，Worker 2 耗时更短应先于 Worker 1 结束
    assert execution_order[0] == "start_1"
    assert "start_2" in execution_order[:2]
    assert execution_order.index("end_2") < execution_order.index("end_1")


@pytest.mark.asyncio
async def test_react_read_remote_file_static_channel():
    """验证 read_remote_file 工具调用走只读静态通道，获取纯净文本且不向 PTY 发送命令"""
    async for session in get_db_session():
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)

        host = SSHConnection(
            name="static-channel-host",
            host="192.168.1.205",
            port=22,
            username="root",
            auth_type=AuthType.PASSWORD,
            password="pwd",
        )
        saved_host = await host_repo.save(host)

        read_call = ToolCall(
            id="call_read_1",
            name="read_remote_file",
            arguments={"path": "/etc/nginx/nginx.conf", "step_title": "读取 Nginx 主配置"},
        )
        finish_call = ToolCall(
            id="call_finish",
            name="complete_troubleshooting",
            arguments={"final_answer": "已获取 Nginx 配置，当前服务监听在 80 端口。"},
        )

        mock_llm = ToolCallingMockLLM([
            LLMResponse(content="", tool_calls=[read_call]),
            LLMResponse(content="", tool_calls=[finish_call]),
        ])

        gateway = MockFileSSHGateway()
        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=mock_llm,
            ssh_gateway=gateway,
            session_repo=chat_repo,
            tool_registry=build_default_registry(),
        )

        events = []
        async for ev in uc.execute(saved_host.connection_id, "查看 nginx 配置"):
            events.append(ev)

        # 验证 observation 接收到了纯净文件文本内容
        obs_events = [e for e in events if e.get("type") == "observation"]
        assert len(obs_events) >= 1
        assert "server {" in obs_events[0]["output"]
        assert "listen 80;" in obs_events[0]["output"]

        # 验证未向终端执行普通 shell 拼接命令 (如无需运行 cat /etc/nginx/nginx.conf 等到终端产生 ANSI 码)
        assert len(gateway.executed_commands) == 0


@pytest.mark.asyncio
async def test_react_write_remote_file_safe_mutation():
    """验证 write_remote_file 工具调用受排队锁保护并原子写入远端文件"""
    async for session in get_db_session():
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)

        host = SSHConnection(
            name="write-channel-host",
            host="192.168.1.206",
            port=22,
            username="root",
            auth_type=AuthType.PASSWORD,
            password="pwd",
        )
        saved_host = await host_repo.save(host)

        new_config_text = "server { listen 8080; server_name localhost; }\n"
        write_call = ToolCall(
            id="call_write_1",
            name="write_remote_file",
            arguments={
                "path": "/etc/nginx/conf.d/vhost.conf",
                "content": new_config_text,
                "step_title": "写入 Nginx 虚拟主机配置",
            },
        )
        finish_call = ToolCall(
            id="call_finish",
            name="complete_troubleshooting",
            arguments={"final_answer": "已成功更新 Nginx 虚拟主机配置。"},
        )

        mock_llm = ToolCallingMockLLM([
            LLMResponse(content="", tool_calls=[write_call]),
            LLMResponse(content="", tool_calls=[finish_call]),
        ])

        gateway = MockFileSSHGateway()
        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=mock_llm,
            ssh_gateway=gateway,
            session_repo=chat_repo,
            tool_registry=build_default_registry(),
        )

        events = []
        async for ev in uc.execute(saved_host.connection_id, "配置 nginx 8080 端口"):
            events.append(ev)

        # 验证远端文件已成功被更新写入
        assert "/etc/nginx/conf.d/vhost.conf" in gateway.files
        assert gateway.files["/etc/nginx/conf.d/vhost.conf"] == new_config_text

        # 验证产生文件写入成功回显
        obs_events = [e for e in events if e.get("type") == "observation"]
        assert len(obs_events) >= 1
        assert "文件写入成功" in obs_events[0]["output"]
