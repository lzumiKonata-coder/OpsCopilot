"""
ReAct 人机协同审批机制专项测试 (Human-in-the-Loop Approval)
严格验证：
1. 遇到高危命令 (HIGH_RISK) 绝不强制取消，进入人机审批等待挂起
2. 用户点击【确认执行】后放行执行
3. 用户点击【拒绝执行】后不中止任务，反馈给智能体自适应重思并换用安全策略
4. 毁灭性破坏指令 (FORBIDDEN) 保持平台底层硬性拦截，不可审批
5. 预授权模式 (full_access / approved_commands) 免审批直接放行
6. Web API 审批接口 POST /api/v1/hosts/{id}/agent/react/approve 端到端联通
"""

from typing import List, Optional
import pytest
from httpx import ASGITransport, AsyncClient

from adapter.repository.database import get_db_session
from adapter.repository import (
    SQLAlchemyChatRepository,
    SQLAlchemySSHConnectionRepository,
)
from adapter.security.auditor import BashlexCommandAuditor
from adapter.bootstrap import container
from adapter.web.main import app
from application.ports import ICommandAuditor, ILLMClient, ISSHGateway
from application.react.managers import InMemoryApprovalManager
from application.usecases.react import ReActUseCase
from domain.entities import (
    AuditDecision,
    AuthType,
    Command,
    ExecutionResult,
    RiskLevel,
    SSHConnection,
)


class MockLLM(ILLMClient):
    def __init__(self, responses: List[str]):
        self.responses = responses
        self.call_count = 0
        self.received_messages: List[list] = []

    async def chat(self, messages: list, temperature: float = 0.2) -> str:
        self.received_messages.append(messages)
        idx = min(self.call_count, len(self.responses) - 1)
        resp = self.responses[idx]
        self.call_count += 1
        return resp

    async def stream_chat(self, messages: list):
        resp = await self.chat(messages)
        yield resp


class RecordingSSHGateway(ISSHGateway):
    def __init__(self):
        self.executed: List[str] = []

    async def execute(self, host: SSHConnection, command: Command) -> ExecutionResult:
        cmd_str = command.raw_command
        self.executed.append(cmd_str)
        if "status" in cmd_str:
            return ExecutionResult(stdout="Active: active (running)", exit_status=0)
        return ExecutionResult(stdout=f"Executed: {cmd_str}", exit_status=0)

    async def test_connection(self, host: SSHConnection) -> bool:
        return True


class ConfigurableAuditor(ICommandAuditor):
    def __init__(self, high_risk_cmds: List[str], forbidden_cmds: Optional[List[str]] = None):
        self.high_risk_cmds = high_risk_cmds
        self.forbidden_cmds = forbidden_cmds or []

    async def audit(self, command: Command) -> AuditDecision:
        cmd = command.raw_command.strip()
        for f in self.forbidden_cmds:
            if f in cmd:
                return AuditDecision(
                    risk_level=RiskLevel.FORBIDDEN,
                    hit_rules=["FORBIDDEN_RULE"],
                    reason=f"平台硬性拦截破坏性指令: {f}",
                )
        for h in self.high_risk_cmds:
            if h in cmd:
                return AuditDecision(
                    risk_level=RiskLevel.HIGH_RISK,
                    hit_rules=["HIGH_RISK_RULE"],
                    reason=f"高危系统操作需人工审批: {h}",
                )
        return AuditDecision(risk_level=RiskLevel.SAFE, reason="安全指令")


async def _create_test_host(host_repo, name: str, ip: str) -> SSHConnection:
    host = SSHConnection(
        name=name,
        host=ip,
        port=22,
        username="root",
        auth_type=AuthType.PASSWORD,
        password="pwd",
    )
    return await host_repo.save(host)


# ====================================================================
# 1. 高危命令审批通过 -> 正常下发执行
# ====================================================================

