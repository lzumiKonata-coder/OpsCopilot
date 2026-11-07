"""
测试：取消固定步数上限与即时插话纠偏机制 (Steering Messages & Unbounded Steps)
验证：
1. 默认 max_steps=None 时，排障流程突破 10 步旧限制，完全按目标收敛自然完成；
2. 即时插话管理器 (ISteeringManager) 能够将用户纠偏指示动态注入下一个思考轮次；
3. Web API 端点 POST /api/v1/hosts/{identifier}/agent/react/steer 正常响应。
"""

import pytest
import uuid

from adapter.repository import (
    SQLAlchemyChatRepository,
    SQLAlchemySSHConnectionRepository,
)
from application.ports import ILLMClient, ISSHGateway
from application.react.engine import LangGraphReActEngine
from application.react.managers import InMemoryApprovalManager, InMemorySteeringManager
from application.usecases.react import ReActUseCase
from domain.entities import AuthType, Command, ExecutionResult, SSHConnection


class MockLongRunningLLM(ILLMClient):
    """模拟一个执行多达 12 步 (超过旧 10 步限制) 并自然完成的模型"""
    def __init__(self):
        self.call_count = 0
        self.recorded_prompts: list[str] = []

    async def chat(self, messages: list, temperature: float = 0.2, tools=None) -> str:
        for m in messages:
            if m.get("role") == "user":
                self.recorded_prompts.append(m.get("content", ""))
        self.call_count += 1
        if self.call_count < 12:
            return f"Thought: 正在推进长线检查第 {self.call_count} 项。\nAction: echo 'step {self.call_count}'\nStep_Title: 步骤 {self.call_count}"
        return "Thought: 所有 12 项复杂巡检已全部核对完毕，指标正常。\nFinal_Answer: 复杂长线巡检顺利达成。"

    async def stream_chat(self, messages: list, temperature: float = 0.2, tools=None):
        resp = await self.chat(messages, temperature=temperature, tools=tools)
        yield resp


class MockGateway(ISSHGateway):
    async def execute(self, host: SSHConnection, command: Command) -> ExecutionResult:
        return ExecutionResult(exit_status=0, stdout="ok", stderr="", duration_ms=10.0)

    async def test_connection(self, host: SSHConnection) -> bool:
        return True


@pytest.mark.asyncio
async def test_unbounded_steps_exceeds_old_limit():
    """验证不传 max_steps 时，默认不限步数，可以顺利推演超过 10 步并自然达成"""
    llm = MockLongRunningLLM()
    gateway = MockGateway()
    host = SSHConnection(name="test", host="127.0.0.1", port=22, username="root", auth_type=AuthType.PASSWORD, password="pwd")

    engine = LangGraphReActEngine(llm_client=llm, ssh_gateway=gateway)
    graph = engine.build_graph(checkpointer=engine.checkpointer)

    initial_state = {
        "host": host,
        "user_prompt": "执行深度复杂多步排障",
        "session_id": f"sess_{uuid.uuid4().hex[:8]}",
        "max_steps": None,  # 无上限
        "step_idx": 1,
        "planned_steps_queue": [],
        "react_history": [],
        "executed_steps": [],
        "session_history": [],
        "has_encountered_error": False,
        "consecutive_errors": 0,
        "emitted_events": [],
    }

    events = []
    run_config = {"configurable": {"thread_id": initial_state["session_id"]}}
    async for mode, chunk in graph.astream(initial_state, config=run_config, stream_mode=["updates"]):
        for node_name, update in chunk.items():
            for ev in update.get("emitted_events", []):
                events.append(ev)

    thought_events = [e for e in events if e.get("type") == "thought"]
    # 验证确实推演了 12 步，突破了旧的 10 步硬编码截断
    assert len(thought_events) == 12
    assert any(e.get("status") == "completed" for e in thought_events)


