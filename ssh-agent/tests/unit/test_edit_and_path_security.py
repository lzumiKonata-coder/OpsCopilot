"""
针对安全文件精准替换 (edit_remote_file) 与敏感系统路径安全审查机制的测试
(File Path Security Audit & Surgical Remote File Editing Tests)
"""

import json
import pytest

from adapter.tools.builtin import build_default_registry
from application.ports import ILLMClient, ISSHGateway
from application.react.engine import LangGraphReActEngine
from application.react.state import ReActGraphState
from domain.policies.file_access import audit_file_path_security
from domain.entities import AuthType, Command, ExecutionResult, RiskLevel, SSHConnection


class MemoryMockGateway(ISSHGateway):
    """内存模拟网关，用于测试 read_file, write_file, edit_file"""

    def __init__(self, initial_files: dict | None = None):
        self.files = initial_files or {}
        self.executed_commands: list[str] = []

    async def test_connection(self, host: SSHConnection) -> bool:
        return True

    async def execute(self, host: SSHConnection, command: Command) -> ExecutionResult:
        self.executed_commands.append(command.raw_command)
        return ExecutionResult(exit_status=0, stdout="ok", stderr="", is_truncated=False, duration_ms=10.0)

    async def read_file(self, host: SSHConnection, path: str, offset: int = 1, limit: int = 200) -> str:
        if path not in self.files:
            return ""
        lines = self.files[path].splitlines(keepends=True)
        end = offset + limit - 1
        return "".join(lines[offset - 1 : end])

    async def write_file(self, host: SSHConnection, path: str, content: str) -> bool:
        self.files[path] = content
        return True


def test_audit_file_path_security_forbidden_paths():
    """验证绝对禁止篡改的根安全路径直接拦截"""
    # 核心凭据与特权配置
    assert audit_file_path_security("/etc/shadow").risk_level == RiskLevel.FORBIDDEN
    assert audit_file_path_security("/etc/gshadow").risk_level == RiskLevel.FORBIDDEN
    assert audit_file_path_security("/etc/sudoers").risk_level == RiskLevel.FORBIDDEN
    assert audit_file_path_security("/etc/sudoers.d/90-cloud-init-users").risk_level == RiskLevel.FORBIDDEN

    # 引导与内核
    assert audit_file_path_security("/boot/grub/grub.cfg").risk_level == RiskLevel.FORBIDDEN
    assert audit_file_path_security("/boot/vmlinuz-5.10").risk_level == RiskLevel.FORBIDDEN

    # 路径穿越绕过防御
    assert audit_file_path_security("/var/log/../../etc/shadow").risk_level == RiskLevel.FORBIDDEN
    assert audit_file_path_security("/etc/./shadow").risk_level == RiskLevel.FORBIDDEN
    assert audit_file_path_security("/etc/sudoers/../sudoers").risk_level == RiskLevel.FORBIDDEN


def test_audit_file_path_security_high_risk_paths():
    """验证高风险关键系统配置标记为 HIGH_RISK，需人工审批"""
    assert audit_file_path_security("/etc/passwd").risk_level == RiskLevel.HIGH_RISK
    assert audit_file_path_security("/etc/group").risk_level == RiskLevel.HIGH_RISK
    assert audit_file_path_security("/etc/hosts").risk_level == RiskLevel.HIGH_RISK
    assert audit_file_path_security("/etc/resolv.conf").risk_level == RiskLevel.HIGH_RISK
    assert audit_file_path_security("/etc/fstab").risk_level == RiskLevel.HIGH_RISK
    assert audit_file_path_security("/etc/systemd/system/nginx.service").risk_level == RiskLevel.HIGH_RISK
    assert audit_file_path_security("/etc/crontab").risk_level == RiskLevel.HIGH_RISK
    assert audit_file_path_security("/etc/cron.d/daily-backup").risk_level == RiskLevel.HIGH_RISK
    assert audit_file_path_security("/etc/security/limits.conf").risk_level == RiskLevel.HIGH_RISK


def test_audit_file_path_security_safe_paths():
    """验证日常业务应用配置属于 SAFE"""
    assert audit_file_path_security("/etc/nginx/nginx.conf").risk_level == RiskLevel.SAFE
    assert audit_file_path_security("/var/www/html/index.html").risk_level == RiskLevel.SAFE
    assert audit_file_path_security("/home/admin/app/settings.py").risk_level == RiskLevel.SAFE
    assert audit_file_path_security("/tmp/debug.log").risk_level == RiskLevel.SAFE
    assert audit_file_path_security("/opt/myapp/config.yaml").risk_level == RiskLevel.SAFE


