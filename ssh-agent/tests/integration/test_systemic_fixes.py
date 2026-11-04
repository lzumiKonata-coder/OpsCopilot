"""
系统性缺陷修复综合验证测试 (Systemic Fixes Verification Tests)
验证 6 大系统性问题修复：
1. 取消、异常、阻断的收尾语义
2. 分支状态注入与跨请求检查点持久化
3. 审批拒绝/超时/FORBIDDEN 记录补全至 executed_steps
4. 长期记忆的主机隔离与累积存储
5. 工具执行容错（非整型 offset/limit、长超时回退、重试工具适配）
6. 诊断接口客观性与 is_normal 真实判定
"""

import pytest
import uuid
from typing import List, Optional

from adapter.repository.database import get_db_session
from adapter.repository import (
    SQLAlchemyChatRepository,
    SQLAlchemyMemoryRepository,
    SQLAlchemySSHConnectionRepository,
)
from adapter.tools.builtin import build_default_registry
from langgraph.checkpoint.memory import MemorySaver
from application.ports import (
    ICommandAuditor,
    ILLMClient,
    ISSHGateway,
)
from domain.entities import (
    AuditDecision,
    AuthType,
    Command,
    ExecutionResult,
    LongTermMemory,
    SSHConnection,
)
from domain.rules import RiskLevel
from application.react.engine import LangGraphReActEngine
from application.react.managers import InMemoryApprovalManager
from application.react.observation import detect_command_error
from application.react.state import ReActGraphState
from application.usecases.memory import _load_long_term_memory_text
from application.usecases.react import ReActUseCase


class MockLLM(ILLMClient):
    def __init__(self, responses: Optional[List[str]] = None, tool_calls: Optional[List[dict]] = None):
        self.call_count = 0
        self.responses = responses or ["Thought: 排查\nAction: uptime\nStep_Title: 步骤1"]
        self.tool_calls = tool_calls or []

    async def chat(self, messages: list, temperature: float = 0.2, tools=None) -> str:
        idx = min(self.call_count, len(self.responses) - 1)
        resp = self.responses[idx]
        self.call_count += 1
        return resp

    async def stream_chat(self, messages: list):
        yield await self.chat(messages)


class MockGateway(ISSHGateway):
    def __init__(self):
        self.commands: List[str] = []

    async def execute(self, host: SSHConnection, command: Command) -> ExecutionResult:
        self.commands.append(command.raw_command)
        return ExecutionResult(stdout=f"ok: {command.raw_command}", stderr="", exit_status=0)

    async def read_file(self, host: SSHConnection, path: str, offset: int = 1, limit: int = 100) -> str:
        return f"file content of {path} lines {offset}..{offset+limit}"

    async def write_file(self, host: SSHConnection, path: str, content: str) -> bool:
        return True

    async def edit_file(self, host: SSHConnection, path: str, target: str, replacement: str) -> bool:
        return True

    async def test_connection(self, host: SSHConnection) -> bool:
        return True


# ====================================================================
# 1. 取消、异常、阻断收尾语义验证
# ====================================================================

@pytest.mark.asyncio
async def test_react_cancellation_emits_event_and_marks_status():
    """测试客户端主动取消时，发射 cancelled 事件，会话报告记录为【用户取消】而非【执行成功】"""
    async for session in get_db_session():
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)

        host = SSHConnection(
            name="cancel-host",
            host="10.0.0.1",
            port=22,
            username="root",
            auth_type=AuthType.PASSWORD,
            password="pwd",
        )
        saved_host = await host_repo.save(host)

        llm = MockLLM(responses=[
            "Thought: 探测第1步\nAction: uptime\nStep_Title: 步骤1",
            "Thought: 探测第2步\nAction: free -m\nStep_Title: 步骤2",
        ])
        gateway = MockGateway()
        uc = ReActUseCase(host_repo=host_repo, llm_client=llm, session_repo=chat_repo, ssh_gateway=gateway)

        # 模拟在 step 1 执行后客户端断开
        call_step = 0
        async def mock_cancel_check():
            nonlocal call_step
            call_step += 1
            return call_step >= 2

        events = []
        sess_id = f"cancel_sess_{uuid.uuid4().hex[:8]}"
        async for ev in uc.execute(
            identifier=saved_host.connection_id,
            user_prompt="系统排障",
            session_id=sess_id,
            cancel_check=mock_cancel_check,
            max_steps=5,
        ):
            events.append(ev)

        # 验证发射了 cancelled 事件
        cancelled_ev = [e for e in events if e.get("type") == "cancelled"]
        assert len(cancelled_ev) >= 1
        assert "用户主动取消" in cancelled_ev[0].get("reason", "")

        # 验证落库会话记录状态为【用户取消】
        saved_session = await chat_repo.get_by_id(sess_id)
        assert saved_session is not None
        assistant_msgs = [m for m in saved_session.messages if m.role == "assistant"]
        assert len(assistant_msgs) >= 1
        assert "【用户取消】" in assistant_msgs[-1].content
        assert "【执行成功】" not in assistant_msgs[-1].content


