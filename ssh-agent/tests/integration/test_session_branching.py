"""
会话分支与时间旅行自动化测试 (Phase 4: Session Branching & Checkpointing Tests)
验证：
1. 会话拓扑树管理器 (SessionTreeManager)
2. LangGraph 状态检查点与时间旅行回溯 (State Checkpoints & Time Travel)
3. 会话在历史步骤分叉出探索性新分支 (Session Branching & State Isolation)
4. ReActUseCase 业务用例中的分支创建事件与独立推演
"""

import pytest
from typing import List, Optional
import uuid

from adapter.repository.database import get_db_session
from adapter.repository import (
    SQLAlchemyChatRepository,
    SQLAlchemySSHConnectionRepository,
)
from application.ports import ILLMClient, ISSHGateway
from application.react.engine import LangGraphReActEngine
from application.runtime import SessionTreeManager
from application.usecases.react import ReActUseCase
from domain.entities import AuthType, Command, ExecutionResult, SSHConnection


class MockBranchingLLM(ILLMClient):
    def __init__(self, responses: Optional[List[str]] = None):
        self.call_count = 0
        self.recorded_prompts: List[str] = []
        self.responses = responses or [
            "Thought: 探测第 1 步。\nAction: df -h\nStep_Title: 检查挂载点",
            "Thought: 探测第 2 步。\nAction: free -m\nStep_Title: 检查内存",
            "Thought: 探测第 3 步。\nFinal_Answer: 所有指标均正常，排障结束。",
        ]

    async def chat(self, messages: list, temperature: float = 0.2, tools=None) -> str:
        for m in messages:
            if m.get("role") == "user":
                self.recorded_prompts.append(m.get("content", ""))
        idx = min(self.call_count, len(self.responses) - 1)
        resp = self.responses[idx]
        self.call_count += 1
        return resp

    async def stream_chat(self, messages: list):
        yield await self.chat(messages)


class RecordingSSHGateway(ISSHGateway):
    def __init__(self):
        self.executed_commands: List[str] = []

    async def execute(self, host: SSHConnection, command: Command) -> ExecutionResult:
        cmd_str = command.raw_command
        self.executed_commands.append(cmd_str)
        return ExecutionResult(stdout=f"Output of {cmd_str}", stderr="", exit_status=0)

    async def test_connection(self, host: SSHConnection) -> bool:
        return True


def test_session_tree_manager_register_and_tree():
    """测试会话树拓扑管理器：支持多层子分支注册与树形拓扑结构遍历"""
    mgr = SessionTreeManager()

    root_id = "root-sess"
    b1_id = "branch-1"
    b2_id = "branch-2"
    b1_sub_id = "branch-1-sub"

    mgr.register_session(root_id, title="生产环境排障主会话")
    mgr.register_branch(root_id, b1_id, branch_point_step=2, branch_title="分支1: 调整内核参数")
    mgr.register_branch(root_id, b2_id, branch_point_step=2, branch_title="分支2: 重启微服务")
    mgr.register_branch(b1_id, b1_sub_id, branch_point_step=3, branch_title="分支1子分支: 扩大连接池")

    branches = mgr.get_branches(root_id)
    assert len(branches) == 2
    branch_ids = [b["session_id"] for b in branches]
    assert b1_id in branch_ids
    assert b2_id in branch_ids

    tree = mgr.get_tree(root_id)
    assert tree["session_id"] == root_id
    assert len(tree["children"]) == 2

    # 验证子分支嵌套
    child1 = next(c for c in tree["children"] if c["session_id"] == b1_id)
    assert len(child1["children"]) == 1
    assert child1["children"][0]["session_id"] == b1_sub_id


def test_session_tree_manager_fork_state():
    """测试状态切片派生：精确截断执行历史，安全隔离并重置执行标记"""
    mgr = SessionTreeManager()

    parent_state = {
        "session_id": "parent-sess",
        "user_prompt": "排查 Nginx 502",
        "step_idx": 4,
        "executed_steps": [
            {"step": 1, "command": "systemctl status nginx", "analysis": "服务处于 active 状态"},
            {"step": 2, "command": "cat /etc/nginx/nginx.conf", "analysis": "配置语法正常"},
            {"step": 3, "command": "netstat -tulpn | grep 9000", "analysis": "后端 php-fpm 端口未监听"},
        ],
        "react_history": [
            "Step 1: ...",
            "Step 2: ...",
            "Step 3: ...",
        ],
        "final_answer": "php-fpm 宕机",
        "should_stop": True,
    }

    # 从第 2 步分叉（此时已知 nginx 正常，但尝试排查防火墙而不是 php-fpm）
    forked = mgr.fork_state_for_branch(
        parent_state=parent_state,
        branch_session_id="branch-iptables",
        branch_point_step=2,
        new_prompt="排查防火墙是否拦截了流量",
    )

    assert forked["session_id"] == "branch-iptables"
    assert forked["user_prompt"] == "排查防火墙是否拦截了流量"
    assert len(forked["executed_steps"]) == 2
    assert len(forked["react_history"]) == 2
    assert forked["step_idx"] == 3  # 下一步推进第 3 步
    assert forked["final_answer"] is None
    assert forked["should_stop"] is False
    assert forked["current_command"] is None