@pytest.mark.asyncio
async def test_gateway_edit_file_success_replacement():
    """验证 ISSHGateway.edit_file 精确局部替换"""
    host = SSHConnection(
        name="web-01",
        host="192.168.1.100",
        port=22,
        username="root",
        auth_type=AuthType.PASSWORD,
        password="pwd",
    )
    initial_content = "server {\n    listen 80;\n    server_name localhost;\n    worker_connections 512;\n}\n"
    gateway = MemoryMockGateway(initial_files={"/etc/nginx/nginx.conf": initial_content})

    ok = await gateway.edit_file(
        host=host,
        path="/etc/nginx/nginx.conf",
        target="worker_connections 512;",
        replacement="worker_connections 2048;",
    )
    assert ok is True
    updated = await gateway.read_file(host, "/etc/nginx/nginx.conf")
    assert "worker_connections 2048;" in updated
    assert "worker_connections 512;" not in updated


@pytest.mark.asyncio
async def test_gateway_edit_file_target_not_found_raises():
    """验证 ISSHGateway.edit_file 当目标不存在时拒绝静默篡改"""
    host = SSHConnection(
        name="web-01",
        host="192.168.1.100",
        port=22,
        username="root",
        auth_type=AuthType.PASSWORD,
        password="pwd",
    )
    initial_content = "listen 80;\n"
    gateway = MemoryMockGateway(initial_files={"/etc/nginx/nginx.conf": initial_content})

    with pytest.raises(ValueError, match="待替换的目标文本在文件 '/etc/nginx/nginx.conf' 中未找到"):
        await gateway.edit_file(
            host=host,
            path="/etc/nginx/nginx.conf",
            target="listen 8080;",
            replacement="listen 443;",
        )


@pytest.mark.asyncio
async def test_react_engine_executes_edit_remote_file_tool():
    """验证 ReAct 状态机原生响应 edit_remote_file 工具调用"""
    host = SSHConnection(
        name="db-01",
        host="192.168.1.101",
        port=22,
        username="root",
        auth_type=AuthType.PASSWORD,
        password="pwd",
    )
    gateway = MemoryMockGateway(
        initial_files={"/opt/app/config.ini": "port = 8080\nmax_connections = 100\n"}
    )

    class MockLLM(ILLMClient):
        async def chat(self, messages: list[dict], temperature: float = 0.1, tools: list[dict] | None = None) -> dict:
            return {
                "role": "assistant",
                "content": "需要调大连接池至 500",
                "tool_calls": [
                    {
                        "id": "call_edit_1",
                        "type": "function",
                        "function": {
                            "name": "edit_remote_file",
                            "arguments": json.dumps({
                                "path": "/opt/app/config.ini",
                                "target": "max_connections = 100",
                                "replacement": "max_connections = 500",
                            }),
                        },
                    }
                ],
            }

        async def stream_chat(self, messages: list):
            yield ""

    engine = LangGraphReActEngine(
        llm_client=MockLLM(),
        ssh_gateway=gateway,
        tool_registry=build_default_registry(),
    )

    state: ReActGraphState = {
        "host": host,
        "user_prompt": "请将 /opt/app/config.ini 中的 max_connections 修改为 500",
        "session_id": "test_edit_tool_session",
        "max_steps": 5,
        "step_idx": 1,
        "permission_mode": "auto_execute",
    }

    # 1. 触发 think
    think_res = await engine._node_think(state)
    state.update(think_res)
    assert state.get("current_tool_name") == "edit_remote_file"
    assert state.get("current_tool_args", {}).get("target") == "max_connections = 100"

    # 2. 触发 audit (SAFE)
    audit_res = await engine._node_audit(state)
    state.update(audit_res)
    assert state.get("is_blocked") is False

    # 3. 触发 act
    act_res = await engine._node_act(state)
    state.update(act_res)
    assert "文件局部修改成功" in state.get("current_observation", "")
    assert "/opt/app/config.ini" in state.get("current_observation", "")

    # 4. 验证真实文件被修改
    assert gateway.files["/opt/app/config.ini"] == "port = 8080\nmax_connections = 500\n"