@pytest.mark.asyncio
async def test_node_finish_blocked_conclusion():
    """测试命中安全阻断 (is_blocked) 时，finish_node 输出明确的阻断提示，而不是误导的最大步数限制"""
    engine = LangGraphReActEngine(llm_client=MockLLM())
    state: ReActGraphState = {
        "host": SSHConnection(name="h", host="1.1.1.1", port=22, username="root", auth_type=AuthType.PASSWORD),
        "user_prompt": "删除根目录",
        "session_id": "test_block",
        "max_steps": 10,
        "step_idx": 1,
        "executed_steps": [],
        "react_history": [],
        "is_blocked": True,
        "blocked_reason": "命中古典 rm -rf / 规则拦截",
        "has_encountered_error": True,
        "last_error_detail": "FORBIDDEN",
        "is_success": False,
        "final_conclusion": None,
        "final_answer": None,
    }

    result = await engine._node_finish(state)
    assert result["is_success"] is False
    assert "[安全拦截/阻断]" in result["final_conclusion"]
    assert "已推进至最大步数限制" not in result["final_conclusion"]


# ====================================================================
# 2. 分支状态注入与跨请求持久化
# ====================================================================

@pytest.mark.asyncio
async def test_branching_state_properly_injected_into_initial_state():
    """测试从父会话分叉时，父会话的 executed_steps 和 react_history 被正确注入初始状态，避免重头执行"""
    async for session in get_db_session():
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)

        host = SSHConnection(
            name="branch-inject-host",
            host="10.0.0.2",
            port=22,
            username="root",
            auth_type=AuthType.PASSWORD,
            password="pwd",
        )
        saved_host = await host_repo.save(host)

        parent_sid = f"p_sess_{uuid.uuid4().hex[:8]}"
        llm = MockLLM(responses=[
            "Thought: 查磁盘\nAction: df -h\nStep_Title: 检查磁盘",
            "Thought: 查内存\nAction: free -m\nStep_Title: 检查内存",
            "Thought: 总结\nFinal_Answer: 主线完成",
        ])
        gateway = MockGateway()
        shared_checkpointer = MemorySaver()
        engine_main = LangGraphReActEngine(
            llm_client=llm,
            ssh_gateway=gateway,
            checkpointer=shared_checkpointer,
        )
        uc1 = ReActUseCase(
            host_repo=host_repo,
            llm_client=llm,
            session_repo=chat_repo,
            ssh_gateway=gateway,
            engine=engine_main,
        )

        # 运行主会话
        async for _ in uc1.execute(saved_host.connection_id, "主线任务", session_id=parent_sid, max_steps=3):
            pass

        # 新建 ReActUseCase 模拟另一个 HTTP 请求，共享 checkpointer
        uc2 = ReActUseCase(
            host_repo=host_repo,
            llm_client=llm,
            session_repo=chat_repo,
            ssh_gateway=gateway,
            engine=engine_main,
        )
        cps = await uc2.get_session_checkpoints(parent_sid)
        assert len(cps) > 0, "跨实例应当可查询到共享 checkpointer 的检查点"

        # 分叉：从第 1 步切出
        branch_sid = f"b_sess_{uuid.uuid4().hex[:8]}"
        llm_branch = MockLLM(responses=[
            "Thought: 分支直接推进下一步\nFinal_Answer: 分支完成",
        ])
        engine_branch = LangGraphReActEngine(
            llm_client=llm_branch,
            ssh_gateway=gateway,
            checkpointer=shared_checkpointer,
        )
        uc_branch = ReActUseCase(
            host_repo=host_repo,
            llm_client=llm_branch,
            session_repo=chat_repo,
            ssh_gateway=gateway,
            engine=engine_branch,
        )

        branch_events = []
        async for ev in uc_branch.execute(
            identifier=saved_host.connection_id,
            user_prompt="分支探测",
            session_id=branch_sid,
            branch_from_session_id=parent_sid,
            branch_step_idx=1,
            max_steps=2,
        ):
            branch_events.append(ev)

        # 验证分支创建事件
        assert any(e.get("type") == "branch_created" for e in branch_events)
        # 验证落库会话报告中保留了父会话第 1 步的记录
        saved_branch = await chat_repo.get_by_id(branch_sid)
        assert saved_branch is not None
        asst = [m for m in saved_branch.messages if m.role == "assistant"][-1]
        assert "检查磁盘" in asst.content or "df -h" in asst.content


