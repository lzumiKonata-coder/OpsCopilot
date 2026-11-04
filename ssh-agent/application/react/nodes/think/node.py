"""应用层 - ReAct Think 节点：推演编排与收尾"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from langgraph.types import StreamWriter

from application.react.cancel_context import get_cancel_check
from application.react.state import ReActGraphState
from application.react.streaming import StreamingThoughtFilter
from domain.policies.intent import is_mutation_task
from utils.text import TokenBudgetEstimator

from .convergence import ConvergenceMixin
from .guards import check_budget, check_loop_guard, check_step_limit
from .llm import parse_tool_calls, resolve_text_response, resolve_tool_call, split_reply
from .plan import PlanConfirmationMixin
from .prompt import build_conversation, build_system_prompt, build_user_prompt
from .retry import RetryActionMixin

logger = logging.getLogger("smartops.application.react.nodes.think")


class ThinkNodeMixin(PlanConfirmationMixin, RetryActionMixin, ConvergenceMixin):
    async def _node_think(self, state: ReActGraphState, writer: StreamWriter = None) -> Dict[str, Any]:
        """Node 1: Thought 思考推演节点"""
        step_idx = state.get("step_idx", 1)
        host = state["host"]
        user_prompt = state["user_prompt"]
        images = list(state.get("images") or [])
        planned_steps_queue = list(state.get("planned_steps_queue") or [])
        has_encountered_error = state.get("has_encountered_error", False)
        last_error_detail = state.get("last_error_detail")
        react_history = state.get("react_history") or []
        is_root = state.get("is_root", False)
        os_family = state.get("os_family", "Linux (通用环境)")
        terminal_buffer = state.get("terminal_buffer") or ""
        core_memories_text = state.get("core_memories_text") or ""
        session_history = state.get("session_history") or []
        executed_steps = list(state.get("executed_steps") or [])

        # 即时取消检测响应
        cancel_check = state.get("cancel_check") or get_cancel_check()
        eff_cancel_check = cancel_check
        if eff_cancel_check and await eff_cancel_check():
            logger.info("Cancellation detected at start of _node_think")
            raise asyncio.CancelledError("客户端连接已断开或用户主动取消")

        llm_call_count = state.get("llm_call_count", 0)
        total_tokens = state.get("total_tokens", 0)

        # 检查是否步数超限
        early_result = check_step_limit(state, step_idx)
        if early_result is not None:
            return early_result

        # 软预算边界校验 (Soft Budgets Check: max_run_seconds / max_llm_calls / max_tokens)
        early_result = check_budget(state, step_idx, executed_steps)
        if early_result is not None:
            return early_result

        # 提取用户即时插话/方向纠偏 (Steering Messages)
        session_id = state.get("session_id")
        steering_texts: List[str] = []
        if getattr(self, "steering_manager", None) and session_id:
            steering_texts.extend(self.steering_manager.pop_steering_messages(session_id))
        if state.get("steering_message"):
            steering_texts.append(state["steering_message"])
            state["steering_message"] = None

        if steering_texts:
            planned_steps_queue.clear()

        steering_events = [
            {"type": "steering_applied", "content": s, "step": step_idx}
            for s in steering_texts
        ]

        # 预规划步骤推进分支（若无报错则按计划推进第 step_idx 步）
        if planned_steps_queue and not has_encountered_error:
            p_step = planned_steps_queue.pop(0)
            thought = f"执行预定排查方案第 {step_idx} 步：{p_step.get('title', '下发命令')}"
            command = p_step.get("command", "")
            step_title = p_step.get("title", f"步骤 {step_idx}")

            if writer is not None:
                try:
                    writer({"type": "token_delta", "step": step_idx, "delta": thought})
                except Exception as ex:
                    logger.warning("Failed to emit token_delta: %s", ex)

            thought_ev = {
                "type": "thought",
                "step": step_idx,
                "thought": thought,
                "content": thought,
                "title": step_title,
                "command": command,
            }
            return {
                "current_thought": thought,
                "current_command": command,
                "current_step_title": step_title,
                "planned_steps_queue": planned_steps_queue,
                "llm_call_count": llm_call_count,
                "total_tokens": total_tokens,
                "emitted_events": [thought_ev],
            }

        # 核心防死循环与进展指纹机制 (Progress Fingerprinting & Stalling Guard)
        guard_result, anti_loop_warning = check_loop_guard(state, step_idx, executed_steps)
        if guard_result is not None:
            return guard_result

        is_plan_confirmed = state.get("is_plan_confirmed", False)
        permission_mode = state.get("permission_mode", "request_approval")

        sys_prompt = build_system_prompt(
            host=host,
            is_root=is_root,
            os_family=os_family,
            terminal_buffer=terminal_buffer,
            core_memories_text=core_memories_text,
            step_idx=step_idx,
        )
        history_text, compactor_stats = self.context_compactor.format_prompt_history_with_stats(
            executed_steps=executed_steps,
            raw_react_history=react_history,
        )
        prompt_content, interactive_hint = build_user_prompt(
            user_prompt=user_prompt,
            step_idx=step_idx,
            history_text=history_text,
            anti_loop_warning=anti_loop_warning,
            has_encountered_error=has_encountered_error,
            last_error_detail=last_error_detail,
            os_family=os_family,
            is_root=is_root,
            react_history=react_history,
            executed_steps=executed_steps,
            is_plan_confirmed=is_plan_confirmed,
            permission_mode=permission_mode,
            steering_texts=steering_texts,
            images=images,
        )

        # 融入多轮历史对话上下文，消除上下文丢失断层并彻底剥离长篇排障报告污染
        clean_history = self.session_compactor.compact_session_history(session_history, user_prompt)
        conversation = build_conversation(sys_prompt, prompt_content, images, clean_history)

        # Token 预算估算与运行时监控（使用当前调用局部独立的 compactor_stats，杜绝单例并发串号）
        est_prompt_tokens = TokenBudgetEstimator.estimate_messages_tokens(conversation)
        logger.info(
            "ReAct Step %d: estimated prompt tokens=%d (history_budget=%s, stats=%s)",
            step_idx,
            est_prompt_tokens,
            self.context_compactor.max_history_tokens,
            compactor_stats,
        )

        thought_filter = None
        if writer is not None:
            def on_thought_delta(delta: str):
                try:
                    writer({"type": "token_delta", "step": step_idx, "delta": delta})
                except Exception as ex:
                    logger.warning("Failed to emit token_delta: %s", ex)

            thought_filter = StreamingThoughtFilter(on_thought_delta)

        def handle_token(token: str):
            if thought_filter:
                thought_filter.feed(token)

        reply = await self._safe_llm_chat(
            conversation,
            temperature=0.1,
            tools=self.tool_registry.specs(),
            on_token=handle_token if writer is not None else None,
            cancel_check=cancel_check,
        )

        if thought_filter:
            thought_filter.close()

        # 优先提取原生结构化 Tool Calling，无 Tool Calls 则平滑回退至纯文本正则解析
        raw_calls, reply_text = split_reply(reply)

        llm_call_count += 1
        prompt_tokens_est = est_prompt_tokens
        reply_tokens_est = TokenBudgetEstimator.estimate_tokens(reply_text or (str(reply) if reply else ""))
        total_tokens += (prompt_tokens_est + reply_tokens_est)

        tool_calls = parse_tool_calls(raw_calls)

        current_tool_name = None
        current_tool_args = None
        plan: Optional[str] = None
        if tool_calls:
            first_tool = tool_calls[0]
            invocation = resolve_tool_call(
                self.tool_registry,
                first_tool,
                step_idx,
                fallback_thought=reply_text.strip(),
            )
            current_tool_name = invocation["tool_name"]
            current_tool_args = invocation["tool_args"]
            thought = invocation["thought"]
            command = invocation["command"]
            step_title = invocation["step_title"]
            final_answer = invocation["final_answer"]
            plan = invocation["plan"]
        else:
            parsed = resolve_text_response(reply_text, step_idx)
            thought = parsed["thought"]
            command = parsed["command"]
            step_title = parsed["step_title"]
            final_answer = parsed["final_answer"]
            plan = parsed["plan"]

        # 核心防御：安装/变更类任务早熟虚假收敛拦截
        is_mutation = is_mutation_task(user_prompt)
        if (
            final_answer
            and not command
            and is_mutation
            and self._is_premature_mutation_answer(thought, final_answer, executed_steps)
        ):
            logger.warning(
                "Rejected premature Final_Answer for mutation task '%s': no mutation command executed yet and not confirmed satisfied",
                user_prompt,
            )
            if step_idx == 1 and not is_plan_confirmed:
                plan = plan or final_answer
                step_title = "方案确认"
            final_answer = None

        # 检查是否为前置方案确认状态（Step 1 针对变更/高危任务输出执行思路并征询用户开始执行）
        if self._check_plan_confirmation(state, step_idx, user_prompt, thought, command, final_answer, plan):
            return self._build_plan_confirmation_response(
                state=state,
                step_idx=step_idx,
                user_prompt=user_prompt,
                thought=thought,
                plan=plan,
                step_title=step_title,
                llm_call_count=llm_call_count,
                total_tokens=total_tokens,
            )

        # 丢失 Action 自动单步纠错补偿
        if not command and not final_answer:
            retry_result = await self._retry_missing_action(
                state=state,
                conversation=conversation,
                step_idx=step_idx,
                user_prompt=user_prompt,
                os_family=os_family,
                is_root=is_root,
                host=host,
                thought=thought,
                command=command,
                step_title=step_title,
                final_answer=final_answer,
                current_tool_name=current_tool_name,
                current_tool_args=current_tool_args,
                llm_call_count=llm_call_count,
                total_tokens=total_tokens,
                cancel_check=cancel_check,
            )
            thought = retry_result["thought"]
            command = retry_result["command"]
            step_title = retry_result["step_title"]
            final_answer = retry_result["final_answer"]
            current_tool_name = retry_result["current_tool_name"]
            current_tool_args = retry_result["current_tool_args"]
            llm_call_count = retry_result["llm_call_count"]
            total_tokens = retry_result["total_tokens"]

        # 只读查询类任务强收敛保护：单目标查询在拿到数据后立即收敛；多目标查询交由模型逐项完成后再收敛
        if not final_answer:
            query_result = self._try_converge_read_only_query(
                user_prompt=user_prompt,
                executed_steps=executed_steps,
                step_idx=step_idx,
                llm_call_count=llm_call_count,
                total_tokens=total_tokens,
            )
            if query_result is not None:
                return query_result

        return self._finalize_response(
            step_idx=step_idx,
            user_prompt=user_prompt,
            thought=thought,
            command=command,
            step_title=step_title,
            final_answer=final_answer,
            interactive_hint=interactive_hint,
            is_mutation=is_mutation,
            executed_steps=executed_steps,
            current_tool_name=current_tool_name,
            current_tool_args=current_tool_args,
            steering_events=steering_events,
            llm_call_count=llm_call_count,
            total_tokens=total_tokens,
        )