@pytest.mark.asyncio
async def test_react_high_risk_approved_executes_successfully():
    """测试：高危命令（kill -9）触发 approval_required，人工审批通过后顺利下发 PTY 执行"""
    async for session in get_db_session():
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)
        saved_host = await _create_test_host(host_repo, "appr-host-1", "192.168.1.131")

        gateway = RecordingSSHGateway()
        approval_mgr = InMemoryApprovalManager()
        llm = MockLLM(responses=[
            "Thought: 探测异常进程并强杀。\nAction: kill -9 1234\nStep_Title: 强杀异常进程",
            "Thought: 确认进程已结束，排障成功。\nFinal_Answer: 目标进程已安全终止，故障已排除。",
        ])
        auditor = ConfigurableAuditor(high_risk_cmds=["kill -9"])

        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=llm,
            ssh_gateway=gateway,
            session_repo=chat_repo,
            auditor=auditor,
            approval_manager=approval_mgr,
            approval_timeout=5.0,
        )

        events = []

        async def run_react():
            async for ev in uc.execute(saved_host.connection_id, "排障任务: 强杀进程", max_steps=5):
                events.append(ev)
                # 当状态机产出 approval_required 事件时，模拟用户在前端点击【确认执行】
                if ev.get("type") == "approval_required":
                    approval_mgr.submit_decision(
                        approval_id=ev["approval_id"],
                        approved=True,
                        scope="once",
                    )

        await run_react()

        # 验证 1: 必须产生 approval_required 事件
        approval_events = [e for e in events if e.get("type") == "approval_required"]
        assert len(approval_events) == 1
        assert approval_events[0]["command"] == "kill -9 1234"
        assert approval_events[0]["risk_level"] == "HIGH_RISK"

        # 验证 2: 审批通过后，下发物理执行
        assert "kill -9 1234" in gateway.executed

        # 验证 3: 流程整体成功完成，未被强制取消
        finish_events = [e for e in events if e.get("type") == "finish"]
        assert len(finish_events) == 1
        assert finish_events[0]["success"] is True


# ====================================================================
# 2. 高危命令被用户拒绝 -> 绝不强制取消！自适应回退重思更安全的策略
# ====================================================================

@pytest.mark.asyncio
async def test_react_high_risk_rejected_does_not_cancel_and_agent_adapts():
    """测试：高危命令（systemctl restart）被用户拒绝执行，任务不取消，智能体收到反馈自适应换用只读状态检查"""
    async for session in get_db_session():
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)
        saved_host = await _create_test_host(host_repo, "appr-host-2", "192.168.1.132")

        gateway = RecordingSSHGateway()
        approval_mgr = InMemoryApprovalManager()

        # 第一步：模型提议高危重启；被拒绝后，第二步自适应换为安全只读检查；第三步给出结论
        llm = MockLLM(responses=[
            "Thought: 尝试重启服务以恢复。\nAction: systemctl restart nginx\nStep_Title: 重启服务",
            "Thought: 用户拒绝了重启高危操作，转为检查当前服务状态与只读日志。\nAction: systemctl status nginx\nStep_Title: 检查服务状态",
            "Thought: 服务状态正常，排查完毕。\nFinal_Answer: 服务当前运行正常，避免了非必要的服务重启。",
        ])
        auditor = ConfigurableAuditor(high_risk_cmds=["systemctl restart"])

        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=llm,
            ssh_gateway=gateway,
            session_repo=chat_repo,
            auditor=auditor,
            approval_manager=approval_mgr,
            approval_timeout=5.0,
        )

        events = []

        async def run_react():
            async for ev in uc.execute(saved_host.connection_id, "排障任务: 检查或恢复服务", max_steps=5):
                events.append(ev)
                # 当状态机请求审批时，模拟用户点击【不执行】(Reject)
                if ev.get("type") == "approval_required":
                    approval_mgr.submit_decision(
                        approval_id=ev["approval_id"],
                        approved=False,
                        scope="once",
                    )

        await run_react()

        # 验证 1: 高危指令绝对没有被下发执行！
        assert "systemctl restart nginx" not in gateway.executed

        # 验证 2: 安全替代指令被成功执行！
        assert "systemctl status nginx" in gateway.executed

        # 验证 3: 流程没有中断取消，最终顺利达成目标
        finish_events = [e for e in events if e.get("type") == "finish"]
        assert len(finish_events) == 1
        assert finish_events[0]["success"] is True


# ====================================================================
# 3. 预授权模式 (full_access / approved_commands) 免人工审批
# ====================================================================

