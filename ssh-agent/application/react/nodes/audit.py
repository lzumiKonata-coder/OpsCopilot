"""应用层 - ReAct Audit 节点"""

import asyncio
import logging
from typing import Any

from application.ports import ToolOperation
from application.react.cancel_context import get_cancel_check
from domain.policies.file_access import (
    audit_file_content_security,
    audit_file_path_security,
)
from application.react.state import ReActGraphState
from domain.entities import AuditDecision, Command
from domain.rules import RiskLevel

logger = logging.getLogger("smartops.application.react.nodes.audit")


class AuditNodeMixin:
    async def _node_audit(self, state: ReActGraphState) -> dict[str, Any]:
        """Node 2: AST 语法级安全审查与人机协同审批节点"""
        eff_cancel_check = state.get("cancel_check") or get_cancel_check()
        if eff_cancel_check and await eff_cancel_check():
            raise asyncio.CancelledError("客户端已主动取消排障流程")
        command = state.get("current_command", "")
        step_idx = state.get("step_idx", 1)
        step_title = state.get("current_step_title", f"步骤 {step_idx}")
        session_id = state.get("session_id") or ""
        permission_mode = state.get("permission_mode", "request_approval")
        approved_commands = state.get("approved_commands") or []

        tool_name = state.get("current_tool_name")
        tool_args = state.get("current_tool_args") or {}
        tool = self.tool_registry.get(tool_name)
        operation = tool.spec.operation if tool is not None else ToolOperation.COMMAND

        # 1. 运行安全审查（按工具通道类型执行多维度审计）
        audit_decision: AuditDecision | None = None
        if operation in {ToolOperation.WRITE_FILE, ToolOperation.EDIT_FILE}:
            path = tool_args.get("path", "")
            content_to_check = tool_args.get("content", "") or tool_args.get("replacement", "")
            path_decision = audit_file_path_security(path, is_write=True)
            content_decision = audit_file_content_security(content_to_check) if content_to_check else None

            # 路径与内容审计独立执行，并合并为更严重的结果 (FORBIDDEN > HIGH_RISK > SAFE)
            decisions = [d for d in (path_decision, content_decision) if d is not None]
            if any(d.risk_level == RiskLevel.FORBIDDEN for d in decisions):
                merged_rules = []
                merged_reasons = []
                for d in decisions:
                    if d.risk_level == RiskLevel.FORBIDDEN:
                        merged_rules.extend(d.hit_rules)
                        if d.reason:
                            merged_reasons.append(d.reason)
                    elif d.risk_level == RiskLevel.HIGH_RISK:
                        merged_rules.extend(d.hit_rules)
                audit_decision = AuditDecision(
                    risk_level=RiskLevel.FORBIDDEN,
                    hit_rules=list(set(merged_rules)),
                    reason="; ".join(merged_reasons),
                )
            elif any(d.risk_level == RiskLevel.HIGH_RISK for d in decisions):
                merged_rules = []
                merged_reasons = []
                for d in decisions:
                    if d.risk_level == RiskLevel.HIGH_RISK:
                        merged_rules.extend(d.hit_rules)
                        if d.reason:
                            merged_reasons.append(d.reason)
                audit_decision = AuditDecision(
                    risk_level=RiskLevel.HIGH_RISK,
                    hit_rules=list(set(merged_rules)),
                    reason="; ".join(merged_reasons),
                )
            else:
                audit_decision = path_decision
        elif operation == ToolOperation.READ_FILE:
            path = tool_args.get("path", "")
            # 只读文件通道直接采用路径安全决策，不再回退到对合成 `cat` 命令的 Shell 审计
            audit_decision = audit_file_path_security(path, is_write=False)

        if operation == ToolOperation.MCP and tool is not None:
            # 外部工具不参与本地 Shell 语法审计，按工具声明的审批策略决定风险等级
            if tool.spec.requires_approval:
                audit_decision = AuditDecision(
                    risk_level=RiskLevel.HIGH_RISK,
                    reason=f"外部 MCP 工具 {tool.spec.name} 需人工确认后执行（来源：{tool.spec.source}）",
                )
            else:
                audit_decision = AuditDecision(risk_level=RiskLevel.SAFE, reason="外部只读工具")

        if not audit_decision and self.auditor:
            try:
                audit_decision = await self.auditor.audit(Command(raw_command=command))
            except Exception as e:
                logger.error("Auditor check failed, blocking command conservatively: %s", e)
                audit_decision = AuditDecision(
                    risk_level=RiskLevel.FORBIDDEN,
                    hit_rules=["AUDITOR_CRASH"],
                    reason=f"安全审查器执行异常，已保守阻断 ({e})"
                )

        if not audit_decision:
            audit_decision = AuditDecision(risk_level=RiskLevel.SAFE, reason="未启用审查器")

        # 2. 绝对禁止指令 (FORBIDDEN) -> 坚决平台硬性拦截，不可审批
        if audit_decision.risk_level == RiskLevel.FORBIDDEN:
            obs_err = f"[安全阻断] 指令包含毁灭性破坏特征，被平台硬性规则绝对拦截 ({audit_decision.reason})"
            action_ev = {
                "type": "action",
                "step": step_idx,
                "title": step_title,
                "command": command,
                "blocked": True,
            }
            obs_ev = {
                "type": "observation",
                "step": step_idx,
                "output": obs_err,
            }
            react_history = list(state.get("react_history") or [])
            react_history.append(f"Step {step_idx}: Action=`{command}` -> Blocked: {obs_err}")
            executed_steps = list(state.get("executed_steps") or [])
            executed_steps.append({
                "step": step_idx,
                "title": step_title or f"第 {step_idx} 步：安全审计阻断 [{tool_name}]",
                "command": command or tool_name,
                "tool_name": tool_name,
                "tool_call_id": state.get("current_tool_call_id"),
                "audit_decision": "FORBIDDEN",
                "risk_level": "FORBIDDEN",
                "status": "forbidden",
                "observation": obs_err,
                "analysis": f"安全策略拦截高危操作: {audit_decision.reason}",
                "is_error": True,
            })

            return {
                "is_blocked": True,
                "blocked_reason": obs_err,
                "has_encountered_error": True,
                "last_error_detail": obs_err,
                "should_stop": True,
                "react_history": react_history,
                "executed_steps": executed_steps,
                "emitted_events": [action_ev, obs_ev],
            }

        # 3. 高危指令人机协同审批检查 (命中 HIGH_RISK 指令)
        is_pre_approved = False
        if permission_mode == "full_access":
            is_pre_approved = True
        elif not self.approval_manager:
            is_pre_approved = True
        elif command in approved_commands:
            is_pre_approved = True
        elif self.approval_manager and self.approval_manager.is_session_approved(session_id, command):
            is_pre_approved = True

        # 外部 MCP 工具会产生跨系统副作用，必须逐次人工确认：
        # 即使 full_access、预授权命令或会话级批准，也不得绕过审批门禁（项目硬性安全约束）
        mcp_requires_manual_approval = (
            operation == ToolOperation.MCP
            and tool is not None
            and bool(tool.spec.requires_approval)
        )
        if mcp_requires_manual_approval:
            is_pre_approved = False

        # 审计器无法完成语法级审查时（如 AST 解析失败），视为"未审查"状态：
        # - request_approval 模式：任何预授权一律失效，强制人工复核
        # - full_access 模式：遵循"放行高危"契约直接执行，但附带审计告警标记以便追溯
        audit_unverified = audit_decision.requires_manual_review
        if audit_unverified and permission_mode != "full_access":
            is_pre_approved = False

        if not is_pre_approved and audit_decision.risk_level == RiskLevel.HIGH_RISK and self.approval_manager:
            approval_id = self.approval_manager.create_approval_request(
                session_id=session_id,
                step_idx=step_idx,
                command=command,
                reason=audit_decision.reason or "AI 请求执行命令，是否允许？",
            )

            approval_ev = {
                "type": "approval_required",
                "step": step_idx,
                "title": step_title,
                "command": command,
                "reason": audit_decision.reason or "AI 请求执行命令，是否允许？",
                "risk_level": audit_decision.risk_level.value if hasattr(audit_decision.risk_level, "value") else str(audit_decision.risk_level),
                "approval_id": approval_id,
            }

            return {
                "is_blocked": False,
                "blocked_reason": audit_decision.reason,
                "pending_approval_id": approval_id,
                "emitted_events": [approval_ev],
            }

        # 4. 正常下发执行
        file_diff_meta = {}
        if operation in {ToolOperation.EDIT_FILE, ToolOperation.WRITE_FILE}:
            file_diff_meta = {
                "tool_name": tool_name,
                "file_path": tool_args.get("path"),
                "file_target": tool_args.get("target"),
                "file_replacement": tool_args.get("replacement"),
                "file_content": tool_args.get("content"),
            }

        action_ev = {
            "type": "action",
            "step": step_idx,
            "title": step_title,
            "command": command,
            "risk_level": audit_decision.risk_level.value if hasattr(audit_decision.risk_level, "value") else str(audit_decision.risk_level),
            "approved": is_pre_approved,
            **file_diff_meta,
        }
        if audit_unverified:
            action_ev["audit_unverified"] = True
            action_ev["audit_warning"] = audit_decision.reason
            logger.warning(
                "Command executed without completed AST audit (permission_mode=%s): %s | %s",
                permission_mode,
                command,
                audit_decision.reason,
            )
        return {"is_blocked": False, "pending_approval_id": None, "emitted_events": [action_ev]}