# ====================================================================
# 3. 审批拒绝/超时/FORBIDDEN 记录补全至 executed_steps
# ====================================================================

@pytest.mark.asyncio
async def test_forbidden_command_appended_to_executed_steps():
    """测试命中 FORBIDDEN 阻断时，executed_steps 中写入该步阻断记录"""
    class ForbiddenAuditor(ICommandAuditor):
        async def audit(self, command: Command) -> AuditDecision:
            return AuditDecision(risk_level=RiskLevel.FORBIDDEN, reason="测试绝对拦截")

    engine = LangGraphReActEngine(llm_client=MockLLM(), auditor=ForbiddenAuditor())
    state: ReActGraphState = {
        "host": SSHConnection(name="h", host="1.1.1.1", port=22, username="root", auth_type=AuthType.PASSWORD),
        "user_prompt": "测试阻断",
        "session_id": "test_audit_forbidden",
        "step_idx": 1,
        "current_command": "rm -rf /",
        "current_tool_name": "execute_ssh_command",
        "current_step_title": "高危删除",
        "permission_mode": "request_approval",
        "approved_commands": [],
        "executed_steps": [],
        "react_history": [],
    }

    result = await engine._node_audit(state)
    assert result["is_blocked"] is True
    assert "executed_steps" in result
    assert len(result["executed_steps"]) == 1
    step_entry = result["executed_steps"][0]
    assert step_entry["status"] == "forbidden"
    assert step_entry["is_error"] is True
    assert "安全策略拦截高危操作" in step_entry["analysis"]


@pytest.mark.asyncio
async def test_approval_rejection_appended_to_executed_steps():
    """测试高危审批被用户拒绝时，executed_steps 记录该步 status=rejected"""
    approval_mgr = InMemoryApprovalManager()
    approval_id = approval_mgr.create_approval_request("test_reject", 2, "systemctl stop nginx", "测试停服")
    approval_mgr.submit_decision(approval_id, approved=False)

    engine = LangGraphReActEngine(llm_client=MockLLM(), approval_manager=approval_mgr)
    state: ReActGraphState = {
        "host": SSHConnection(name="h", host="1.1.1.1", port=22, username="root", auth_type=AuthType.PASSWORD),
        "user_prompt": "高危操作",
        "session_id": "test_reject",
        "step_idx": 2,
        "current_command": "systemctl stop nginx",
        "current_tool_name": "execute_ssh_command",
        "current_step_title": "停服操作",
        "pending_approval_id": approval_id,
        "executed_steps": [],
        "react_history": [],
    }

    result = await engine._node_approval_gate(state)
    assert "executed_steps" in result
    assert len(result["executed_steps"]) == 1
    assert result["executed_steps"][0]["status"] == "rejected"
    assert result["executed_steps"][0]["is_error"] is True


@pytest.mark.asyncio
async def test_approval_timeout_appended_to_executed_steps():
    """测试高危审批超时时，executed_steps 记录该步 status=timeout"""
    approval_mgr = InMemoryApprovalManager()
    approval_id = approval_mgr.create_approval_request("test_timeout", 3, "reboot", "测试重启")

    engine = LangGraphReActEngine(llm_client=MockLLM(), approval_manager=approval_mgr)
    engine.approval_timeout = 0.01  # 极短超时

    state: ReActGraphState = {
        "host": SSHConnection(name="h", host="1.1.1.1", port=22, username="root", auth_type=AuthType.PASSWORD),
        "user_prompt": "高危操作",
        "session_id": "test_timeout",
        "step_idx": 3,
        "current_command": "reboot",
        "current_tool_name": "execute_ssh_command",
        "current_step_title": "重启主机",
        "pending_approval_id": approval_id,
        "executed_steps": [],
        "react_history": [],
    }

    result = await engine._node_approval_gate(state)
    assert "executed_steps" in result
    assert len(result["executed_steps"]) == 1
    assert result["executed_steps"][0]["status"] == "timeout"
    assert result["executed_steps"][0]["is_error"] is True