@pytest.mark.asyncio
async def test_steering_message_dynamic_injection():
    """验证即时插话 (Steering Messages) 能在中途注入并改变推演 Prompt"""
    steering_mgr = InMemorySteeringManager()
    session_id = f"steer_sess_{uuid.uuid4().hex[:8]}"

    # 用户在执行中途推送插话纠偏
    steering_mgr.push_steering_message(session_id, "方向偏了，不要看常规状态，直接检查端口 8080")

    llm = MockLongRunningLLM()
    gateway = MockGateway()
    host = SSHConnection(name="test", host="127.0.0.1", port=22, username="root", auth_type=AuthType.PASSWORD, password="pwd")

    engine = LangGraphReActEngine(llm_client=llm, ssh_gateway=gateway, steering_manager=steering_mgr)
    graph = engine.build_graph(checkpointer=engine.checkpointer)

    initial_state = {
        "host": host,
        "user_prompt": "排查服务",
        "session_id": session_id,
        "max_steps": 2,
        "step_idx": 1,
        "planned_steps_queue": [],
        "react_history": [],
        "executed_steps": [],
        "session_history": [],
        "has_encountered_error": False,
        "consecutive_errors": 0,
        "emitted_events": [],
    }

    events = []
    run_config = {"configurable": {"thread_id": session_id}}
    async for mode, chunk in graph.astream(initial_state, config=run_config, stream_mode=["updates"]):
        for node_name, update in chunk.items():
            for ev in update.get("emitted_events", []):
                events.append(ev)

    # 验证产生 steering_applied 事件
    steering_evs = [e for e in events if e.get("type") == "steering_applied"]
    assert len(steering_evs) == 1
    assert "检查端口 8080" in steering_evs[0]["content"]

    # 验证 LLM 收到的 prompt 中包含了插话纠偏提示
    assert any("用户最新即时插话/方向纠偏指令" in p and "检查端口 8080" in p for p in llm.recorded_prompts)


@pytest.mark.asyncio
async def test_web_api_steer_endpoint():
    """验证 Web API 端点 POST /api/v1/hosts/{identifier}/agent/react/steer"""
    from httpx import ASGITransport, AsyncClient
    from adapter.web.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. 注册测试主机
        resp = await client.post(
            "/api/v1/hosts",
            json={
                "connection_name": "steer-test-host",
                "host": "127.0.0.1",
                "port": 22,
                "username": "root",
                "auth_type": 1,
                "password": "pwd",
            },
        )
        assert resp.status_code == 201
        host_id = resp.json()["connection_id"]

        # 2. 发送即时插话请求
        steer_resp = await client.post(
            f"/api/v1/hosts/{host_id}/agent/react/steer",
            json={
                "session_id": "test-session-123",
                "content": "请先切换查看 iptables 防火墙策略",
            },
        )
        assert steer_resp.status_code == 200
        assert steer_resp.json()["status"] == "ok"
        assert steer_resp.json()["session_id"] == "test-session-123"



class MockDockerInstallProbeLLM(ILLMClient):
    """模拟用户请求'安装 docker'，第1步探测版本，第2步发现已安装立即收敛的模型"""
    def __init__(self):
        self.call_count = 0

    async def chat(self, messages: list, temperature: float = 0.2, tools=None) -> str:
        self.call_count += 1
        if self.call_count == 1:
            return "Thought: 检查 Docker 是否已安装及运行状态。\nAction: docker --version && systemctl is-active docker\nStep_Title: 探查 Docker 状态"
        return (
            "Thought: 前序探测命令 docker --version && systemctl is-active docker 的物理回显显示："
            "Docker version 26.1.4 已安装，服务状态为 active（正常运行）。目标已达成，立即收敛。\n"
            "Final_Answer: Docker version 26.1.4 已经安装且服务处于 active 正常运行状态，无需重复安装。"
        )

    async def stream_chat(self, messages: list, temperature: float = 0.2, tools=None):
        resp = await self.chat(messages, temperature=temperature, tools=tools)
        yield resp


class MockDockerGateway(ISSHGateway):
    async def execute(self, host: SSHConnection, command: Command) -> ExecutionResult:
        if "docker --version" in command.raw_command:
            return ExecutionResult(exit_status=0, stdout="Docker version 26.1.4, build 5650f9b\nactive\n", stderr="", duration_ms=10.0)
        return ExecutionResult(exit_status=0, stdout="ok", stderr="", duration_ms=10.0)

    async def test_connection(self, host: SSHConnection) -> bool:
        return True


