"""应用层 - Think 节点：只读查询收敛与最终响应裁定"""

import logging
import uuid
from typing import Any

from application.react.observation import extract_command_payload, is_empty_observation
from domain.policies.intent import (
    is_multi_target_read_only_prompt,
    is_mutation_already_satisfied,
    is_read_only_query_prompt,
)

logger = logging.getLogger("smartops.application.react.nodes.think")


class ConvergenceMixin:
    def _try_converge_read_only_query(
        self,
        user_prompt: str,
        executed_steps: list[dict[str, Any]],
        step_idx: int,
        llm_call_count: int,
        total_tokens: int,
    ) -> dict[str, Any] | None:
        """只读查询类任务强收敛保护：单目标查询在拿到数据后立即收敛"""
        is_query_intent = is_read_only_query_prompt(user_prompt)
        is_multi_target_query = is_multi_target_read_only_prompt(user_prompt)
        if not (is_query_intent and not is_multi_target_query and executed_steps):
            return None

        last_step = executed_steps[-1]
        last_obs = last_step.get("observation", "")
        if last_step.get("is_error") or not last_obs or is_empty_observation(last_step.get("command", ""), last_obs):
            return None

        logger.info("Query intent '%s' satisfied by step 1; auto-converging to Final_Answer", user_prompt)
        first_payload = extract_command_payload(last_step.get("command", ""), last_obs) or last_obs
        final_conclusion = (
            "已成功获取目标信息。\n\n"
            f"**查询结果详情**：\n```text\n{first_payload.strip()}\n```"
        )
        thought = "目标查询结果已成功获取，任务已圆满达成。"
        thought_ev = {
            "type": "thought",
            "step": step_idx,
            "thought": thought,
            "content": thought,
            "title": "查询完成",
            "command": "",
            "status": "completed",
        }
        return {
            "current_thought": thought,
            "final_answer": final_conclusion,
            "final_conclusion": final_conclusion,
            "should_stop": True,
            "llm_call_count": llm_call_count,
            "total_tokens": total_tokens,
            "emitted_events": [thought_ev],
        }

    def _finalize_response(
        self,
        step_idx: int,
        user_prompt: str,
        thought: str,
        command: str | None,
        step_title: str | None,
        final_answer: str | None,
        interactive_hint: str | None,
        is_mutation: bool,
        executed_steps: list[dict[str, Any]],
        steering_events: list[dict[str, Any]],
        current_tool_name: str | None,
        current_tool_args: dict[str, Any] | None,
        llm_call_count: int,
        total_tokens: int,
        allowed_tool_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """交互悬挂兜底、最终收敛判定与命令下发返回"""
        # 核心防悬挂保护：若终端正停留在交互确认提示符或未闭合续行符中，但模型由于格式偏差未输出 command
        # 强制下发 y 或中断信号，彻底杜绝终端被悬挂遗弃
        if interactive_hint and not command:
            if ">" in interactive_hint or "续行" in interactive_hint:
                logger.info("Terminal is stuck at PS2 continuation prompt '%s', auto-recovering with Ctrl+C", interactive_hint)
                thought = "检测到终端处于未闭合引号或多行等待状态（>），自动下发中断信号恢复正常终端提示符。"
                command = "^C"
                step_title = "恢复终端提示符"
                final_answer = None
            else:
                logger.info(
                    "Terminal is waiting at interactive prompt '%s' but model returned no action; auto-completing with 'y'",
                    interactive_hint,
                )
                thought = thought or "终端处于交互提问状态，自动下发 y 确认继续。"
                command = "y"
                step_title = "确认继续"
                final_answer = None

        # 模型给出最终结论，结束循环
        if final_answer and not command:
            # 核心防御：变更类任务绝不能在未执行任何实质变更前宣告成功收敛
            if is_mutation and self._is_premature_mutation_answer(thought, final_answer, executed_steps):
                logger.warning(
                    "Rejected premature final_answer for mutation task '%s': no mutation command executed yet and not confirmed satisfied",
                    user_prompt,
                )
                final_answer = None

        if final_answer and not command:
            thought_ev = {
                "type": "thought",
                "step": step_idx,
                "thought": thought,
                "content": thought,
                "title": step_title or "排障推演完成",
                "command": "",
                "status": "completed",
            }
            return {
                "current_thought": thought,
                "final_answer": final_answer,
                "final_conclusion": final_answer,
                "should_stop": True,
                "llm_call_count": llm_call_count,
                "total_tokens": total_tokens,
                "emitted_events": [*steering_events, thought_ev],
            }

        if not command:
            # 变更类任务防御：如果未显式输出 final_answer 且没有生成命令，严禁判定为成功完成！
            if is_mutation and not is_mutation_already_satisfied(thought, final_answer, executed_steps):
                logger.warning(
                    "No command generated and no explicit final_answer for mutation task '%s', aborting as unaccomplished",
                    user_prompt,
                )
                unaccomplished_msg = (
                    f"[提示] 系统运维变更任务尚未完全达成：未能生成下一步有效执行指令。\n"
                    f"最新推演分析：{thought}\n"
                    f"请检查终端回显状态或补充具体指示后重试。"
                )
                thought_ev = {
                    "type": "thought",
                    "step": step_idx,
                    "thought": thought,
                    "content": thought,
                    "title": step_title or "任务未达成",
                    "command": "",
                    "status": "error",
                }
                return {
                    "current_thought": thought,
                    "final_answer": unaccomplished_msg,
                    "final_conclusion": unaccomplished_msg,
                    "is_success": False,
                    "should_stop": True,
                    "llm_call_count": llm_call_count,
                    "total_tokens": total_tokens,
                    "emitted_events": [*steering_events, thought_ev],
                }

            thought_ev = {
                "type": "thought",
                "step": step_idx,
                "thought": thought,
                "content": thought,
                "title": step_title or "排障流程完毕",
                "command": "",
                "status": "completed",
            }
            return {
                "current_thought": thought,
                "final_answer": thought,
                "final_conclusion": thought or "排查流程已执行完毕。",
                "should_stop": True,
                "llm_call_count": llm_call_count,
                "total_tokens": total_tokens,
                "emitted_events": [thought_ev],
            }

        thought_ev = {
            "type": "thought",
            "step": step_idx,
            "thought": thought,
            "content": thought,
            "title": step_title,
            "command": command,
        }
        effective_tool_name = current_tool_name
        if not effective_tool_name and command:
            # 纯文本兜底仅在基线工具确实在本轮路由清单内时才标记为 execute_ssh_command
            if allowed_tool_names is None or "execute_ssh_command" in allowed_tool_names:
                effective_tool_name = "execute_ssh_command"
        return {
            "current_thought": thought,
            "current_command": command,
            "current_step_title": step_title,
            "current_tool_name": effective_tool_name,
            "current_tool_call_id": f"call_{uuid.uuid4().hex}" if effective_tool_name else None,
            "current_tool_args": current_tool_args or ({"command": command} if command else None),
            "allowed_tool_names": allowed_tool_names or [],
            "llm_call_count": llm_call_count,
            "total_tokens": total_tokens,
            "emitted_events": [*steering_events, thought_ev],
            # 图片仅在首轮多模态消息中使用，随后清空，避免每个检查点重复存储 base64
            "images": [],
        }
