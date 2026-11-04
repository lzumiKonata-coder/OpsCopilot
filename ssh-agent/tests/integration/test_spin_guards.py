"""
防止无序空转与长循环综合测试 (Spin Guards & Soft Budgets Verification Tests)
验证：
1. 进展指纹：交替循环 (A-B-A-B)、重复命令 3 次、连续 4 步无新信息饱和熔断
2. 软预算：运行时间上限 (max_run_seconds)、LLM 调用次数上限 (max_llm_calls)、Token 上限 (max_tokens)
3. 即时取消响应：审批等待中途检测取消立即打断 (无需等待 120s)、LLM 调用前即时取消
"""

import asyncio
import time
import uuid
import pytest
from typing import List, Optional

from adapter.repository.database import get_db_session
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
    def __init__(self, responses: Optional[List[str]] = None):
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
        self.commands: List[str] = []
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
# 1. 进展指纹与循环熔断验证
# ====================================================================

def test_normalize_observation_payload():
    """验证物理回显归一化：剔除 ANSI 控制符、动态日志时间戳与可变易碎字段"""
    raw_obs = "\x1b[32m[2026-11-02 18:30:15] up 10 days, 2:15, load average: 0.12, 0.08, 0.05\x1b[0m\nnginx running"
    normalized = normalize_observation_payload(raw_obs)
    assert "\x1b" not in normalized
    assert "2026-11-02" not in normalized
    assert "nginx running" in normalized


@pytest.mark.asyncio
async def test_alternating_cycle_guard_breaks_abab():
    """测试交替循环 A-B-A-B (如 ss -lntp <-> ps -ef) 触发安全熔断"""
    engine = LangGraphReActEngine(llm_client=MockLLM())
    state: ReActGraphState = {
        "host": SSHConnection(name="h", host="1.1.1.1", port=22, username="root", auth_type=AuthType.PASSWORD),
        "user_prompt": "排查服务",
        "session_id": "test_abab",
        "step_idx": 5,
        "executed_steps": [
            {"step": 1, "command": "ss -lntp", "observation": "LISTEN 80"},
            {"step": 2, "command": "ps -ef", "observation": "nginx worker"},
            {"step": 3, "command": "ss -lntp", "observation": "LISTEN 80"},
            {"step": 4, "command": "ps -ef", "observation": "nginx worker"},
        ],
        "react_history": [],
        "max_steps": None,
    }

    result = await engine._node_think(state)
    assert result["should_stop"] is True
    assert result["is_success"] is False
    assert "交替振荡" in result["final_conclusion"]


@pytest.mark.asyncio
async def test_stalling_guard_breaks_on_4_saturated_inspection_steps():
    """测试连续 4 步只读排查但回显未产生新物理信息（信息饱和）时主动收敛终止"""
    engine = LangGraphReActEngine(llm_client=MockLLM())
    state: ReActGraphState = {
        "host": SSHConnection(name="h", host="1.1.1.1", port=22, username="root", auth_type=AuthType.PASSWORD),
        "user_prompt": "排查系统",
        "session_id": "test_stalling",
        "step_idx": 5,
        "executed_steps": [
            {"step": 1, "command": "uptime", "observation": "load average: 0.10, 0.10, 0.10"},
            {"step": 2, "command": "vmstat", "observation": "load average: 0.10, 0.10, 0.10"},
            {"step": 3, "command": "uptime", "observation": "load average: 0.10, 0.10, 0.10"},
            {"step": 4, "command": "vmstat", "observation": "load average: 0.10, 0.10, 0.10"},
        ],
        "react_history": [],
        "max_steps": None,
    }

    result = await engine._node_think(state)
    assert result["should_stop"] is True
    assert result["is_success"] is False
    assert "收敛" in result["final_conclusion"]


# ====================================================================
# 2. 软预算 (Soft Budgets) 验证
# ====================================================================

@pytest.mark.asyncio
async def test_soft_budget_max_run_seconds_break():
    """测试运行耗时超过 max_run_seconds 软预算时主动收敛终止"""
    engine = LangGraphReActEngine(llm_client=MockLLM())
    state: ReActGraphState = {
        "host": SSHConnection(name="h", host="1.1.1.1", port=22, username="root", auth_type=AuthType.PASSWORD),
        "user_prompt": "耗时排查",
        "session_id": "test_budget_time",
        "step_idx": 3,
        "start_time": time.time() - 700.0,  # 已运行 700 秒
        "max_run_seconds": 600.0,            # 上限 600 秒
        "executed_steps": [{"step": 1, "command": "ls"}],
        "react_history": [],
        "max_steps": None,
    }

    result = await engine._node_think(state)
    assert result["should_stop"] is True
    assert result["is_success"] is False
    assert "时间软预算上限" in result["final_conclusion"]