@pytest.mark.asyncio
async def test_mutation_task_already_satisfied_not_false_failed():
    """验证安装类变更任务在探测到已安装/已就绪时，正常成功收敛，绝不误报'任务未达成'"""
    llm = MockDockerInstallProbeLLM()
    gateway = MockDockerGateway()
    host = SSHConnection(name="test", host="127.0.0.1", port=22, username="root", auth_type=AuthType.PASSWORD, password="pwd")

    engine = LangGraphReActEngine(llm_client=llm, ssh_gateway=gateway)
    graph = engine.build_graph(checkpointer=engine.checkpointer)

    initial_state = {
        "host": host,
        "user_prompt": "安装 docker 运行环境",
        "session_id": f"sess_docker_{uuid.uuid4().hex[:8]}",
        "max_steps": None,
        "step_idx": 1,
        "planned_steps_queue": [],
        "react_history": [],
        "executed_steps": [],
        "session_history": [],
        "has_encountered_error": False,
        "consecutive_errors": 0,
        "emitted_events": [],
    }

    events = []
    run_config = {"configurable": {"thread_id": initial_state["session_id"]}}
    async for mode, chunk in graph.astream(initial_state, config=run_config, stream_mode=["updates"]):
        for node_name, update in chunk.items():
            for ev in update.get("emitted_events", []):
                events.append(ev)

    # 1. 验证没有触发任何任务未达成的报错
    assert not any("任务未达成" in str(e.get("title", "")) for e in events)
    assert not any("系统运维变更任务尚未完全达成" in str(e.get("content", "")) for e in events)

    # 2. 验证正常完成
    completed_evs = [e for e in events if e.get("status") == "completed"]
    assert len(completed_evs) >= 1
    final_ev = completed_evs[-1]
    assert "Docker version 26.1.4" in final_ev.get("content", "")



class MockMemoryCitingLLM(ILLMClient):
    """模拟模型在第 1 步根据长期记忆直接收敛，且使用 tool_calls 的场景"""
    async def chat(self, messages: list, temperature: float = 0.2, tools=None) -> str:
        return {
            "content": "",
            "tool_calls": [
                {
                    "name": "complete_troubleshooting",
                    "arguments": {
                        "thought": "根据长期记忆，Docker version 26.1.4 已经安装且服务处于 active 正常运行状态。目标已达成，无需重复安装。",
                        "final_answer": "根据此前运维记忆确认：Docker 已经安装且处于 active 状态，无需重复安装。",
                    },
                }
            ],
        }

    async def stream_chat(self, messages: list, temperature: float = 0.2, tools=None):
        resp = await self.chat(messages, temperature=temperature, tools=tools)
        yield resp


@pytest.mark.asyncio
async def test_step1_memory_satisfied_and_plan_variable_defined():
    """验证第 1 步模型根据长期记忆直接收敛时：
    1. plan 局部变量不抛 UnboundLocalError 异常；
    2. 正常成功收敛，不被误杀为任务未达成。
    """
    llm = MockMemoryCitingLLM()
    gateway = MockDockerGateway()
    host = SSHConnection(name="test", host="127.0.0.1", port=22, username="root", auth_type=AuthType.PASSWORD, password="pwd")

    engine = LangGraphReActEngine(llm_client=llm, ssh_gateway=gateway)
    graph = engine.build_graph(checkpointer=engine.checkpointer)

    initial_state = {
        "host": host,
        "user_prompt": "安装 docker",
        "session_id": f"sess_mem_{uuid.uuid4().hex[:8]}",
        "max_steps": None,
        "step_idx": 1,
        "planned_steps_queue": [],
        "react_history": [],
        "executed_steps": [],
        "session_history": [],
        "has_encountered_error": False,
        "consecutive_errors": 0,
        "emitted_events": [],
    }

    events = []
    run_config = {"configurable": {"thread_id": initial_state["session_id"]}}
    # 必须平稳执行完成，绝不能抛出 UnboundLocalError: 'plan'
    async for mode, chunk in graph.astream(initial_state, config=run_config, stream_mode=["updates"]):
        for node_name, update in chunk.items():
            for ev in update.get("emitted_events", []):
                events.append(ev)

    assert not any("error" in str(e.get("type", "")) for e in events)
    completed_evs = [e for e in events if e.get("status") == "completed"]
    assert len(completed_evs) >= 1
    assert "Docker version 26.1.4" in completed_evs[0].get("content", "")


class MockPlanProposingLLM(ILLMClient):
    """模拟模型在第 1 步输出前置安装方案并提请用户确认"""
    async def chat(self, messages: list, temperature: float = 0.2, tools=None) -> str:
        return (
            "Thought: 目标主机 hadoop100 为 RPM 体系，需先探测系统版本、是否已安装 Docker 及包管理器可用性，风险可控可退。\n"
            "Step_Title: 方案确认\n"
            "Plan: 1. 探测系统版本与是否已安装 Docker 2. 配置阿里云镜像源 3. 执行安装并启动服务。是否按这个思路开始执行？"
        )

    async def stream_chat(self, messages: list, temperature: float = 0.2, tools=None):
        resp = await self.chat(messages, temperature=temperature, tools=tools)
        yield resp


