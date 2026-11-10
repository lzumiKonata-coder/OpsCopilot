"""应用层 - ReAct Think 节点：LLM 回复解析与 ToolCall 归一化"""

import json
from typing import Any

from application.react.text_utils import parse_react_response
from utils.shell import clean_extracted_command


def split_reply(reply: Any) -> tuple[Any, str]:
    """拆分 LLM 回复为 (原生 ToolCalls, 文本内容)"""
    if isinstance(reply, dict):
        return reply.get("tool_calls"), reply.get("content") or ""
    return (
        getattr(reply, "tool_calls", None),
        getattr(reply, "content", None) or (str(reply) if reply is not None else ""),
    )


def parse_tool_calls(raw_calls: Any) -> list[dict[str, Any]]:
    """将原生 ToolCall 统一归一化为 {name, arguments} 列表"""
    tool_calls: list[dict[str, Any]] = []
    if not raw_calls:
        return tool_calls

    for tc in raw_calls:
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            t_name = fn.get("name") or tc.get("name")
            t_args = fn.get("arguments") or tc.get("arguments") or {}
        else:
            fn = getattr(tc, "function", None)
            t_name = getattr(fn, "name", None) or getattr(tc, "name", None)
            t_args = getattr(fn, "arguments", None) or getattr(tc, "arguments", None) or {}
        if isinstance(t_args, str):
            try:
                t_args = json.loads(t_args)
            except Exception:
                t_args = {}
        tool_calls.append({"name": t_name, "arguments": t_args})
    return tool_calls


def resolve_tool_call(
    tool_registry: Any,
    tool_call: dict[str, Any],
    step_idx: int,
    fallback_thought: str,
    fallback_to_args: bool = True,
    allowed_tool_names: set[str] | None = None,
) -> dict[str, Any] | None:
    """归一化工具调用参数；白名单外或未注册工具按调用方要求降级"""
    name = tool_call.get("name")
    args = tool_call.get("arguments") or {}

    tool = tool_registry.get(name)

    # 动态路由白名单校验：仅拦截“已注册但本轮未路由”的工具；未注册工具沿用纯文本兜底语义
    if tool is not None and allowed_tool_names is not None and name not in allowed_tool_names:
        return None

    if tool is not None:
        invocation = tool.normalize(args, step_idx, fallback_thought=fallback_thought)
        thought = invocation.thought
        command = invocation.command
        step_title = invocation.step_title
        final_answer = invocation.final_answer
        plan = invocation.plan
    elif fallback_to_args:
        # 未注册工具的兼容兜底：保持既有纯文本参数解析行为
        thought = args.get("thought", "")
        command = args.get("command")
        step_title = args.get("step_title") or f"步骤 {step_idx}"
        final_answer = args.get("final_answer")
        plan = None
    else:
        return None

    if command:
        command = clean_extracted_command(command)

    return {
        "tool_name": name,
        "tool_args": args,
        "thought": thought,
        "command": command,
        "step_title": step_title,
        "final_answer": final_answer,
        "plan": plan,
    }


def parse_text_response(reply_text: str) -> dict[str, Any]:
    """纯文本格式的原始解析结果"""
    return parse_react_response(reply_text)


def resolve_text_response(reply_text: str, step_idx: int) -> dict[str, Any]:
    """无 ToolCalls 时回退至纯文本正则解析（缺失字段按步骤兜底）"""
    parsed = parse_react_response(reply_text)
    return {
        "thought": parsed["thought"] or f"推进排查第 {step_idx} 步",
        "command": parsed["action"],
        "step_title": parsed["title"] or f"步骤 {step_idx}",
        "final_answer": parsed["final_answer"],
        "plan": parsed.get("plan"),
    }