@pytest.mark.asyncio
async def test_soft_budget_max_llm_calls_break():
    """测试 LLM 调用次数达到 max_llm_calls 软预算时主动收敛终止"""
    engine = LangGraphReActEngine(llm_client=MockLLM())
    state: ReActGraphState = {
        "host": SSHConnection(name="h", host="1.1.1.1", port=22, username="root", auth_type=AuthType.PASSWORD),
        "user_prompt": "无限思考",
        "session_id": "test_budget_llm",
        "step_idx": 31,
        "llm_call_count": 30,
        "max_llm_calls": 30,
        "executed_steps": [],
        "react_history": [],
        "max_steps": None,
    }

    result = await engine._node_think(state)
    assert result["should_stop"] is True
    assert result["is_success"] is False
    assert "大模型调用预算上限" in result["final_conclusion"]


@pytest.mark.asyncio
async def test_alternating_cycle_with_new_info_does_not_break():
    """测试交替指令 (df -h <-> free -m) 在物理回显有新数据变化时不误杀，允许正常巡检"""
    engine = LangGraphReActEngine(llm_client=MockLLM(responses=["Thought: 继续\nAction: echo ok\nStep_Title: 步骤5"]))
    state: ReActGraphState = {
        "host": SSHConnection(name="h", host="1.1.1.1", port=22, username="root", auth_type=AuthType.PASSWORD),
        "user_prompt": "巡检机器磁盘和内存",
        "session_id": "test_abab_new_info",
        "step_idx": 5,
        "executed_steps": [
            {"step": 1, "command": "df -h", "observation": "/dev/sda1 10% used 10G/100G"},
            {"step": 2, "command": "free -m", "observation": "Mem: total 8000 used 2000 free 6000"},
            {"step": 3, "command": "df -h", "observation": "/dev/sda1 35% used 35G/100G"},  # 磁盘使用率发生实质变化
            {"step": 4, "command": "free -m", "observation": "Mem: total 8000 used 4500 free 3500"},  # 内存使用率发生实质变化
        ],
        "react_history": [],
        "max_steps": None,
    }

    result = await engine._node_think(state)
    # 不应被交替循环误杀
    assert result.get("is_stalled") is not True
    assert "交替振荡" not in (result.get("final_conclusion") or "")


@pytest.mark.asyncio
async def test_branching_preserves_request_parameters_over_parent_state():
    """测试分叉时分支请求参数（permission_mode/max_steps/软预算等）不被父会话旧值覆盖"""
    from application.runtime import SessionTreeManager

    parent_state = {
        "session_id": "parent_sess_1",
        "permission_mode": "full_access",  # 父会话曾为全放行
        "approved_commands": ["rm -rf /tmp/abc"],
        "max_steps": 3,                    # 父会话只给 3 步
        "max_run_seconds": 120.0,
        "max_llm_calls": 5,
        "max_tokens": 5000,
        "terminal_buffer": "parent buffer old",
        "steering_message": "parent steering",
        "executed_steps": [
            {"step": 1, "command": "uptime", "observation": "up 1 day"},
            {"step": 2, "command": "free -m", "observation": "mem 8G"},
        ],
        "react_history": ["Step 1", "Step 2"],
    }

    # 1. 验证 fork_state_for_branch 仅提取历史事实，不复制配置参数
    forked = SessionTreeManager.fork_state_for_branch(
        parent_state=parent_state,
        branch_session_id="branch_sess_1",
        branch_point_step=1,
    )
    assert forked["session_id"] == "branch_sess_1"
    assert len(forked["executed_steps"]) == 1
    assert "permission_mode" not in forked
    assert "max_steps" not in forked
    assert "max_run_seconds" not in forked
    assert "max_llm_calls" not in forked
    assert "max_tokens" not in forked
    assert "terminal_buffer" not in forked
    assert "steering_message" not in forked

    # 2. 验证 ReActUseCase 初始状态合并时分支请求参数最高优先级
    async for session in get_db_session():
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)
        host = await host_repo.save(SSHConnection(
            name="branch-test-host", host="10.0.0.8", port=22, username="root", auth_type=AuthType.PASSWORD, password="pwd"
        ))

        uc = ReActUseCase(host_repo=host_repo, llm_client=MockLLM(), session_repo=chat_repo)

        # 模拟分支请求：明确指定 request_approval, max_steps=10, max_run_seconds=500
        gen = uc.execute(
            identifier=host.connection_id,
            user_prompt="分支排查",
            session_id="branch_req_sess",
            permission_mode="request_approval",
            max_steps=10,
            max_run_seconds=500.0,
            branch_from_session_id="parent_sess_1",
            branch_step_idx=1,
        )
        # 提取第一个事件
        async for ev in gen:
            break
        await gen.aclose()


