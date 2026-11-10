"""应用层 - Think 节点：丢失 Action 的单步纠错补偿"""

import logging
from typing import Any

from application.react.state import ReActGraphState
from utils.text import TokenBudgetEstimator

from .llm import parse_text_response, parse_tool_calls, resolve_tool_call, split_reply

logger = logging.getLogger("smartops.application.react.nodes.think")


class RetryActionMixin:
    async def _retry_missing_action(
        self,
        state: ReActGraphState,
        conversation: list[dict[str, Any]],
        step_idx: int,
        user_prompt: str,
        os_family: str,
        is_root: bool,
        host: Any,
        thought: str,
        command: str | None,
        step_title: str | None,
        final_answer: str | None,
        current_tool_name: str | None,
        current_tool_args: dict[str, Any] | None,
        llm_call_count: int,
        total_tokens: int,
        cancel_check: Any | None,
        tool_schemas: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """丢失 Action 时自动单步纠错补偿，返回更新后的推演结果"""
        if any(k in thought for k in ["已安装", "无需", "最新版本", "已经存在", "正常运行", "目标达成"]):
            logger.info("Recognized completion/skip intention in thought: '%s'", thought[:80])
            final_answer = thought
        else:
            logger.info("Step %d missing Action for prompt '%s', re-prompting with strict single-step guidance", step_idx, user_prompt)
        correction_prompt = (
            f"【单步执行纠错提示】：\n"
            f"前序回复未包含有效的单行 Linux Shell 命令（Action）！当前任务为【{user_prompt}】，目前必须推进执行下一步。\n"
            f"受管系统环境为：{os_family}，当前用户身份为 {'root' if is_root else host.username}。\n"
            f"请严格按格式仅输出【格式 A】：\n"
            f"Thought: 简要说明下一步要做什么（单步）\n"
            f"Action: 具体单行 Linux Shell 命令\n"
            f"Step_Title: 步骤标题"
        )
        retry_conv = list(conversation)
        retry_conv.append({"role": "user", "content": correction_prompt})
        retry_reply = await self._safe_llm_chat(
            retry_conv,
            temperature=0.1,
            tools=tool_schemas,
            cancel_check=cancel_check,
        )
        retry_raw_calls, retry_reply_text = split_reply(retry_reply)

        usage = getattr(retry_reply, "usage", None) or {}
        if usage:
            logger.info(
                "ReAct Step %d retry cache stats: prompt=%d cached=%d hit_rate=%.2f%%",
                step_idx,
                usage.get("prompt_tokens", 0),
                usage.get("cached_tokens", 0),
                usage.get("cache_hit_rate", 0.0) * 100,
            )

        llm_call_count += 1
        retry_prompt_est = TokenBudgetEstimator.estimate_messages_tokens(retry_conv, tools=tool_schemas)
        retry_reply_est = TokenBudgetEstimator.estimate_tokens(
            retry_reply_text or (str(retry_reply) if retry_reply else "")
        )
        total_tokens += (retry_prompt_est + retry_reply_est)

        retry_tool_calls = parse_tool_calls(retry_raw_calls)
        allowed_tool_names = {
            schema.get("function", {}).get("name")
            for schema in tool_schemas
            if schema.get("function", {}).get("name")
        }

        if retry_tool_calls:
            rt_first = retry_tool_calls[0]
            rt_args = rt_first["arguments"]
            current_tool_name = rt_first["name"]
            current_tool_args = rt_args
            invocation = resolve_tool_call(
                self.tool_registry,
                rt_first,
                step_idx,
                fallback_thought=thought,
                fallback_to_args=False,
                allowed_tool_names=allowed_tool_names,
            )
            if invocation is not None:
                thought = invocation["thought"]
                command = invocation["command"]
                step_title = invocation["step_title"]
                final_answer = invocation["final_answer"]
                # 纠错重试沿用上一轮步骤标题，保持与原始行为一致
                if not rt_args.get("step_title") and command is not None:
                    step_title = state.get("current_step_title") or step_title
        else:
            parsed_retry = parse_text_response(retry_reply_text)
            thought = parsed_retry["thought"] or thought
            command = parsed_retry["action"]
            step_title = parsed_retry["title"] or step_title
            final_answer = parsed_retry["final_answer"]
        if command:
            final_answer = None

        return {
            "thought": thought,
            "command": command,
            "step_title": step_title,
            "final_answer": final_answer,
            "current_tool_name": current_tool_name,
            "current_tool_args": current_tool_args,
            "llm_call_count": llm_call_count,
            "total_tokens": total_tokens,
        }