@pytest.mark.asyncio
async def test_step1_mutation_plan_triggers_approval_card():
    """验证第 1 步方案推演包含“是否已安装”、“版本”等词汇时：
    1. 不会被 is_mutation_already_satisfied 误判为已完成收敛；
    2. 必须触发 approval_required 审批事件以弹窗卡片提请用户确认。
    """
    llm = MockPlanProposingLLM()
    gateway = MockDockerGateway()
    approval_mgr = InMemoryApprovalManager()
    host = SSHConnection(name="test", host="127.0.0.1", port=22, username="root", auth_type=AuthType.PASSWORD, password="pwd")

    engine = LangGraphReActEngine(llm_client=llm, ssh_gateway=gateway, approval_manager=approval_mgr, approval_timeout=0.1)
    graph = engine.build_graph(checkpointer=engine.checkpointer)

    session_id = f"sess_plan_{uuid.uuid4().hex[:8]}"
    initial_state = {
        "host": host,
        "user_prompt": "安装 docker",
        "session_id": session_id,
        "max_steps": None,
        "step_idx": 1,
        "is_plan_confirmed": False,
        "permission_mode": "request_approval",
        "planned_steps_queue": [],
        "react_history": [],
        "executed_steps": [],
        "session_history": [],
        "has_encountered_error": False,
        "consecutive_errors": 0,
        "emitted_events": [],
    }

    events = []
    run_config = {"configurable": {"thread_id": session_id}}
    async for mode, chunk in graph.astream(initial_state, config=run_config, stream_mode=["updates"]):
        for node_name, update in chunk.items():
            for ev in update.get("emitted_events", []):
                events.append(ev)

    # 验证是否正确发出了 approval_required 事件（前端据此弹出审批卡片）
    approval_evs = [e for e in events if e.get("type") == "approval_required"]
    assert len(approval_evs) >= 1
    assert approval_evs[0].get("title") == "方案确认"
    assert "探测系统版本" in approval_evs[0].get("plan", "")


class MockPlanViaToolCallLLM(ILLMClient):
    """模拟模型在第 1 步使用 complete_troubleshooting 工具调用给出前置安装方案"""
    async def chat(self, messages: list, temperature: float = 0.2, tools=None) -> str:
        return {
            "content": "",
            "tool_calls": [
                {
                    "name": "complete_troubleshooting",
                    "arguments": {
                        "thought": "目标主机 hadoop100 为 RPM 体系，当前任务为安装 Docker，属于系统状态变更操作。",
                        "final_answer": "需先探测系统版本、是否已安装 Docker 及包管理器可用性，再选择适配的安装源（阿里云镜像源以加速下载），最后验证服务运行状态。整个安装过程不涉及删除现有数据文件，风险主要为软件源配置变更与服务启用，可控可退。是否按这个思路开始执行？",
                    },
                }
            ],
        }

    async def stream_chat(self, messages: list, temperature: float = 0.2, tools=None):
        resp = await self.chat(messages, temperature=temperature, tools=tools)
        yield resp


@pytest.mark.asyncio
async def test_step1_tool_call_plan_triggers_approval_card():
    """验证第 1 步即便 LLM 误用 complete_troubleshooting 交付执行方案：
    1. 也能正确被识别为前置方案；
    2. 触发 approval_required 事件弹出卡片，而不是当作排障成功直接结束。
    """
    llm = MockPlanViaToolCallLLM()
    gateway = MockDockerGateway()
    approval_mgr = InMemoryApprovalManager()
    host = SSHConnection(name="test", host="127.0.0.1", port=22, username="root", auth_type=AuthType.PASSWORD, password="pwd")

    engine = LangGraphReActEngine(llm_client=llm, ssh_gateway=gateway, approval_manager=approval_mgr, approval_timeout=0.1)
    graph = engine.build_graph(checkpointer=engine.checkpointer)

    session_id = f"sess_tool_plan_{uuid.uuid4().hex[:8]}"
    initial_state = {
        "host": host,
        "user_prompt": "安装 docker",
        "session_id": session_id,
        "max_steps": None,
        "step_idx": 1,
        "is_plan_confirmed": False,
        "permission_mode": "request_approval",
        "planned_steps_queue": [],
        "react_history": [],
        "executed_steps": [],
        "session_history": [],
        "has_encountered_error": False,
        "consecutive_errors": 0,
        "emitted_events": [],
    }

    events = []
    run_config = {"configurable": {"thread_id": session_id}}
    async for mode, chunk in graph.astream(initial_state, config=run_config, stream_mode=["updates"]):
        for node_name, update in chunk.items():
            for ev in update.get("emitted_events", []):
                events.append(ev)

    approval_evs = [e for e in events if e.get("type") == "approval_required"]
    assert len(approval_evs) >= 1
    assert approval_evs[0].get("title") == "方案确认"
    assert "需先探测系统版本" in approval_evs[0].get("plan", "")