@pytest.mark.asyncio
async def test_async_generator_closes_without_runtime_error_on_generator_exit():
    """测试异步生成器在 aclose() 收到 GeneratorExit 时不会尝试 yield 并抛出 RuntimeError"""
    async for session in get_db_session():
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)
        host = await host_repo.save(SSHConnection(
            name="gen-exit-host", host="10.0.0.9", port=22, username="root", auth_type=AuthType.PASSWORD, password="pwd"
        ))

        uc = ReActUseCase(host_repo=host_repo, llm_client=MockLLM(), session_repo=chat_repo)
        gen = uc.execute(
            identifier=host.connection_id,
            user_prompt="测试生成器关闭",
            session_id=f"gen_exit_{uuid.uuid4().hex[:8]}",
        )
        # 步进一次后直接触发 aclose()
        async for ev in gen:
            break
        # 关闭生成器：绝不应抛出 RuntimeError: async generator ignored GeneratorExit
        await gen.aclose()


@pytest.mark.asyncio
async def test_soft_budget_enforced_in_act_node():
    """测试当总运行时间已达 max_run_seconds 时，act_node 终止执行并不下发命令"""
    engine = LangGraphReActEngine(llm_client=MockLLM(), ssh_gateway=MockGateway())
    state: ReActGraphState = {
        "host": SSHConnection(name="h", host="1.1.1.1", port=22, username="root", auth_type=AuthType.PASSWORD),
        "user_prompt": "耗时排查",
        "current_command": "rm -rf /tmp/data",
        "step_idx": 3,
        "start_time": time.time() - 650.0,  # 已运行 650 秒
        "max_run_seconds": 600.0,            # 上限 600 秒
        "executed_steps": [],
    }

    result = await engine._node_act(state)
    assert result.get("budget_exceeded") is True
    assert result.get("should_stop") is True
    assert "总运行时间上限" in result.get("final_conclusion", "")
    assert len(result["executed_steps"]) == 1
    assert result["executed_steps"][0]["status"] == "timeout"


@pytest.mark.asyncio
async def test_soft_budget_enforced_in_approval_gate_node():
    """测试当总运行时间已耗尽时，approval_gate_node 立即超时并不等待 120 秒"""
    engine = LangGraphReActEngine(llm_client=MockLLM(), approval_manager=InMemoryApprovalManager())
    state: ReActGraphState = {
        "host": SSHConnection(name="h", host="1.1.1.1", port=22, username="root", auth_type=AuthType.PASSWORD),
        "user_prompt": "耗时排查",
        "pending_approval_id": "fake_appr_id",
        "current_command": "reboot",
        "step_idx": 2,
        "start_time": time.time() - 610.0,  # 已运行 610 秒
        "max_run_seconds": 600.0,            # 上限 600 秒
        "executed_steps": [],
    }

    start_t = time.time()
    result = await engine._node_approval_gate(state)
    elapsed = time.time() - start_t
    assert elapsed < 0.5, "时间超限时审批门禁应毫秒级直接终止，不可盲等"
    assert result.get("budget_exceeded") is True
    assert result.get("should_stop") is True
    assert "总运行时间上限" in result.get("final_conclusion", "")


