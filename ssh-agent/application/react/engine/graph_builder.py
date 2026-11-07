"""应用层 - ReAct 状态图构建与编译"""

from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from application.react.engine.node_registry import (
    NODE_ACT,
    NODE_APPROVAL_GATE,
    NODE_AUDIT,
    NODE_FINISH,
    NODE_OBSERVE,
    NODE_THINK,
)
from application.react.state import ReActGraphState

NodeMap = dict[str, Callable[..., Any]]


def build_react_graph(nodes: NodeMap, checkpointer: Any | None = None) -> Any:
    """基于显式节点注册表构造并编译 LangGraph 状态机图"""
    builder = StateGraph(ReActGraphState)

    # 1. 注册核心状态节点
    for name, node in nodes.items():
        builder.add_node(name, node)

    # 2. 编排边与条件转移
    builder.add_edge(START, NODE_THINK)

    def route_after_think(state: ReActGraphState) -> str:
        if state.get("pending_approval_id"):
            return NODE_APPROVAL_GATE
        if state.get("should_stop") or state.get("final_answer") or not state.get("current_command"):
            return NODE_FINISH
        return NODE_AUDIT

    builder.add_conditional_edges(
        NODE_THINK,
        route_after_think,
        {NODE_AUDIT: NODE_AUDIT, NODE_FINISH: NODE_FINISH, NODE_APPROVAL_GATE: NODE_APPROVAL_GATE},
    )

    def route_after_audit(state: ReActGraphState) -> str:
        if state.get("is_blocked") or state.get("should_stop"):
            return NODE_FINISH
        if state.get("pending_approval_id"):
            return NODE_APPROVAL_GATE
        return NODE_ACT

    builder.add_conditional_edges(
        NODE_AUDIT,
        route_after_audit,
        {NODE_ACT: NODE_ACT, NODE_FINISH: NODE_FINISH, NODE_APPROVAL_GATE: NODE_APPROVAL_GATE},
    )

    def route_after_approval_gate(state: ReActGraphState) -> str:
        if state.get("should_stop"):
            return NODE_FINISH
        if not state.get("current_command"):
            # 用户拒绝执行高危指令或超时，不进入 act_node，直接返回 think_node 自适应重思安全替代方案
            return NODE_THINK
        return NODE_ACT

    builder.add_conditional_edges(
        NODE_APPROVAL_GATE,
        route_after_approval_gate,
        {NODE_ACT: NODE_ACT, NODE_FINISH: NODE_FINISH, NODE_THINK: NODE_THINK},
    )

    def route_after_act(state: ReActGraphState) -> str:
        if state.get("should_stop") or state.get("budget_exceeded"):
            return NODE_FINISH
        return NODE_OBSERVE

    builder.add_conditional_edges(
        NODE_ACT,
        route_after_act,
        {NODE_OBSERVE: NODE_OBSERVE, NODE_FINISH: NODE_FINISH},
    )

    def route_after_reflect(state: ReActGraphState) -> str:
        if state.get("should_stop"):
            return NODE_FINISH
        max_steps = state.get("max_steps")
        if max_steps is not None and state.get("step_idx", 1) > max_steps:
            return NODE_FINISH
        return NODE_THINK

    builder.add_conditional_edges(
        NODE_OBSERVE,
        route_after_reflect,
        {NODE_THINK: NODE_THINK, NODE_FINISH: NODE_FINISH},
    )

    builder.add_edge(NODE_FINISH, END)

    return builder.compile(checkpointer=checkpointer)
