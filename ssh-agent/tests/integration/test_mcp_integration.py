"""MCP 工具接入集成测试：注册、执行、审批策略、超时与断连"""

import json
import sys
import time
from pathlib import Path

import pytest

from adapter.security.auditor import BashlexCommandAuditor
from adapter.tools.builtin import build_default_registry
from adapter.tools.mcp import McpError, McpManager, McpServerConfig, load_mcp_server_configs
from application.ports import ToolContext, ToolOperation
from application.react.engine import LangGraphReActEngine

FAKE_SERVER = str(Path(__file__).resolve().parents[1] / "fakes" / "fake_mcp_server.py")


def _fake_config(timeout: float = 10.0) -> McpServerConfig:
    return McpServerConfig(
        name="fake",
        command=sys.executable,
        args=[FAKE_SERVER],
        timeout=timeout,
    )


class _StubLLM:
    model = "stub-model"


@pytest.mark.asyncio
async def test_mcp_tools_register_and_execute():
    registry = build_default_registry()
    manager = McpManager([_fake_config()])
    try:
        registered = await manager.connect_all(registry)
        assert registered == 3

        tool = registry.get("mcp__fake__echo_text")
        assert tool is not None
        assert tool.spec.operation == ToolOperation.MCP
        assert tool.spec.requires_approval is True
        assert tool.spec.source == "mcp:fake"

        result = await tool.execute({"text": "hello"}, ToolContext())
        assert result.is_error is False
        assert result.observation == "echo: hello"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_mcp_tool_reports_is_error_result():
    registry = build_default_registry()
    manager = McpManager([_fake_config()])
    try:
        await manager.connect_all(registry)
        failure = await registry.get("mcp__fake__fail_tool").execute({}, ToolContext())
        assert failure.is_error is True
        assert "[外部工具调用失败]" in failure.observation
        assert "intentional failure" in failure.observation

        # 未注册的远程工具名走协议错误分支
        with pytest.raises(McpError, match="unknown tool"):
            await registry.get("mcp__fake__echo_text").client.call_tool("missing_tool", {})
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_mcp_tool_timeout_is_contained():
    registry = build_default_registry()
    manager = McpManager([_fake_config(timeout=1.0)])
    try:
        await manager.connect_all(registry)
        tool = registry.get("mcp__fake__slow_tool")
        result = await tool.execute({}, ToolContext())
        assert result.is_error is True
        assert "timed out" in result.observation
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_mcp_tool_requires_approval_in_audit_node():
    registry = build_default_registry()
    manager = McpManager([_fake_config()])
    try:
        await manager.connect_all(registry)
        engine = LangGraphReActEngine(llm_client=_StubLLM(), tool_registry=registry)
        state = {
            "host": None,
            "current_tool_name": "mcp__fake__echo_text",
            "current_tool_args": {"text": "hi"},
            "current_command": "mcp:fake.echo_text",
            "current_step_title": "调用外部工具",
            "step_idx": 1,
            "session_id": "mcp-approval-session",
            "permission_mode": "request_approval",
            "approved_commands": [],
        }
        result = await engine._node_audit(state)
        events = result.get("emitted_events") or []
        assert events and events[0]["type"] == "approval_required"
        assert "mcp__fake__echo_text" in events[0]["reason"]
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_mcp_tool_requires_approval_even_in_full_access():
    """MCP 外部工具必须强制逐次人工审批，full_access 与预授权命令均不得绕过"""
    registry = build_default_registry()
    manager = McpManager([_fake_config()])
    try:
        await manager.connect_all(registry)
        engine = LangGraphReActEngine(llm_client=_StubLLM(), tool_registry=registry)
        state = {
            "host": None,
            "current_tool_name": "mcp__fake__echo_text",
            "current_tool_args": {"text": "hi"},
            "current_command": "mcp:fake.echo_text",
            "current_step_title": "调用外部工具",
            "step_idx": 1,
            "session_id": "mcp-full-access-session",
            "permission_mode": "full_access",
            "approved_commands": ["mcp:fake.echo_text"],
        }
        result = await engine._node_audit(state)
        events = result.get("emitted_events") or []
        assert events and events[0]["type"] == "approval_required"
        assert result.get("pending_approval_id")
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_builtin_high_risk_command_still_auto_approved_in_full_access():
    """回归保护：MCP 强制审批不能误伤内置工具在 full_access 下的放行语义"""
    engine = LangGraphReActEngine(llm_client=_StubLLM(), auditor=BashlexCommandAuditor())
    state = {
        "host": None,
        "current_tool_name": "execute_ssh_command",
        "current_tool_args": {"command": "sudo systemctl restart nginx"},
        "current_command": "sudo systemctl restart nginx",
        "current_step_title": "重启服务",
        "step_idx": 1,
        "session_id": "full-access-builtin-session",
        "permission_mode": "full_access",
        "approved_commands": [],
    }
    result = await engine._node_audit(state)
    events = result.get("emitted_events") or []
    assert events and events[0]["type"] == "action"


@pytest.mark.asyncio
async def test_mcp_tool_executes_in_act_node():
    registry = build_default_registry()
    manager = McpManager([_fake_config()])
    try:
        await manager.connect_all(registry)
        engine = LangGraphReActEngine(llm_client=_StubLLM(), tool_registry=registry)
        state = {
            "host": None,
            "current_tool_name": "mcp__fake__echo_text",
            "current_tool_args": {"text": "act-node"},
            "current_command": "mcp:fake.echo_text",
            "current_step_title": "调用外部工具",
            "step_idx": 1,
            "session_id": "mcp-act-session",
            "is_root": False,
            "start_time": time.time(),
            "max_run_seconds": 60,
        }
        result = await engine._node_act(state)
        events = result.get("emitted_events") or []
        assert events and events[0]["output"] == "echo: act-node"
        assert result["current_observation"] == "echo: act-node"
    finally:
        await manager.close()


def test_mcp_config_parsing(monkeypatch):
    monkeypatch.setenv(
        "MCP_SERVERS",
        json.dumps(
            [
                {
                    "name": "demo",
                    "command": sys.executable,
                    "args": [FAKE_SERVER],
                    "env": {"DEMO_TOKEN": "x"},
                    "timeout": 8,
                }
            ]
        ),
    )
    configs = load_mcp_server_configs()
    assert len(configs) == 1
    assert configs[0].name == "demo"
    assert configs[0].timeout == 8.0
    assert configs[0].env["DEMO_TOKEN"] == "x"

    monkeypatch.setenv("MCP_SERVERS", "not-json")
    assert load_mcp_server_configs() == []
