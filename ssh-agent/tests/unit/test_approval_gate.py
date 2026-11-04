"""审批门禁回归：current_step_title 为空时拒绝/超时分支不得因未定义 tool_name 崩溃"""

import time

import pytest

from application.react.managers import InMemoryApprovalManager
from application.react.nodes.approval_gate import ApprovalGateNodeMixin


class _GateHarness(ApprovalGateNodeMixin):
    def __init__(self, manager: InMemoryApprovalManager, timeout: float = 0.05):
        self.approval_manager = manager
        self.approval_timeout = timeout


def _gate_state(approval_id: str):
    return {
        "pending_approval_id": approval_id,
        "current_command": "cat /etc/shadow",
        "current_tool_name": "read_remote_file",
        "current_step_title": "",
        "step_idx": 1,
        "blocked_reason": "高危读取",
        "start_time": time.time(),
        "max_run_seconds": 60,
    }


@pytest.mark.asyncio
async def test_rejection_with_empty_step_title_uses_tool_name():
    manager = InMemoryApprovalManager()
    approval_id = manager.create_approval_request("sess", 1, "cat /etc/shadow", "高危读取")
    manager.submit_decision(approval_id, approved=False)
    gate = _GateHarness(manager)

    result = await gate._node_approval_gate(_gate_state(approval_id))

    assert result["current_command"] is None
    assert "read_remote_file" in result["executed_steps"][-1]["title"]


@pytest.mark.asyncio
async def test_timeout_with_empty_step_title_uses_tool_name():
    manager = InMemoryApprovalManager()
    approval_id = manager.create_approval_request("sess", 1, "cat /etc/shadow", "高危读取")
    gate = _GateHarness(manager, timeout=0.01)

    result = await gate._node_approval_gate(_gate_state(approval_id))

    assert result["current_command"] is None
    assert "read_remote_file" in result["executed_steps"][-1]["title"]


@pytest.mark.asyncio
async def test_plan_confirmation_does_not_consume_execution_step():
    manager = InMemoryApprovalManager()
    approval_id = manager.create_approval_request("sess", 1, "", "方案确认")
    manager.submit_decision(approval_id, approved=True)
    gate = _GateHarness(manager)
    state = {
        "pending_approval_id": approval_id,
        "current_command": "",
        "current_tool_name": None,
        "current_step_title": "方案确认",
        "step_idx": 1,
        "blocked_reason": "方案确认",
        "start_time": time.time(),
        "max_run_seconds": 60,
    }

    result = await gate._node_approval_gate(state)

    assert result["is_plan_confirmed"] is True
    assert result["step_idx"] == 1

