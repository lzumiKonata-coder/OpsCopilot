"""应用层 - ReAct ApprovalGate 节点"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from application.react.cancel_context import get_cancel_check
from application.react.state import ReActGraphState

logger = logging.getLogger("smartops.application.react.nodes.approval_gate")


class ApprovalGateNodeMixin:
    async def _node_approval_gate(self, state: ReActGraphState) -> Dict[str, Any]:
        """Node 2b: 人机协同审批门禁挂起等待节点 (绝不强制取消，支持确认执行与自适应重思替代)"""
        approval_id = state.get("pending_approval_id")
        command = state.get("current_command", "")
        step_idx = state.get("step_idx", 1)
        step_title = state.get("current_step_title", f"步骤 {step_idx}")
        tool_name = state.get("current_tool_name")
        reason = state.get("blocked_reason") or "高危操作需人工审批"

        eff_cancel_check = state.get("cancel_check") or get_cancel_check()
        if eff_cancel_check and await eff_cancel_check():
            raise asyncio.CancelledError("客户端已主动取消排障流程")

        # 软预算上限守卫：检查整场任务剩余可用时长
        start_time = state.get("start_time") or time.time()
        max_run_seconds = state.get("max_run_seconds") or 600.0
        elapsed_seconds = time.time() - start_time
        remaining_seconds = max_run_seconds - elapsed_seconds

        if remaining_seconds <= 0:
            logger.warning("Approval gate: overall run budget exceeded (elapsed %.1fs >= max_run_seconds %.1fs)", elapsed_seconds, max_run_seconds)
            timeout_msg = f"[提示] 运维任务已达总运行时间上限 ({int(max_run_seconds)}s)，审批等待安全终止。"
            thought_ev = {
                "type": "thought",
                "step": step_idx,
                "thought": timeout_msg,
                "content": timeout_msg,
                "title": "运行时间超限终止",
                "status": "completed",
            }
            executed_steps = list(state.get("executed_steps") or [])
            executed_steps.append({
                "step": step_idx,
                "title": step_title or f"第 {step_idx} 步：时间超限终止",
                "command": command or "(方案确认)",
                "audit_decision": "TIMEOUT",
                "risk_level": "HIGH_RISK",
                "status": "timeout",
                "observation": timeout_msg,
                "analysis": "总运行时间预算耗尽，审批终止。",
                "is_error": True,
            })
            return {
                "is_blocked": False,
                "pending_approval_id": None,
                "budget_exceeded": True,
                "is_success": False,
                "should_stop": True,
                "final_answer": timeout_msg,
                "final_conclusion": timeout_msg,
                "executed_steps": executed_steps,
                "emitted_events": [thought_ev],
            }

        effective_approval_timeout = min(self.approval_timeout, remaining_seconds)
        user_approved: Optional[bool] = None
        if self.approval_manager and approval_id:
            user_approved = await self.approval_manager.wait_for_decision(
                approval_id=approval_id,
                timeout=effective_approval_timeout,
                cancel_check=eff_cancel_check,
            )

        if user_approved is True:
            # 用户点击【开始执行】
            if not command:
                # 前置方案已获得用户确认批准：流转进入实际执行
                plan_ev = {
                    "type": "plan_confirmed",
                    "step": step_idx,
                    "title": "方案已确认",
                    "approved": True,
                }
                react_history = list(state.get("react_history") or [])
                react_history.append(
                    f"Step {step_idx}: 用户已审阅并确认开始执行上述排查与操作方案。请严格按照所列方案推进实际执行！"
                )
                return {
                    "is_blocked": False,
                    "is_plan_confirmed": True,
                    "pending_approval_id": None,
                    "current_command": None,
                    # 方案确认属于前置交互，不消耗实际执行步数
                    "step_idx": step_idx,
                    "react_history": react_history,
                    "emitted_events": [plan_ev],
                }
            else:
                # 针对单步高危指令的审批通过
                action_ev = {
                    "type": "action",
                    "step": step_idx,
                    "title": step_title,
                    "command": command,
                    "risk_level": "HIGH_RISK",
                    "approved": True,
                }
                return {
                    "is_blocked": False,
                    "is_plan_confirmed": True,
                    "pending_approval_id": None,
                    "emitted_events": [action_ev],
                }

        elif user_approved is False:
            if not command:
                cancel_msg = "用户已取消执行该操作方案。"
                thought_ev = {
                    "type": "thought",
                    "step": step_idx,
                    "thought": cancel_msg,
                    "content": cancel_msg,
                    "title": "方案已取消",
                    "status": "completed",
                }
                return {
                    "is_blocked": False,
                    "pending_approval_id": None,
                    "should_stop": True,
                    "final_answer": cancel_msg,
                    "final_conclusion": cancel_msg,
                    "emitted_events": [thought_ev],
                }
            else:
                # 用户点击【不执行】：绝不取消任务！反馈给智能体进行自适应重思与替代
                reject_msg = f"[提示] 用户未批准执行高危指令 `{command}`（原因: {reason}）。请反思并尝试更安全的只读排查或替代策略。"
                obs_ev = {
                    "type": "observation",
                    "step": step_idx,
                    "output": reject_msg,
                }
                reflect_ev = {
                    "type": "reflection",
                    "step": step_idx,
                    "analysis": f"用户拒绝审批高危指令 `{command}`。ReAct 状态机保持运行，将根据反馈调整后续排障链路。",
                    "is_normal": False,
                }
                react_history = list(state.get("react_history") or [])
                react_history.append(f"Step {step_idx}: Action=`{command}` -> 用户拒绝执行该高危命令，请换用安全策略。")
                executed_steps = list(state.get("executed_steps") or [])
                executed_steps.append({
                    "step": step_idx,
                    "title": step_title or f"第 {step_idx} 步：用户审批拒绝 [{tool_name}]",
                    "command": command or tool_name,
                    "audit_decision": "REJECTED",
                    "risk_level": "HIGH_RISK",
                    "status": "rejected",
                    "observation": reject_msg,
                    "analysis": f"用户拒绝执行高危命令: {reason or '用户主动拒绝'}",
                    "is_error": True,
                })

                return {
                    "is_blocked": False,
                    "pending_approval_id": None,
                    "current_command": None,  # 清空命令，不进入 act_node，直接回流 think_node
                    "current_observation": reject_msg,
                    "current_reflection": "用户拒绝审批高危指令，状态机转向安全替代方案。",
                    "step_idx": step_idx + 1,
                    "react_history": react_history,
                    "executed_steps": executed_steps,
                    "emitted_events": [obs_ev, reflect_ev],
                }
        else:
            # 等待超时未决
            executed_steps = list(state.get("executed_steps") or [])
            if not command:
                timeout_msg = f"[提示] 执行方案等待用户确认超时 ({self.approval_timeout:.0f}s)，流程已安全终止。"
                thought_ev = {
                    "type": "thought",
                    "step": step_idx,
                    "thought": timeout_msg,
                    "content": timeout_msg,
                    "title": "确认超时",
                    "status": "completed",
                }
                executed_steps.append({
                    "step": step_idx,
                    "title": step_title or f"第 {step_idx} 步：方案确认超时",
                    "command": "(方案确认)",
                    "audit_decision": "TIMEOUT",
                    "risk_level": "HIGH_RISK",
                    "status": "timeout",
                    "observation": timeout_msg,
                    "analysis": "方案确认等待超时，流程安全终止。",
                    "is_error": True,
                })
                return {
                    "is_blocked": False,
                    "pending_approval_id": None,
                    "should_stop": True,
                    "final_answer": timeout_msg,
                    "final_conclusion": timeout_msg,
                    "executed_steps": executed_steps,
                    "emitted_events": [thought_ev],
                }
            else:
                timeout_msg = f"[提示] 高危指令 `{command}` 等待用户审批超时 ({self.approval_timeout:.0f}s)，为保障生产安全，本步已跳过。"
                obs_ev = {"type": "observation", "step": step_idx, "output": timeout_msg}
                reflect_ev = {
                    "type": "reflection",
                    "step": step_idx,
                    "analysis": f"高危指令 `{command}` 审批超时，跳过执行并继续推演。",
                    "is_normal": False,
                }
                react_history = list(state.get("react_history") or [])
                react_history.append(f"Step {step_idx}: Action=`{command}` -> 审批超时，未执行。")
                executed_steps.append({
                    "step": step_idx,
                    "title": step_title or f"第 {step_idx} 步：高危审批超时 [{tool_name}]",
                    "command": command or tool_name,
                    "audit_decision": "TIMEOUT",
                    "risk_level": "HIGH_RISK",
                    "status": "timeout",
                    "observation": timeout_msg,
                    "analysis": f"审批等待超过 {self.approval_timeout:.0f}s 超时，安全跳过此步。",
                    "is_error": True,
                })

                return {
                    "is_blocked": False,
                    "pending_approval_id": None,
                    "current_command": None,
                    "current_observation": timeout_msg,
                    "current_reflection": "审批超时跳过，状态机继续推进。",
                    "step_idx": step_idx + 1,
                    "react_history": react_history,
                    "executed_steps": executed_steps,
                    "emitted_events": [obs_ev, reflect_ev],
                }
