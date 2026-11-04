"""AST 解析失败时的 fail-secure 回归测试：升级为强制人工复核"""

import pytest

from adapter.security.auditor import BashlexCommandAuditor
from application.react.engine import LangGraphReActEngine
from application.react.managers import InMemoryApprovalManager
from domain.entities import Command, RiskLevel


@pytest.mark.asyncio
async def test_unparsable_command_requires_manual_review():
    """AST 解析失败且无破坏特征：不得返回 SAFE，必须升级为强制人工复核"""
    auditor = BashlexCommandAuditor()
    decision = await auditor.audit(Command(raw_command='echo "unclosed'))

    assert decision.risk_level == RiskLevel.HIGH_RISK
    assert decision.requires_manual_review is True
    assert "AST_PARSE_FAILED" in decision.hit_rules


@pytest.mark.asyncio
async def test_unparsable_destructive_command_still_forbidden():
    """AST 解析失败且命中毁灭性特征：仍应硬性拦截"""
    auditor = BashlexCommandAuditor()
    decision = await auditor.audit(Command(raw_command='rm -rf / "unclosed'))

    assert decision.risk_level == RiskLevel.FORBIDDEN
    assert decision.is_blocked is True


@pytest.mark.asyncio
async def test_full_access_allows_unverified_command_with_flag():
    """full_access 模式下解析失败的命令按契约放行，但必须携带未审查标记"""
    engine = LangGraphReActEngine(
        llm_client=None,  # type: ignore[arg-type]
        auditor=BashlexCommandAuditor(),
        approval_manager=InMemoryApprovalManager(),
    )
    state = {
        "current_command": 'echo "unclosed',
        "step_idx": 1,
        "session_id": "audit-fallback-session",
        "permission_mode": "full_access",
        "current_tool_name": "execute_ssh_command",
        "current_tool_args": {"command": 'echo "unclosed'},
        "react_history": [],
    }

    result = await engine._node_audit(state)  # type: ignore[arg-type]

    events = result.get("emitted_events", [])
    assert not any(e.get("type") == "approval_required" for e in events)
    action_events = [e for e in events if e.get("type") == "action"]
    assert len(action_events) == 1
    assert action_events[0]["audit_unverified"] is True
    assert action_events[0]["approved"] is True


@pytest.mark.asyncio
async def test_request_approval_overrides_whitelist_for_unparsable_command():
    """request_approval 模式下，解析失败命令即使已在白名单中也必须转人工审批"""
    engine = LangGraphReActEngine(
        llm_client=None,  # type: ignore[arg-type]
        auditor=BashlexCommandAuditor(),
        approval_manager=InMemoryApprovalManager(),
    )
    command = 'echo "unclosed'
    state = {
        "current_command": command,
        "step_idx": 1,
        "session_id": "audit-fallback-session",
        "permission_mode": "request_approval",
        "approved_commands": [command],
        "current_tool_name": "execute_ssh_command",
        "current_tool_args": {"command": command},
        "react_history": [],
    }

    result = await engine._node_audit(state)  # type: ignore[arg-type]

    events = result.get("emitted_events", [])
    assert any(e.get("type") == "approval_required" for e in events)
    assert result.get("pending_approval_id")