# ====================================================================
# 4. 长期记忆主机隔离与累积存储
# ====================================================================

@pytest.mark.asyncio
async def test_long_term_memory_host_isolation():
    """测试长期记忆过滤：查询 Host A 时严格隔离排除 Host B 的画像，防止污染"""
    async for session in get_db_session():
        mem_repo = SQLAlchemyMemoryRepository(session)
        # 为 Host A 和 Host B 分别录入画像
        await mem_repo.save_long_term_memory(
            LongTermMemory(
                user_id="user_test",
                memory_type="ServerEnv",
                memory_key="host_a:os_env",
                content="Ubuntu 22.04 LTS, 使用 apt",
                keywords="环境",
            )
        )
        await mem_repo.save_long_term_memory(
            LongTermMemory(
                user_id="user_test",
                memory_type="ServerEnv",
                memory_key="host_b:os_env",
                content="CentOS 7, 使用 yum",
                keywords="环境",
            )
        )

        # 针对 Host A 提取长期记忆
        text_a = await _load_long_term_memory_text(mem_repo, user_id="user_test", host_id="host_a")
        assert "host_a:os_env" in text_a
        assert "Ubuntu" in text_a
        assert "host_b:os_env" not in text_a
        assert "CentOS" not in text_a


# ====================================================================
# 5. 工具执行容错（非整型 offset/limit、重试工具适配）
# ====================================================================

@pytest.mark.asyncio
async def test_read_remote_file_safe_int_conversion():
    """测试模型输出非整数 offset/limit (如 '全部') 时安全转换为默认值，不抛出 ValueError 崩溃流"""
    gateway = MockGateway()
    engine = LangGraphReActEngine(
        llm_client=MockLLM(),
        ssh_gateway=gateway,
        tool_registry=build_default_registry(),
    )
    state: ReActGraphState = {
        "host": SSHConnection(name="h", host="1.1.1.1", port=22, username="root", auth_type=AuthType.PASSWORD),
        "user_prompt": "读取文件",
        "session_id": "test_read",
        "step_idx": 1,
        "current_tool_name": "read_remote_file",
        "current_tool_args": {"path": "/etc/hosts", "offset": "全部", "limit": "max"},
        "current_command": "cat /etc/hosts",
        "is_root": True,
    }

    result = await engine._node_act(state)
    assert "current_observation" in result
    assert "file content of /etc/hosts" in result["current_observation"]


# ====================================================================
# 6. 诊断接口客观性与 is_normal 真实判定
# ====================================================================

@pytest.mark.asyncio
async def test_step_analyze_diagnostics_truthfulness():
    """测试终端分析接口在 LLM 不可用时对真实报错和回显的客观判断，不虚构平稳"""
    from adapter.web.routers.agent_react import analyze_step_execution
    from adapter.web.schemas import StepAnalyzeRequest

    class MockRepo:
        async def get_by_identifier(self, ident):
            return None

    class FailingLLM(ILLMClient):
        async def chat(self, messages, temperature=0.1):
            raise RuntimeError("LLM unavailable")
        async def stream_chat(self, messages):
            yield ""

    # 1. 回显包含显著错误时，判定 is_normal = False
    req_err = StepAnalyzeRequest(
        step_number=1,
        step_title="测试步骤",
        command="curl localhost:8080",
        terminal_snapshot="curl: (7) Failed to connect to localhost port 8080: Connection refused",
    )
    res_err = await analyze_step_execution("ident", req_err, host_repo=MockRepo(), llm_client=FailingLLM())
    assert res_err.is_normal is False
    assert "异常" in res_err.analysis or "警告" in res_err.analysis

    # 2. free -m 输出时，分析客观展示回显摘要，不虚构 OOM 结论
    req_free = StepAnalyzeRequest(
        step_number=2,
        step_title="查看内存",
        command="free -m",
        terminal_snapshot="              total        used        free      shared  buff/cache   available\nMem:           7900        6500         200         120        1200        1000\nSwap:          2048        1800         248",
    )
    res_free = await analyze_step_execution("ident", req_free, host_repo=MockRepo(), llm_client=FailingLLM())
    assert "未观测到 Out-of-Memory (OOM) 征兆" not in res_free.analysis
    assert "物理内存水位快照" in res_free.analysis