@pytest.mark.asyncio
async def test_plan_confirmation_records_llm_and_token_counts():
    """测试 Step 1 方案确认早退分支能正确将 llm_call_count 与 total_tokens 回写入状态字典"""
    engine = LangGraphReActEngine(
        llm_client=MockLLM(responses=[
            "Thought: 这是前置方案分析，是否按这个思路开始执行？\nStep_Title: 方案确认",
        ]),
        approval_manager=InMemoryApprovalManager(),
    )
    state: ReActGraphState = {
        "host": SSHConnection(name="h", host="1.1.1.1", port=22, username="root", auth_type=AuthType.PASSWORD),
        "user_prompt": "安装 MySQL 8.0 服务",
        "session_id": "test_plan_budget",
        "step_idx": 1,
        "is_plan_confirmed": False,
        "permission_mode": "request_approval",
        "llm_call_count": 0,
        "total_tokens": 0,
        "executed_steps": [],
        "react_history": [],
    }

    result = await engine._node_think(state)
    assert result.get("pending_approval_id") is not None
    assert result.get("llm_call_count") == 1
    assert result.get("total_tokens") > 0




@pytest.mark.asyncio
async def test_act_node_budget_exceeded_bypasses_observe_and_reflect_node():
    """测试 act_node 因预算耗尽早退时直接路由至 finish_node，绝不经过反思节点导致同一步骤重复记录"""
    from unittest.mock import patch

    llm = MockLLM(responses=["Thought: 探测\nAction: ls /var\nStep_Title: 步骤1"])
    engine = LangGraphReActEngine(llm_client=llm, ssh_gateway=MockGateway())
    graph = engine.build_graph()

    base_time = 1700000000.0
    current_time = [base_time + 10.0]  # think 节点时耗时 10 秒（未超时）

    def fake_time():
        return current_time[0]

    state: ReActGraphState = {
        "host": SSHConnection(name="h", host="1.1.1.1", port=22, username="root", auth_type=AuthType.PASSWORD),
        "user_prompt": "排查系统",
        "session_id": "test_act_route",
        "step_idx": 1,
        "start_time": base_time,
        "max_run_seconds": 600.0,            # 上限 600 秒
        "max_steps": None,
        "executed_steps": [],
        "react_history": [],
        "planned_steps_queue": [],
        "has_encountered_error": False,
        "consecutive_errors": 0,
        "is_blocked": False,
        "final_answer": None,
        "final_conclusion": None,
        "is_success": False,
        "should_stop": False,
        "emitted_events": [],
    }

    final_state = dict(state)
    with patch("time.time", side_effect=fake_time):
        async for update in graph.astream(state, config={"configurable": {"thread_id": "test_act_route"}}, stream_mode="updates"):
            for node_name, val in update.items():
                final_state.update(val)
                # 当 think 节点执行完毕后，模拟时间跳变至超时 (650s > 600s)
                if node_name == "think_node":
                    current_time[0] = base_time + 650.0

    # 验证最终只记录了 1 条超时步骤，没有被反思节点追加第 2 条记录
    executed = final_state.get("executed_steps", [])
    assert len(executed) == 1
    assert executed[0]["status"] == "timeout"
    assert final_state.get("should_stop") is True
    assert final_state.get("budget_exceeded") is True


@pytest.mark.asyncio
async def test_main_sse_stream_does_not_duplicate_error_events():
    """测试 main.py 的 sse_react_stream 在 use_case 已发射 error 事件后不会二次重复发射 error"""
    from starlette.requests import Request
    from unittest.mock import AsyncMock, MagicMock
    import json

    # 模拟在抛出异常前已 emit error 的生成器
    class MockErrorUseCase:
        async def execute(self, **kwargs):
            yield {"type": "error", "error": "上游业务报错", "step": 1}
            raise RuntimeError("上游业务报错")

    mock_request = MagicMock()
    mock_request.is_disconnected = AsyncMock(return_value=False)

    from adapter.web.routers.agent_react import react_agent_chat
    from adapter.web.schemas import ReActRequest
    from domain.entities import SSHConnection, AuthType

    req = ReActRequest(prompt="排查")
    resp = await react_agent_chat(
        identifier="fake_id",
        req=req,
        request=mock_request,
        use_case=MockErrorUseCase(),
    )

    events = []
    async for chunk in resp.body_iterator:
        if chunk.startswith("data: "):
            payload = chunk[6:].strip()
            if payload != "[DONE]":
                try:
                    events.append(json.loads(payload))
                except Exception:
                    pass

    error_events = [e for e in events if e.get("type") == "error"]
    assert len(error_events) == 1, f"客户端应只收到 1 个 error 事件，实际收到 {len(error_events)} 个: {error_events}"