@pytest.mark.asyncio
async def test_react_high_risk_pre_approved_executes_immediately():
    """测试：当配置了 permission_mode='full_access' 或白名单命令时，高危命令免审批直接执行"""
    async for session in get_db_session():
        host_repo = SQLAlchemySSHConnectionRepository(session)
        saved_host = await _create_test_host(host_repo, "appr-host-3", "192.168.1.133")

        gateway = RecordingSSHGateway()
        approval_mgr = InMemoryApprovalManager()
        llm = MockLLM(responses=[
            "Thought: 强杀无响应进程。\nAction: kill -9 9999\nStep_Title: 杀进程",
            "Thought: 完成。\nFinal_Answer: 进程已终止。",
        ])
        auditor = ConfigurableAuditor(high_risk_cmds=["kill -9"])

        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=llm,
            ssh_gateway=gateway,
            auditor=auditor,
            approval_manager=approval_mgr,
        )

        events = []
        async for ev in uc.execute(
            saved_host.connection_id,
            "终止进程",
            max_steps=5,
            permission_mode="full_access",  # 完全放行模式
        ):
            events.append(ev)

        # 验证：无需用户交互，直接执行
        assert "kill -9 9999" in gateway.executed
        # 未产生 approval_required 挂起事件
        approval_events = [e for e in events if e.get("type") == "approval_required"]
        assert len(approval_events) == 0


# ====================================================================
# 4. 平台底层毁灭性破坏指令 (FORBIDDEN) 硬性拦截，不可审批
# ====================================================================

@pytest.mark.asyncio
async def test_react_forbidden_is_strictly_blocked_never_approvable():
    """测试：rm -rf / 等破坏性指令被平台底层硬性拦截，不进入审批，坚决阻断"""
    async for session in get_db_session():
        host_repo = SQLAlchemySSHConnectionRepository(session)
        saved_host = await _create_test_host(host_repo, "appr-host-4", "192.168.1.134")

        gateway = RecordingSSHGateway()
        approval_mgr = InMemoryApprovalManager()
        llm = MockLLM(responses=[
            "Thought: 清理磁盘。\nAction: rm -rf /\nStep_Title: 清理根目录",
        ])
        auditor = ConfigurableAuditor(high_risk_cmds=[], forbidden_cmds=["rm -rf /"])

        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=llm,
            ssh_gateway=gateway,
            auditor=auditor,
            approval_manager=approval_mgr,
        )

        events = []
        async for ev in uc.execute(
            saved_host.connection_id,
            "清理文件",
            max_steps=5,
            permission_mode="full_access",  # 即便在 full_access 下，硬性规则也绝不放行
        ):
            events.append(ev)

        # 坚决未执行
        assert gateway.executed == []
        # 无审批事件
        assert not any(e.get("type") == "approval_required" for e in events)
        # 产生阻断观测
        blocked_obs = [e for e in events if e.get("type") == "observation" and "安全阻断" in e.get("output", "")]
        assert len(blocked_obs) == 1


# ====================================================================
# 5. Web 端点 POST /api/v1/hosts/{identifier}/agent/react/approve 接口测试
# ====================================================================

