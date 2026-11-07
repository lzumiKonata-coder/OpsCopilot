"""应用层 - ReAct 状态图节点注册表"""

from typing import Any, Callable

NodeCallable = Callable[..., Any]

NODE_THINK = "think_node"
NODE_AUDIT = "audit_node"
NODE_APPROVAL_GATE = "approval_gate_node"
NODE_ACT = "act_node"
NODE_OBSERVE = "observe_and_reflect_node"
NODE_FINISH = "finish_node"


def build_node_map(engine: Any) -> dict[str, NodeCallable]:
    """将引擎能力映射为状态图节点 callable，使图编排与节点实现解耦"""
    return {
        NODE_THINK: engine._node_think,
        NODE_AUDIT: engine._node_audit,
        NODE_APPROVAL_GATE: engine._node_approval_gate,
        NODE_ACT: engine._node_act,
        NODE_OBSERVE: engine._node_observe_and_reflect,
        NODE_FINISH: engine._node_finish,
    }
