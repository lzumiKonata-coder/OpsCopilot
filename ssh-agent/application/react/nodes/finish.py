"""应用层 - ReAct Finish 节点"""

from typing import Any

from application.react.observation import detect_command_error
from application.react.state import ReActGraphState


class FinishNodeMixin:
    async def _node_finish(self, state: ReActGraphState) -> dict[str, Any]:
        """Node 5: 最终报告与结论收敛节点"""
        executed_steps = state.get("executed_steps") or []
        final_conclusion = state.get("final_conclusion") or state.get("final_answer")
        has_encountered_error = state.get("has_encountered_error", False)
        last_error_detail = state.get("last_error_detail")
        planned_steps_queue = state.get("planned_steps_queue") or []
        is_blocked = state.get("is_blocked", False)
        max_steps = state.get("max_steps")
        step_idx = state.get("step_idx", 1)
        is_max_steps_exceeded = bool(max_steps is not None and step_idx > max_steps)

        is_success = True
        if is_blocked:
            is_success = False
            if not final_conclusion:
                blocked_reason = state.get("blocked_reason") or "命中高危命令安全策略拦截"
                final_conclusion = f"[安全拦截/阻断] 运维排障流程已终止：{blocked_reason}。"
        elif state.get("is_stalled") or state.get("budget_exceeded"):
            is_success = False
        elif not executed_steps and not final_conclusion:
            is_success = False
        elif executed_steps:
            last_step = executed_steps[-1]
            last_obs = last_step.get("observation", "")
            last_cmd = last_step.get("command", "")
            last_err = detect_command_error(last_cmd, last_obs)
            if last_err or last_step.get("is_error"):
                is_success = False
            elif has_encountered_error:
                if any(err_k in last_obs.lower() for err_k in ["未找到命令", "command not found", "unit not found", "failed to start"]):
                    is_success = False

        if not final_conclusion:
            if is_max_steps_exceeded:
                # 步数耗尽且模型未输出 Final_Answer，严禁虚报成功
                is_success = False
                final_conclusion = (
                    f"[提示] 运维任务未闭环：已达最大步数限制 ({len(executed_steps)} 步)，排障流程在此中断。"
                    + (f"期间检测到异常（{last_error_detail}）。" if last_error_detail else "大模型尚未输出 Final_Answer 最终验证结论。")
                    + self.failure_advice(last_error_detail)
                )
            elif is_success and not has_encountered_error and not planned_steps_queue and executed_steps:
                final_conclusion = (
                    f"已执行完成共 {len(executed_steps)} 个运维步骤。"
                    "流程未收到显式 Final_Answer，前序步骤物理执行正常。"
                )
            else:
                is_success = False
                final_conclusion = (
                    f"[提示] 运维任务未完全达成：已推进至最大步数限制 ({len(executed_steps)} 步)，"
                    + (f"期间检测到异常（{last_error_detail}）。" if last_error_detail else "流程尚未收到最终验证完成确认。")
                    + self.failure_advice(last_error_detail)
                )

        finish_ev = {
            "type": "finish",
            "conclusion": final_conclusion,
            "steps_count": len(executed_steps),
            "success": is_success,
        }
        return {
            "final_conclusion": final_conclusion,
            "is_success": is_success,
            "emitted_events": [finish_ev],
        }