@pytest.mark.asyncio
async def test_react_web_approval_endpoint_api():
    """测试：FastAPI Web 端点 /api/v1/hosts/{identifier}/agent/react/approve 正向决断与异常处理"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. 决断一个不存在的 approval_id -> 404
        resp_404 = await ac.post(
            "/api/v1/hosts/some-host/agent/react/approve",
            json={"approval_id": "non-existent-id", "approved": True, "scope": "once"},
        )
        assert resp_404.status_code == 404

        # 2. 注册一个挂起审批请求到全局 AppContainer
        appr_id = container.approval_manager.create_approval_request(
            session_id="test-session-123",
            step_idx=1,
            command="systemctl stop firewalld",
            reason="关闭防火墙高危操作",
        )

        # 3. 前端调用 API 确认审批
        resp_ok = await ac.post(
            "/api/v1/hosts/some-host/agent/react/approve",
            json={"approval_id": appr_id, "approved": True, "scope": "session"},
        )
        assert resp_ok.status_code == 200
        body = resp_ok.json()
        assert body["status"] == "ok"
        assert body["approval_id"] == appr_id
        assert body["approved"] is True

        # 验证会话级别授权生效
        assert container.approval_manager.is_session_approved("test-session-123", "systemctl stop firewalld")


# ====================================================================
# 6. 变更/破坏性任务前置方案确认与流转测试 (Plan & Confirmation Gate)
# ====================================================================

@pytest.mark.asyncio
async def test_react_pre_plan_confirmation_approved_and_executes():
    """测试：变更/删除任务第 1 步输出执行思路与风险询问，挂起等待用户确认；用户批准后顺畅执行后续命令"""
    async for session in get_db_session():
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)
        saved_host = await _create_test_host(host_repo, "plan-host-1", "192.168.1.188")

        gateway = RecordingSSHGateway()
        approval_mgr = InMemoryApprovalManager()
        llm = MockLLM(responses=[
            (
                "Thought: 即将删除当前终端目录下 (/root) 的 test.txt，该操作不可恢复。执行思路:\n"
                "1. 先确认 /root/test.txt 是否存在及其大小、修改时间\n"
                "2. 执行删除\n"
                "3. 再次检查确认文件已不存在\n"
                "需要说明的是，删除为不可逆操作，删除后无法通过终端恢复。是否按这个思路开始执行？\n"
                "Step_Title: 方案确认"
            ),
            (
                "Thought: 用户已确认方案，首先检查文件是否存在。\n"
                "Action: ls -la test.txt\n"
                "Step_Title: 检查文件"
            ),
            (
                "Thought: 文件确实存在，执行删除。\n"
                "Action: rm -f test.txt\n"
                "Step_Title: 删除文件"
            ),
            (
                "Thought: 确认已彻底删除，排障完成。\n"
                "Final_Answer: test.txt 文件已成功删除，流程圆满完成。"
            ),
        ])
        auditor = BashlexCommandAuditor()

        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=llm,
            ssh_gateway=gateway,
            session_repo=chat_repo,
            auditor=auditor,
            approval_manager=approval_mgr,
            approval_timeout=5.0,
        )

        events = []

        async def run_react():
            async for ev in uc.execute(saved_host.connection_id, "删除当前目录下的test.txt", max_steps=5):
                events.append(ev)
                if ev.get("type") == "approval_required":
                    approval_mgr.submit_decision(
                        approval_id=ev["approval_id"],
                        approved=True,
                        scope="once",
                    )

        await run_react()

        # 1. 验证第 1 步产出方案审批请求 (command 为空，reason 为前置方案确认)
        approval_events = [e for e in events if e.get("type") == "approval_required"]
        assert len(approval_events) >= 1
        assert approval_events[0]["command"] == ""
        assert "方案确认" in approval_events[0]["reason"]

        # 2. 验证批准后下发 plan_confirmed 事件
        plan_events = [e for e in events if e.get("type") == "plan_confirmed"]
        assert len(plan_events) == 1
        assert plan_events[0]["approved"] is True

        # 3. 验证后续实际下发物理命令执行，且 rm -f test.txt 未被重复审批阻断
        assert "ls -la test.txt" in gateway.executed
        assert "rm -f test.txt" in gateway.executed

        # 4. 验证最终成功完成
        finish_events = [e for e in events if e.get("type") == "finish"]
        assert len(finish_events) == 1


@pytest.mark.asyncio
async def test_react_pre_plan_confirmation_rejected_cancels():
    """测试：变更/删除任务方案确认被用户拒绝时，流程安全退出，严禁下发任何物理命令"""
    async for session in get_db_session():
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)
        saved_host = await _create_test_host(host_repo, "plan-host-2", "192.168.1.189")

        gateway = RecordingSSHGateway()
        approval_mgr = InMemoryApprovalManager()
        llm = MockLLM(responses=[
            (
                "Thought: 即将删除当前终端目录下 (/root) 的 test.txt... 是否按这个思路开始执行？\n"
                "Step_Title: 方案确认"
            ),
        ])
        auditor = BashlexCommandAuditor()

        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=llm,
            ssh_gateway=gateway,
            session_repo=chat_repo,
            auditor=auditor,
            approval_manager=approval_mgr,
            approval_timeout=5.0,
        )

        events = []

        async def run_react():
            async for ev in uc.execute(saved_host.connection_id, "删除当前目录下的test.txt", max_steps=5):
                events.append(ev)
                if ev.get("type") == "approval_required":
                    approval_mgr.submit_decision(
                        approval_id=ev["approval_id"],
                        approved=False,
                        scope="once",
                    )

        await run_react()

        # 验证未下发任何物理命令
        assert len(gateway.executed) == 0

        # 验证收敛至已取消
        finish_events = [e for e in events if e.get("type") == "finish"]
        assert len(finish_events) == 1
        assert "用户已取消执行该操作方案" in finish_events[0]["conclusion"]
