"""应用层 - ReAct Observe & Reflect 节点"""

import logging
from typing import Any

from application.ports import ToolOperation
from domain.policies.intent import is_inspection_command, is_mutation_task
from application.react.observation import detect_command_error
from application.react.state import ReActGraphState

logger = logging.getLogger("smartops.application.react.nodes.observe")


class ObserveAndReflectNodeMixin:
    async def _node_observe_and_reflect(self, state: ReActGraphState) -> dict[str, Any]:
        """Node 4: 回显事实研判与反思节点"""
        command = state.get("current_command", "")
        clean_obs = state.get("current_observation", "")
        step_idx = state.get("step_idx", 1)
        thought = state.get("current_thought", "")
        step_title = state.get("current_step_title", f"步骤 {step_idx}")
        planned_steps_queue = list(state.get("planned_steps_queue") or [])
        consecutive_errors = state.get("consecutive_errors", 0)
        has_encountered_error = state.get("has_encountered_error", False)
        last_error_detail = state.get("last_error_detail")
        executed_steps = list(state.get("executed_steps") or [])
        react_history = list(state.get("react_history") or [])
        react_messages = list(state.get("react_messages") or [])

        tool_name = state.get("current_tool_name")
        tool_args = state.get("current_tool_args") or {}
        tool = self.tool_registry.get(tool_name)
        operation = tool.spec.operation if tool is not None else ToolOperation.COMMAND

        if operation == ToolOperation.READ_FILE:
            path = tool_args.get("path", "")
            if "[读取失败]" in clean_obs or "failed" in clean_obs.lower():
                has_encountered_error = True
                consecutive_errors += 1
                last_error_detail = f"读取远端文件 `{path}` 异常: {clean_obs[:200]}"
                analysis = f"[提示] 远端文件 `{path}` 读取失败，文件可能不存在或操作无权限。"
                is_step_normal = False
            else:
                consecutive_errors = 0
                has_encountered_error = False
                last_error_detail = None
                analysis = f"成功通过只读静态通道读取远端文件 `{path}`，已获取配置与状态信息。"
                is_step_normal = True

        elif operation == ToolOperation.EDIT_FILE:
            path = tool_args.get("path", "")
            if "[文件局部修改成功]" in clean_obs:
                consecutive_errors = 0
                has_encountered_error = False
                last_error_detail = None
                analysis = f"成功通过受锁保护的安全通道精确替换远端文件 `{path}` 的目标配置片段。"
                is_step_normal = True
            else:
                has_encountered_error = True
                consecutive_errors += 1
                last_error_detail = f"修改远端文件 `{path}` 失败: {clean_obs[:200]}"
                analysis = f"[警告] 修改远端文件 `{path}` 失败，目标待替换文本可能不匹配或文件不可写。"
                is_step_normal = False

        elif operation == ToolOperation.WRITE_FILE:
            path = tool_args.get("path", "")
            if "[文件写入成功]" in clean_obs:
                consecutive_errors = 0
                has_encountered_error = False
                last_error_detail = None
                analysis = f"成功通过受锁保护的安全文件通道原子写入远端文件 `{path}`。"
                is_step_normal = True
            else:
                has_encountered_error = True
                consecutive_errors += 1
                last_error_detail = f"写入远端文件 `{path}` 失败: {clean_obs[:200]}"
                analysis = f"[警告] 写入远端文件 `{path}` 失败，目标路径可能只读或空间不足。"
                is_step_normal = False

        elif operation == ToolOperation.MCP:
            if "[外部工具调用失败]" in clean_obs or "[外部工具调用已取消]" in clean_obs:
                has_encountered_error = True
                consecutive_errors += 1
                last_error_detail = f"外部工具 `{tool_name}` 调用失败: {clean_obs[:200]}"
                analysis = f"[警告] 外部工具 `{tool_name}` 调用未成功，请研判错误原因或改用等效内置通道。"
                is_step_normal = False
            else:
                consecutive_errors = 0
                has_encountered_error = False
                last_error_detail = None
                analysis = f"成功调用外部工具 `{tool_name}`，已获取返回结果。"
                is_step_normal = True

        else:
            # 核心：物理回显错误检测与状态机自愈触发研判（智能区分日志查询、工具探测与执行崩溃）
            err_msg = detect_command_error(command, clean_obs)
            if err_msg:
                # 区分：若用户目标为安装/部署类任务，而当前执行的是只读探查命令（如 docker --version, which docker），
                # 回显提示未找到命令是完全符合预期的探查事实（证明尚未安装），绝非导致计划崩溃的恶性错误！
                user_prompt = state.get("user_prompt", "")
                is_mutation = is_mutation_task(user_prompt)
                is_inspecting = is_inspection_command(command)
                is_cmd_not_found = any(k in err_msg for k in ["command not found", "未找到命令", "找不到命令"])

                if is_mutation and is_inspecting and is_cmd_not_found:
                    consecutive_errors = 0
                    has_encountered_error = False
                    last_error_detail = None
                    analysis = f"探测指令 `{command}` 确认：目标系统当前未安装该软件（未找到命令），确认为全新安装场景，下一步执行安装与依赖准备流程。"
                    is_step_normal = True
                else:
                    has_encountered_error = True
                    consecutive_errors += 1
                    last_error_detail = f"指令 `{command}` 物理执行失败：{err_msg} (回显: {clean_obs[:300]})"
                    # 核心铁律：一旦发现执行报错，立即废弃所有剩余预定步骤，强制启动动态自愈！
                    planned_steps_queue.clear()
                    analysis = f"[警告] 指令执行异常：{err_msg}。预定计划在目标机失效，ReAct 立即启动动态自愈，研判真实物理环境并重构排查链路。"
                    is_step_normal = False
            else:
                consecutive_errors = 0
                has_encountered_error = False
                last_error_detail = None
                analysis = await self.analyze_observation(command, clean_obs)
                is_step_normal = True

        reflect_ev = {
            "type": "reflection",
            "step": step_idx,
            "analysis": analysis,
            "is_normal": is_step_normal,
        }

        executed_steps.append({
            "step": step_idx,
            "thought": thought,
            "title": step_title,
            "command": command,
            "tool_name": tool_name,
            "tool_call_id": state.get("current_tool_call_id"),
            "observation": clean_obs,
            "analysis": analysis,
            "is_error": not is_step_normal,
        })

        react_history.append(
            f"Step {step_idx}:\n"
            f"- Thought: {thought}\n"
            f"- Action: `{command}`\n"
            f"- Observation:\n{clean_obs}\n"
            f"- Reflection: {analysis}"
        )
        # 追加式消息历史：Observation 作为独立消息追加，后续轮次不再重写历史前缀
        react_messages.append({
            "role": "user",
            "content": f"Observation:\n{clean_obs or '(无回显)'}\n\nReflection: {analysis}",
        })

        # 连续失败熔断保护（防止死循环）
        should_stop = False
        if consecutive_errors >= 3:
            should_stop = True
            analysis += "（连续多次探测异常，触发状态机安全熔断机制）"

        return {
            "step_idx": step_idx + 1,
            "planned_steps_queue": planned_steps_queue,
            "consecutive_errors": consecutive_errors,
            "has_encountered_error": has_encountered_error,
            "last_error_detail": last_error_detail,
            "current_reflection": analysis,
            "is_step_normal": is_step_normal,
            "executed_steps": executed_steps,
            "react_history": react_history,
            "react_messages": react_messages,
            "should_stop": should_stop,
            "emitted_events": [reflect_ev],
        }
