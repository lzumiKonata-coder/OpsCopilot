"""应用层 - Think 节点：前置方案确认与变更任务早熟收敛判定"""

from typing import Any, Dict, List, Optional

from application.react.state import ReActGraphState
from domain.policies.intent import (
    is_inspection_command,
    is_mutation_already_satisfied,
    is_mutation_task,
)


class PlanConfirmationMixin:
    @staticmethod
    def _is_premature_mutation_answer(
        thought: str,
        final_answer: Optional[str],
        executed_steps: List[Dict[str, Any]],
    ) -> bool:
        """变更类任务在未执行任何实质变更前宣告成功的判定"""
        if not final_answer:
            return False
        has_executed_mutation = any(not is_inspection_command(s.get("command", "")) for s in executed_steps)
        return not has_executed_mutation and not is_mutation_already_satisfied(thought, final_answer, executed_steps)

    def _check_plan_confirmation(
        self,
        state: ReActGraphState,
        step_idx: int,
        user_prompt: str,
        thought: str,
        command: Optional[str],
        final_answer: Optional[str],
        plan: Optional[str],
    ) -> bool:
        """判断是否为前置方案确认状态（Step 1 针对变更/高危任务输出执行思路并征询用户开始执行）"""
        is_plan_confirmed = state.get("is_plan_confirmed", False)
        permission_mode = state.get("permission_mode", "request_approval")
        return (
            step_idx == 1
            and not is_plan_confirmed
            and not command
            and not final_answer
            and permission_mode != "full_access"
            and bool(self.approval_manager)
            and (
                is_mutation_task(user_prompt)
                or any(q in thought for q in ["是否按这个思路开始执行", "是否开始执行", "确认执行", "执行思路"])
                or bool(plan)
            )
        )

    def _build_plan_confirmation_response(
        self,
        state: ReActGraphState,
        step_idx: int,
        user_prompt: str,
        thought: str,
        plan: Optional[str],
        step_title: Optional[str],
        llm_call_count: int,
        total_tokens: int,
    ) -> Dict[str, Any]:
        """构造前置方案确认的审批挂起返回"""
        session_id = state.get("session_id") or ""
        approval_id = self.approval_manager.create_approval_request(
            session_id=session_id,
            step_idx=step_idx,
            command="",
            reason=f"执行变更任务【{user_prompt}】前置方案确认",
        )
        final_plan = plan or thought
        thought_text = thought if plan and plan != thought else ""
        thought_ev = {
            "type": "thought",
            "step": step_idx,
            "thought": thought_text,
            "content": final_plan,
            "title": step_title or "方案确认",
            "plan": final_plan,
            "command": "",
            "status": "approval_required",
        }
        approval_ev = {
            "type": "approval_required",
            "step": step_idx,
            "title": step_title or "方案确认",
            "command": "",
            "plan": final_plan,
            "description": final_plan,
            "reason": f"执行变更任务【{user_prompt}】前置方案确认",
            "risk_level": "HIGH_RISK",
            "approval_id": approval_id,
        }
        return {
            "current_thought": thought_text,
            "current_command": None,
            "current_step_title": step_title or "方案确认",
            "current_plan": final_plan,
            "pending_approval_id": approval_id,
            "blocked_reason": f"执行变更任务【{user_prompt}】前置方案确认",
            "llm_call_count": llm_call_count,
            "total_tokens": total_tokens,
            "emitted_events": [thought_ev, approval_ev],
        }
