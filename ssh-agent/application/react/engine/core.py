"""应用层 - LangGraph ReAct 引擎核心（生命周期与检查点/分支管理）"""

import logging
from typing import Any
import uuid

from langgraph.checkpoint.memory import MemorySaver

from application.runtime import (
    RemoteFileMutationQueue,
    SessionCheckpointInfo,
    SessionTreeManager,
)
from application.ports import (
    ICommandAuditor,
    ILLMClient,
    ISSHGateway,
    ISSHTerminalSessionManager,
)
from application.react.compaction import (
    ReActContextCompactor,
    SessionCompactor,
    allocate_context_budgets,
    resolve_context_profile,
)
from application.react.engine.graph_builder import build_react_graph
from application.react.engine.llm_call import LLMCallMixin
from application.react.engine.node_registry import build_node_map
from application.react.engine.observation_analysis import ObservationAnalysisMixin
from application.react.engine.pty_exec import PTYExecMixin
from application.react.managers import (
    IApprovalManager,
    ISteeringManager,
    InMemoryApprovalManager,
    InMemorySteeringManager,
)
from application.react.nodes.act import ActNodeMixin
from application.react.nodes.approval_gate import ApprovalGateNodeMixin
from application.react.nodes.audit import AuditNodeMixin
from application.react.nodes.finish import FinishNodeMixin
from application.react.nodes.observe import ObserveAndReflectNodeMixin
from application.react.nodes.think import ThinkNodeMixin
from application.tool_registry import ToolRegistry
from utils.text import TokenBudgetEstimator

logger = logging.getLogger("smartops.application.react.engine")