@pytest.mark.asyncio
async def test_langgraph_engine_checkpointing_and_time_travel():
    """测试 LangGraph 引擎检查点持久化与时间旅行查询能力"""
    llm = MockBranchingLLM(responses=[
        "Thought: 步骤 1 查看。\nAction: df -h\nStep_Title: 检查1",
        "Thought: 步骤 2 确认。\nFinal_Answer: 磁盘充足，完成。",
    ])
    gateway = RecordingSSHGateway()
    engine = LangGraphReActEngine(llm_client=llm, ssh_gateway=gateway)

    host = SSHConnection(name="test", host="127.0.0.1", port=22, username="root", auth_type=AuthType.PASSWORD, password="pwd")
    session_id = f"tt_sess_{uuid.uuid4().hex[:8]}"

    initial_state = {
        "host": host,
        "user_prompt": "系统巡检",
        "session_id": session_id,
        "max_steps": 3,
        "step_idx": 1,
        "planned_steps_queue": [],
        "react_history": [],
        "executed_steps": [],
        "session_history": [],
        "has_encountered_error": False,
        "consecutive_errors": 0,
        "is_blocked": False,
        "final_answer": None,
        "final_conclusion": None,
        "is_success": True,
        "should_stop": False,
        "emitted_events": [],
    }

    graph = engine.build_graph(checkpointer=engine.checkpointer)
    config = {"configurable": {"thread_id": session_id}}

    async for chunk in graph.astream(initial_state, config=config, stream_mode="updates"):
        pass

    # 1. 查询时间旅行检查点列表
    checkpoints = await engine.get_session_checkpoints(session_id)
    assert len(checkpoints) >= 3

    # 验证检查点包含物理命令与推演字段
    cmd_checkpoints = [cp for cp in checkpoints if cp.get("command") == "df -h"]
    assert len(cmd_checkpoints) >= 1
    assert cmd_checkpoints[0]["title"] == "检查1"

    # 2. 查询特定步骤完成时的状态快照
    step_1_state = await engine.get_step_state(session_id, step_idx=1)
    assert step_1_state is not None
    assert len(step_1_state["executed_steps"]) == 1
    assert step_1_state["executed_steps"][0]["command"] == "df -h"


@pytest.mark.asyncio
async def test_langgraph_engine_fork_branch_independence():
    """测试通过 fork_branch 分叉新会话，新分支与原会话推演完全隔离不干扰"""
    llm = MockBranchingLLM(responses=[
        "Thought: 步骤 1 查端口。\nAction: ss -tulpn\nStep_Title: 查看端口",
        "Thought: 步骤 2 查日志。\nAction: journalctl -n 20\nStep_Title: 查看日志",
        "Thought: 步骤 3 完成。\nFinal_Answer: 巡检完成。",
    ])
    gateway = RecordingSSHGateway()
    engine = LangGraphReActEngine(llm_client=llm, ssh_gateway=gateway)

    host = SSHConnection(name="test", host="127.0.0.1", port=22, username="root", auth_type=AuthType.PASSWORD, password="pwd")
    parent_sid = f"parent_{uuid.uuid4().hex[:8]}"

    initial_state = {
        "host": host,
        "user_prompt": "端口排查",
        "session_id": parent_sid,
        "max_steps": 3,
        "step_idx": 1,
        "planned_steps_queue": [],
        "react_history": [],
        "executed_steps": [],
        "session_history": [],
        "has_encountered_error": False,
        "consecutive_errors": 0,
        "is_blocked": False,
        "final_answer": None,
        "final_conclusion": None,
        "is_success": True,
        "should_stop": False,
        "emitted_events": [],
    }

    graph = engine.build_graph(checkpointer=engine.checkpointer)
    # 执行主会话
    async for _ in graph.astream(initial_state, config={"configurable": {"thread_id": parent_sid}}, stream_mode="updates"):
        pass

    # 从第 1 步分叉出新分支探索
    branch_sid, forked_state = await engine.fork_branch(
        parent_session_id=parent_sid,
        branch_point_step=1,
        branch_title="分支: 调整防火墙策略",
        new_prompt="尝试排查 iptables 规则",
    )

    assert branch_sid != parent_sid
    assert len(forked_state["executed_steps"]) == 1
    assert forked_state["user_prompt"] == "尝试排查 iptables 规则"

    # 执行新分支
    branch_llm = MockBranchingLLM(responses=[
        "Thought: 分支直接查看 iptables。\nAction: iptables -L -n\nStep_Title: 查看防火墙",
        "Thought: 防火墙已确认无阻断。\nFinal_Answer: 防火墙放行正常。",
    ])
    branch_engine = LangGraphReActEngine(llm_client=branch_llm, ssh_gateway=gateway)
    branch_graph = branch_engine.build_graph(checkpointer=branch_engine.checkpointer)

    async for _ in branch_graph.astream(forked_state, config={"configurable": {"thread_id": branch_sid}}, stream_mode="updates"):
        pass

    # 验证分支推演不会改变父会话检查点与执行历史
    parent_history = await engine.get_session_checkpoints(parent_sid)
    branch_history = await branch_engine.get_session_checkpoints(branch_sid)

    assert any(cp.get("command") == "journalctl -n 20" for cp in parent_history)
    assert not any(cp.get("command") == "journalctl -n 20" for cp in branch_history)
    assert any(cp.get("command") == "iptables -L -n" for cp in branch_history)