@pytest.mark.asyncio
async def test_react_engine_blocks_edit_forbidden_path():
    """验证 ReAct 状态机在审计阶段直接拦截针对 forbidden 路径的篡改"""
    host = SSHConnection(
        name="db-01",
        host="192.168.1.101",
        port=22,
        username="root",
        auth_type=AuthType.PASSWORD,
        password="pwd",
    )
    gateway = MemoryMockGateway(
        initial_files={"/etc/shadow": "root:$6$xyz:19000:0:99999:7:::"}
    )

    class MockLLM(ILLMClient):
        async def chat(self, messages: list[dict], temperature: float = 0.1, tools: list[dict] | None = None) -> dict:
            return {
                "role": "assistant",
                "content": "试图修改 shadow",
                "tool_calls": [
                    {
                        "id": "call_bad_1",
                        "type": "function",
                        "function": {
                            "name": "edit_remote_file",
                            "arguments": json.dumps({
                                "path": "/etc/shadow",
                                "target": "root:",
                                "replacement": "root2:",
                            }),
                        },
                    }
                ],
            }

        async def stream_chat(self, messages: list):
            yield ""

    engine = LangGraphReActEngine(
        llm_client=MockLLM(),
        ssh_gateway=gateway,
        tool_registry=build_default_registry(),
    )

    state: ReActGraphState = {
        "host": host,
        "user_prompt": "修改 shadow 文件",
        "session_id": "test_forbidden_edit_session",
        "max_steps": 5,
        "step_idx": 1,
    }

    # 1. 触发 think
    think_res = await engine._node_think(state)
    state.update(think_res)
    assert state.get("current_tool_name") == "edit_remote_file"

    # 2. 触发 audit
    audit_res = await engine._node_audit(state)
    state.update(audit_res)

    # 验证审计环节硬性拦截
    assert state.get("is_blocked") is True
    assert "安全阻断" in state.get("blocked_reason", "")
    assert "/etc/shadow" in state.get("blocked_reason", "")
    assert gateway.files["/etc/shadow"] == "root:$6$xyz:19000:0:99999:7:::"


@pytest.mark.asyncio
async def test_dynamic_terminal_buffer_in_node_think():
    """验证终端快照位于稳定的任务消息中，动态步骤消息不再重复携带快照"""
    host = SSHConnection(
        name="test-host",
        host="127.0.0.1",
        port=22,
        username="root",
        auth_type=AuthType.PASSWORD,
        password="pwd",
    )

    captured_messages = []

    class MockCaptureLLM(ILLMClient):
        async def chat(self, messages: list[dict], temperature: float = 0.1, tools: list[dict] | None = None) -> dict:
            captured_messages.append(messages)
            return {
                "role": "assistant",
                "content": "Thought: 检查系统状态\nAction: uname -a\nStep_Title: 检查系统内核",
            }

        async def stream_chat(self, messages: list):
            yield ""

    engine = LangGraphReActEngine(llm_client=MockCaptureLLM(), ssh_gateway=MemoryMockGateway())

    # 第 1 轮测试
    state_step_1: ReActGraphState = {
        "host": host,
        "user_prompt": "检查状态",
        "terminal_buffer": "LAST_LOGIN: 2026-10-31 12:00:00\n[root@localhost ~]#",
        "step_idx": 1,
        "max_steps": 5,
    }
    await engine._node_think(state_step_1)

    assert len(captured_messages) == 1
    # system 消息保持纯静态，运行时环境位于稳定的任务消息中
    assert "LAST_LOGIN: 2026-10-31 12:00:00" not in captured_messages[0][0]["content"]
    assert "LAST_LOGIN: 2026-10-31 12:00:00" in captured_messages[0][1]["content"]

    # 第 2 轮测试
    state_step_2: ReActGraphState = {
        "host": host,
        "user_prompt": "检查状态",
        "terminal_buffer": "LAST_LOGIN: 2026-10-31 12:00:00\n[root@localhost ~]#",
        "step_idx": 2,
        "max_steps": 5,
        "executed_steps": [{"step": 1, "command": "whoami"}],
    }
    await engine._node_think(state_step_2)

    assert len(captured_messages) == 2
    step_2_messages = captured_messages[1]
    # 任务消息保持稳定，仍携带终端快照
    assert "LAST_LOGIN: 2026-10-31 12:00:00" in step_2_messages[1]["content"]
    # 最新步骤消息只包含轻量步骤状态，不重复附带庞大快照
    latest_step_message = step_2_messages[-1]["content"]
    assert "第 2 步" in latest_step_message
    assert "LAST_LOGIN: 2026-10-31 12:00:00" not in latest_step_message
