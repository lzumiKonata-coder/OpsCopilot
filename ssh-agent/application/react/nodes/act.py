"""应用层 - ReAct Act 节点"""

import asyncio
import logging
import time
from typing import Any

from application.ports import ToolContext, ToolOperation
from application.react.cancel_context import get_cancel_check
from application.react.observation import extract_command_payload
from application.react.state import ReActGraphState

logger = logging.getLogger("smartops.application.react.nodes.act")


class ActNodeMixin:
    async def _node_act(self, state: ReActGraphState) -> dict[str, Any]:
        """Node 3: 受管机物理执行节点"""
        eff_cancel_check = state.get("cancel_check") or get_cancel_check()
        if eff_cancel_check and await eff_cancel_check():
            raise asyncio.CancelledError("客户端已主动取消排障流程")
        host = state["host"]
        command = state.get("current_command", "")
        step_idx = state.get("step_idx", 1)
        is_root_user = state.get("is_root", False)

        tool_name = state.get("current_tool_name")
        tool_args = state.get("current_tool_args") or {}
        tool = self.tool_registry.get(tool_name)
        operation = tool.spec.operation if tool is not None else ToolOperation.COMMAND
        session_id = state.get("session_id") or ""

        # 工具白名单防御：若非法工具绕过 Think 落到执行节点，降级为纯文本命令通道
        allowed_tool_names = state.get("allowed_tool_names")
        if tool_name and allowed_tool_names and tool_name not in allowed_tool_names:
            logger.warning(
                "Act node rejected tool '%s' outside routed whitelist %s",
                tool_name,
                sorted(allowed_tool_names),
            )
            if not command:
                display_obs = (
                    f"[工具调用被拒绝] 工具 `{tool_name}` 不在本轮路由白名单内，"
                    f"且未提供可降级的纯文本命令。请改用当前可用工具或输出 Action 文本。"
                )
                obs_ev = {
                    "type": "observation",
                    "step": step_idx,
                    "output": display_obs,
                    "stdout": display_obs,
                    "content": display_obs,
                    "raw_output": display_obs,
                }
                return {
                    "current_observation": display_obs,
                    "is_root": is_root_user,
                    "emitted_events": [obs_ev],
                }
            tool = None
            tool_args = {}
            tool_name = None
            operation = ToolOperation.COMMAND

        # 软预算上限守卫：检查整场任务剩余可用时长
        start_time = state.get("start_time") or time.time()
        max_run_seconds = state.get("max_run_seconds") or 600.0
        elapsed_seconds = time.time() - start_time
        remaining_seconds = max_run_seconds - elapsed_seconds

        if remaining_seconds <= 0:
            logger.warning("Act node: overall run budget exceeded (elapsed %.1fs >= max_run_seconds %.1fs)", elapsed_seconds, max_run_seconds)
            budget_conclusion = (
                f"[提示] 运维任务推演已达总运行时间上限 ({int(max_run_seconds)}s)。"
                f"为保护系统安全，指令未下发并已主动收敛终止。"
            )
            thought_ev = {
                "type": "thought",
                "step": step_idx,
                "thought": budget_conclusion,
                "content": budget_conclusion,
                "title": "运行时间超限终止",
                "status": "completed",
            }
            executed_steps = list(state.get("executed_steps") or [])
            executed_steps.append({
                "step": step_idx,
                "title": state.get("current_step_title", f"步骤 {step_idx}"),
                "command": command or f"({tool_name})",
                "tool_name": tool_name,
                "tool_call_id": state.get("current_tool_call_id"),
                "audit_decision": "TIMEOUT",
                "risk_level": "NORMAL",
                "status": "timeout",
                "observation": budget_conclusion,
                "analysis": "总运行时间预算耗尽，未下发指令。",
                "is_error": True,
            })
            return {
                "current_thought": budget_conclusion,
                "final_answer": budget_conclusion,
                "final_conclusion": budget_conclusion,
                "budget_exceeded": True,
                "is_success": False,
                "should_stop": True,
                "executed_steps": executed_steps,
                "emitted_events": [thought_ev],
            }

        if operation == ToolOperation.READ_FILE:
            path = tool_args.get("path", "")
            def _to_safe_int(val, default):
                try:
                    return int(val)
                except (ValueError, TypeError):
                    return default

            offset = _to_safe_int(tool_args.get("offset"), 1)
            limit = _to_safe_int(tool_args.get("limit"), 200)
            try:
                if self.ssh_gateway:
                    raw_obs = await self.ssh_gateway.read_file(host, path, offset=offset, limit=limit)
                else:
                    raw_obs = "(网关未初始化，无法读取文件)"
            except Exception as e:
                raw_obs = f"[读取失败] {str(e)}"
            clean_obs = self.context_compactor.truncate_observation(raw_obs)
            display_obs = clean_obs if clean_obs.strip() else "(文件为空)"

        elif operation == ToolOperation.EDIT_FILE:
            path = tool_args.get("path", "")
            target = tool_args.get("target", "")
            replacement = tool_args.get("replacement", "")
            try:
                if self.ssh_gateway:
                    success = await self.file_mutation_queue.run_with_lock(
                        host.connection_id,
                        path,
                        self.ssh_gateway.edit_file,
                        host,
                        path,
                        target,
                        replacement,
                    )
                    if success:
                        raw_obs = f"[文件局部修改成功] 已精确将远端文件 `{path}` 中的目标文本安全替换为新内容。"
                    else:
                        raw_obs = f"[文件局部修改失败] 远端文件 `{path}` 替换未生效。"
                else:
                    raw_obs = "(网关未初始化，无法修改文件)"
            except Exception as e:
                raw_obs = f"[文件局部修改异常] {str(e)}"
            clean_obs = self.context_compactor.truncate_observation(raw_obs)
            display_obs = clean_obs

        elif operation == ToolOperation.WRITE_FILE:
            path = tool_args.get("path", "")
            content_to_write = tool_args.get("content", "")
            try:
                if self.ssh_gateway:
                    success = await self.file_mutation_queue.run_with_lock(
                        host.connection_id,
                        path,
                        self.ssh_gateway.write_file,
                        host,
                        path,
                        content_to_write,
                    )
                    if success:
                        raw_obs = f"[文件写入成功] 已安全原子写入远端文件 `{path}` (共 {len(content_to_write)} 字节)。"
                    else:
                        raw_obs = f"[文件写入失败] 远端文件 `{path}` 写入异常。"
                else:
                    raw_obs = "(网关未初始化，无法写入文件)"
            except Exception as e:
                raw_obs = f"[文件写入异常] {str(e)}"
            clean_obs = self.context_compactor.truncate_observation(raw_obs)
            display_obs = clean_obs

        elif operation == ToolOperation.MCP:
            execute_fn = getattr(tool, "execute", None)
            if execute_fn is None:
                raw_obs = "[外部工具调用失败] 该工具未实现执行通道"
            else:
                try:
                    result = await execute_fn(
                        tool_args,
                        ToolContext(host=host, session_id=session_id, cancel_check=eff_cancel_check),
                    )
                    raw_obs = result.observation
                except Exception as e:
                    raw_obs = f"[外部工具调用失败] {str(e)}"
            clean_obs = self.context_compactor.truncate_observation(raw_obs)
            display_obs = clean_obs

        else:
            raw_obs = await self._execute_command_in_pty(
                host,
                command,
                wait_seconds=1.6,
                max_timeout=remaining_seconds,
                cancel_check=eff_cancel_check,
            )
            clean_obs = self.context_compactor.truncate_observation(raw_obs)

            # 从回显提示符动态修正 root 研判
            root_hint = self.detect_root_from_output(clean_obs)
            if root_hint is not None:
                is_root_user = root_hint

            payload_obs = extract_command_payload(command, clean_obs)
            display_obs = payload_obs if payload_obs else (clean_obs if clean_obs.strip() else "(无回显输出)")

        obs_ev = {
            "type": "observation",
            "step": step_idx,
            "output": display_obs,
            "stdout": display_obs,
            "content": display_obs,
            "raw_output": clean_obs,
        }
        return {
            "current_observation": clean_obs,
            "is_root": is_root_user,
            "emitted_events": [obs_ev],
        }
