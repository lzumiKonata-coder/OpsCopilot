"""Audit 节点回归：只读文件通道直接采用路径安全决策，不回退 Shell 审计"""

import pytest

from adapter.tools.builtin import build_default_registry
from application.ports import ICommandAuditor
from application.react.managers import InMemoryApprovalManager
from application.react.nodes.audit import AuditNodeMixin
from domain.entities import AuditDecision, Command, RiskLevel


class _RecordingAuditor(ICommandAuditor):
    def __init__(self):
        self.calls = 0

    async def audit(self, command: Command) -> AuditDecision:
        self.calls += 1
        return AuditDecision(risk_level=RiskLevel.HIGH_RISK, reason="合成命令被误判为高危")


class _Harness(AuditNodeMixin):
    tool_registry = build_default_registry()
    approval_manager = InMemoryApprovalManager()


@pytest.mark.asyncio
async def test_read_only_file_uses_path_decision_without_shell_audit():
    auditor = _RecordingAuditor()
    harness = _Harness()
    harness.auditor = auditor
    state = {
        "current_command": "cat /tmp/app.conf",
        "current_tool_name": "read_remote_file",
        "current_tool_args": {"path": "/tmp/app.conf"},
        "current_step_title": "读取配置",
        "step_idx": 1,
        "session_id": "sess",
        "permission_mode": "request_approval",
        "approved_commands": [],
    }

    result = await harness._node_audit(state)

    assert auditor.calls == 0
    assert result["pending_approval_id"] is None
    assert result["is_blocked"] is False