@pytest.mark.asyncio
async def test_react_use_case_branching_end_to_end():
    """测试 ReActUseCase 业务用例中的分支创建事件、状态继承与拓扑树生成"""
    async for session in get_db_session():
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)

        host = SSHConnection(
            name="branch-host",
            host="192.168.1.108",
            port=22,
            username="root",
            auth_type=AuthType.PASSWORD,
            password="pwd",
        )
        saved_host = await host_repo.save(host)

        parent_sid = f"uc_parent_{uuid.uuid4().hex[:8]}"
        llm = MockBranchingLLM(responses=[
            "Thought: 第一步检查磁盘。\nAction: df -h\nStep_Title: 步骤1",
            "Thought: 第二步检查内存。\nAction: free -m\nStep_Title: 步骤2",
            "Thought: 主线完成。\nFinal_Answer: 主线排查完毕。",
        ])
        gateway = RecordingSSHGateway()
        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=llm,
            session_repo=chat_repo,
            ssh_gateway=gateway,
        )

        # 1. 运行主会话
        events_parent = []
        async for ev in uc.execute(saved_host.connection_id, "主线系统排查", session_id=parent_sid, max_steps=3):
            events_parent.append(ev)

        assert any(e.get("type") == "finish" for e in events_parent)

        # 2. 从主会话第 1 步派生出新分支
        branch_events = []
        branch_sid = f"uc_branch_{uuid.uuid4().hex[:8]}"
        async for ev in uc.execute(
            identifier=saved_host.connection_id,
            user_prompt="分支方向: 重点分析磁盘 inode 使用率",
            session_id=branch_sid,
            branch_from_session_id=parent_sid,
            branch_step_idx=1,
            branch_title="分支: inode 分析",
            max_steps=2,
        ):
            branch_events.append(ev)

        # 验证产出 branch_created 事件
        branch_evs = [e for e in branch_events if e.get("type") == "branch_created"]
        assert len(branch_evs) == 1
        assert branch_evs[0]["parent_session_id"] == parent_sid
        assert branch_evs[0]["branch_step"] == 1

        # 3. 验证树形拓扑结构
        tree = uc.get_session_tree(parent_sid)
        assert tree["session_id"] == parent_sid
        assert len(tree["children"]) >= 1
        assert tree["children"][0]["session_id"] == branch_sid



@pytest.mark.asyncio
async def test_session_checkpoint_info_dataclass():
    """验证 SessionCheckpointInfo 实体模型及字典互转与双向 title 兼容"""
    from application.runtime import SessionCheckpointInfo

    info = SessionCheckpointInfo(
        checkpoint_id="cp-123",
        session_id="sess-abc",
        step_idx=2,
        node_name="act_node",
        node_next=["observe_node"],
        step_title="检查内存",
        command="free -m",
        thought="查看可用内存",
        observation="Mem: 16G",
        reflection="内存充足",
        is_normal=True,
        executed_steps_count=1,
        has_final_answer=False,
    )
    d = info.to_dict()
    assert d["checkpoint_id"] == "cp-123"
    assert d["step_title"] == "检查内存"
    assert d["title"] == "检查内存"
    assert d["command"] == "free -m"
    assert d["node_next"] == ["observe_node"]


@pytest.mark.asyncio
async def test_record_ssh_session_log_use_case():
    """验证 RecordSSHSessionLogUseCase 的 open_session 与 close_session 业务流"""
    from adapter.repository import SQLAlchemySSHSessionLogRepository
    from application.usecases.ssh_session import RecordSSHSessionLogUseCase

    async for db_session in get_db_session():
        repo = SQLAlchemySSHSessionLogRepository(db_session)
        use_case = RecordSSHSessionLogUseCase(session_log_repo=repo)

        # 1. 开启终端审计会话
        log = await use_case.open_session(
            connection_id="conn-test-01",
            remote_addr="192.168.1.50",
            user_id="admin",
        )
        assert log.session_id is not None
        assert log.status == 0
        assert log.remote_addr == "192.168.1.50"

        # 2. 正常关闭终端审计会话
        closed = await use_case.close_session(log.session_id)
        assert closed is not None
        assert closed.status == 1

        # 3. 异常关闭终端审计会话
        log_err = await use_case.open_session(connection_id="conn-test-02")
        closed_err = await use_case.close_session(log_err.session_id, error_msg="Connection timed out")
        assert closed_err is not None
        assert closed_err.status == 1
        assert closed_err.error_msg == "Connection timed out"
        break