class LangGraphReActEngine(
    LLMCallMixin,
    ObservationAnalysisMixin,
    PTYExecMixin,
    ThinkNodeMixin,
    AuditNodeMixin,
    ApprovalGateNodeMixin,
    ActNodeMixin,
    ObserveAndReflectNodeMixin,
    FinishNodeMixin,
):
    """
    基于 LangGraph 的智能运维 ReAct 状态机构建与执行器
    """

    def __init__(
        self,
        llm_client: ILLMClient,
        terminal_manager: ISSHTerminalSessionManager | None = None,
        terminal_gateway: Any | None = None,
        ssh_gateway: ISSHGateway | None = None,
        auditor: ICommandAuditor | None = None,
        approval_manager: IApprovalManager | None = None,
        approval_timeout: float = 120.0,
        context_window_size: int | None = None,
        max_observation_chars: int | None = None,
        context_compactor: ReActContextCompactor | None = None,
        session_compactor: SessionCompactor | None = None,
        checkpointer: Any | None = None,
        session_tree_manager: SessionTreeManager | None = None,
        steering_manager: ISteeringManager | None = None,
        tool_registry: ToolRegistry | None = None,
        file_mutation_queue: RemoteFileMutationQueue | None = None,
    ):
        self.llm_client = llm_client
        self.terminal_manager = terminal_manager
        self.terminal_gateway = terminal_gateway
        self.ssh_gateway = ssh_gateway
        self.auditor = auditor
        self.approval_manager = approval_manager if approval_manager is not None else InMemoryApprovalManager()
        self.approval_timeout = approval_timeout
        self.steering_manager = steering_manager if steering_manager is not None else InMemorySteeringManager()
        self.file_mutation_queue = (
            file_mutation_queue if file_mutation_queue is not None else RemoteFileMutationQueue()
        )
        if tool_registry is None:
            # 默认使用空注册表，生产环境必须由组合根注入已装配的共享注册表
            tool_registry = ToolRegistry()
        self.tool_registry = tool_registry
        # 模型上下文容量自适应画像解析
        llm_model = getattr(llm_client, "model", None)
        profile = resolve_context_profile(llm_model)

        # None 表示未显式配置，交由模型上下文画像决定活跃窗口
        eff_window_size = (
            context_window_size
            if context_window_size is not None
            else profile["default_window_size"]
        )
        eff_max_obs = (
            max_observation_chars
            if max_observation_chars is not None
            else profile["max_observation_chars"]
        )

        # 共享输入预算池：工具 schema 与输出预留只扣一次，再统一分配给两个历史消费方
        tool_tokens = TokenBudgetEstimator.estimate_tools_tokens(self.tool_registry.specs())
        step_history_budget, session_history_budget = allocate_context_budgets(profile, tool_tokens)
        self.context_compactor = context_compactor or ReActContextCompactor(
            window_size=eff_window_size,
            max_observation_chars=eff_max_obs,
            max_history_tokens=step_history_budget,
            model_name=llm_model,
        )
        self.session_compactor = session_compactor or SessionCompactor(
            max_recent_turns=profile["max_recent_turns"],
            max_session_tokens=session_history_budget,
            model_name=llm_model,
        )
        self.checkpointer = checkpointer if checkpointer is not None else MemorySaver()
        self.session_tree_manager = session_tree_manager if session_tree_manager is not None else SessionTreeManager()
        self._compiled_graph = None
        # 客户端能力探测只需在初始化时执行一次，避免每次推演都做签名反射
        self._llm_supports_tools, self._llm_supports_on_token = self._detect_llm_capabilities(llm_client)

    async def get_session_checkpoints(self, session_id: str) -> list[dict[str, Any]]:
        """时间旅行：获取指定会话所有历史检查点状态与物理执行快照"""
        graph = self.get_compiled_graph()
        config = {"configurable": {"thread_id": session_id}}
        checkpoints: list[dict[str, Any]] = []
        try:
            async for state_snapshot in graph.aget_state_history(config):
                vals = state_snapshot.values or {}
                cid = state_snapshot.config.get("configurable", {}).get("checkpoint_id", "")
                next_node = list(state_snapshot.next) if state_snapshot.next else []
                executed = vals.get("executed_steps") or []
                info = SessionCheckpointInfo(
                    checkpoint_id=cid,
                    session_id=session_id,
                    step_idx=vals.get("step_idx", 1),
                    node_name=next_node[0] if next_node else "",
                    node_next=next_node,
                    command=vals.get("current_command"),
                    thought=vals.get("current_thought", ""),
                    title=vals.get("current_step_title", ""),
                    step_title=vals.get("current_step_title", ""),
                    observation=vals.get("current_observation", ""),
                    reflection=vals.get("current_reflection", ""),
                    is_normal=vals.get("is_step_normal", True),
                    executed_steps_count=len(executed),
                    has_final_answer=bool(vals.get("final_answer")),
                )
                checkpoints.append(info.to_dict())
        except Exception as e:
            logger.warning("Failed to retrieve checkpoints for session %s: %s", session_id, e)
        checkpoints.reverse()
        return checkpoints

    async def get_step_state(self, session_id: str, step_idx: int) -> dict[str, Any] | None:
        """获取指定步骤完成时的状态机内部状态快照"""
        graph = self.get_compiled_graph()
        config = {"configurable": {"thread_id": session_id}}
        target_state: dict[str, Any] | None = None
        try:
            async for state_snapshot in graph.aget_state_history(config):
                vals = state_snapshot.values or {}
                executed = vals.get("executed_steps") or []
                if len(executed) == step_idx:
                    target_state = dict(vals)
                    break
        except Exception as e:
            logger.warning("Failed to query step state: %s", e)
        return target_state

    async def fork_branch(
        self,
        parent_session_id: str,
        branch_point_step: int,
        new_branch_session_id: str | None = None,
        branch_title: str | None = None,
        new_prompt: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """从父会话指定步骤分叉出新的独立排障分支，状态完全隔离"""
        parent_state = await self.get_step_state(parent_session_id, branch_point_step)
        if not parent_state:
            graph = self.get_compiled_graph()
            cur = await graph.aget_state({"configurable": {"thread_id": parent_session_id}})
            parent_state = cur.values if cur else {}

        branch_id = new_branch_session_id or f"{parent_session_id}_branch_{uuid.uuid4().hex[:6]}"
        forked_state = self.session_tree_manager.fork_state_for_branch(
            parent_state=parent_state,
            branch_session_id=branch_id,
            branch_point_step=branch_point_step,
            new_prompt=new_prompt,
        )
        self.session_tree_manager.register_branch(
            parent_session_id=parent_session_id,
            branch_session_id=branch_id,
            branch_point_step=branch_point_step,
            branch_title=branch_title,
        )
        return branch_id, forked_state

    def build_graph(self, checkpointer: Any | None = None) -> Any:
        """基于节点注册表构造并编译状态图"""
        compiled = build_react_graph(build_node_map(self), checkpointer=checkpointer)
        if checkpointer is not None and checkpointer is self.checkpointer:
            self._compiled_graph = compiled
        return compiled

    def get_compiled_graph(self) -> Any:
        """获取复用默认 Checkpointer 编译的全局状态图"""
        if self._compiled_graph is None:
            self._compiled_graph = self.build_graph(checkpointer=self.checkpointer)
        return self._compiled_graph
