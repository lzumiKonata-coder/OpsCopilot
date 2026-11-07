"""
应用层 - 会话树与检查点分支管理器 (Session Tree & Branch Manager)
借鉴 Pi 架构的树形分支 (Session Tree & Branching) 与时间旅行 (Time-Travel) 设计。
支持：
1. 捕获 ReAct 状态机各步骤快照与元数据 (Checkpoint Info)；
2. 会话在任意历史步骤分叉出探索性新分支 (Session Branching)；
3. 父子分支树形拓扑追踪与可视化结构呈现 (Session Tree Topology)；
4. 状态安全隔离与时间旅行回溯。
"""

from dataclasses import asdict, dataclass, field
import logging
import time
from typing import Any

logger = logging.getLogger("smartops.application.session_tree")


@dataclass
class SessionCheckpointInfo:
    """检查点快照元数据（表达特定步数完成时的物理与推演状态）"""
    checkpoint_id: str
    session_id: str
    step_idx: int = 1
    node_name: str = ""
    node_next: list[str] = field(default_factory=list)
    step_title: str = ""
    title: str = ""
    command: str | None = None
    thought: str = ""
    observation: str = ""
    reflection: str = ""
    is_normal: bool = True
    executed_steps_count: int = 0
    has_final_answer: bool = False
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if not d.get("title") and d.get("step_title"):
            d["title"] = d["step_title"]
        elif not d.get("step_title") and d.get("title"):
            d["step_title"] = d["title"]
        return d


@dataclass
class SessionTreeNode:
    """会话树节点"""
    session_id: str
    parent_session_id: str | None = None
    branch_point_step: int | None = None
    title: str = "根会话"
    created_at: float = field(default_factory=time.time)
    children: list["SessionTreeNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "parent_session_id": self.parent_session_id,
            "branch_point_step": self.branch_point_step,
            "title": self.title,
            "created_at": self.created_at,
            "children": [child.to_dict() for child in self.children],
        }


class SessionTreeManager:
    """
    会话分支拓扑与分叉管理器
    负责多分支的父子关系注册、分叉状态复制与树状结构检索
    """

    def __init__(self):
        # parent_id -> list of child branch_session_ids
        self._branches: dict[str, list[str]] = {}
        # session_id -> metadata
        self._metadata: dict[str, dict[str, Any]] = {}

    def register_session(
        self,
        session_id: str,
        title: str = "排障会话",
        parent_session_id: str | None = None,
        branch_point_step: int | None = None,
    ) -> None:
        """注册会话及其父子关系"""
        if session_id not in self._metadata:
            self._metadata[session_id] = {
                "session_id": session_id,
                "title": title,
                "parent_session_id": parent_session_id,
                "branch_point_step": branch_point_step,
                "created_at": time.time(),
            }

        if parent_session_id:
            if parent_session_id not in self._branches:
                self._branches[parent_session_id] = []
            if session_id not in self._branches[parent_session_id]:
                self._branches[parent_session_id].append(session_id)

    def register_branch(
        self,
        parent_session_id: str,
        branch_session_id: str,
        branch_point_step: int,
        branch_title: str | None = None,
    ) -> None:
        """注册新分叉分支"""
        parent_meta = self._metadata.get(parent_session_id, {})
        default_title = f"{parent_meta.get('title', '主分支')} (分叉自第 {branch_point_step} 步)"
        title = branch_title or default_title
        self.register_session(
            session_id=branch_session_id,
            title=title,
            parent_session_id=parent_session_id,
            branch_point_step=branch_point_step,
        )

    def get_branches(self, session_id: str) -> list[dict[str, Any]]:
        """获取指定会话直接分叉出的子分支列表"""
        child_ids = self._branches.get(session_id, [])
        return [self._metadata[cid] for cid in child_ids if cid in self._metadata]

    def remove_session(self, session_id: str) -> None:
        """移除会话运行期元数据与分支关系，避免内存长期累积"""
        self._metadata.pop(session_id, None)
        self._branches.pop(session_id, None)
        for children in self._branches.values():
            if session_id in children:
                children.remove(session_id)

    def get_tree(self, root_session_id: str) -> dict[str, Any]:
        """获取从 root_session_id 起始的完整分支拓扑树"""
        root_meta = self._metadata.get(root_session_id, {
            "session_id": root_session_id,
            "title": "排障会话",
            "parent_session_id": None,
            "branch_point_step": None,
            "created_at": time.time(),
        })

        node = SessionTreeNode(
            session_id=root_session_id,
            parent_session_id=root_meta.get("parent_session_id"),
            branch_point_step=root_meta.get("branch_point_step"),
            title=root_meta.get("title", "根会话"),
            created_at=root_meta.get("created_at", time.time()),
        )

        def build_children(curr_node: SessionTreeNode) -> None:
            children_ids = self._branches.get(curr_node.session_id, [])
            for cid in children_ids:
                cmeta = self._metadata.get(cid, {})
                cnode = SessionTreeNode(
                    session_id=cid,
                    parent_session_id=curr_node.session_id,
                    branch_point_step=cmeta.get("branch_point_step"),
                    title=cmeta.get("title", f"分支 {cid}"),
                    created_at=cmeta.get("created_at", time.time()),
                )
                curr_node.children.append(cnode)
                build_children(cnode)

        build_children(node)
        return node.to_dict()

    @staticmethod
    def fork_state_for_branch(
        parent_state: dict[str, Any],
        branch_session_id: str,
        branch_point_step: int,
        new_prompt: str | None = None,
    ) -> dict[str, Any]:
        """
        根据父会话在 branch_point_step 步骤的状态快照，派生分支初始状态：
        1. 仅保留前序 1..branch_point_step 步的执行记录与推演历史事实；
        2. 绝不盲目全量复制父状态中的配置参数（权限模式、白名单、软预算、转向消息等由当前分支请求决定）；
        3. 重置终态标识，使分支状态机从 branch_point_step + 1 继续独立推演；
        4. 状态隔离，保证后续推演不会污染或破坏父分支。
        """
        # 历史记录精确截取至分叉点
        executed_steps = list(parent_state.get("executed_steps") or [])
        react_history = list(parent_state.get("react_history") or [])

        trimmed_steps = executed_steps[:branch_point_step]
        trimmed_history = react_history[:branch_point_step]

        forked: dict[str, Any] = {
            "session_id": branch_session_id,
            "host": parent_state.get("host"),
            "user_prompt": new_prompt or parent_state.get("user_prompt", ""),
            "executed_steps": trimmed_steps,
            "react_history": trimmed_history,
            "step_idx": len(trimmed_steps) + 1,
            "os_family": parent_state.get("os_family", "Linux (通用环境)"),
            "is_root": parent_state.get("is_root", False),
            "current_command": None,
            "current_thought": f"从父会话分叉（切入点：第 {branch_point_step} 步），开始新假设分支推演。",
            "current_tool_name": None,
            "current_tool_call_id": None,
            "current_tool_args": None,
            "current_step_title": f"分支步骤 {len(trimmed_steps) + 1}",
            "current_observation": "",
            "current_reflection": "",
            "final_answer": None,
            "final_conclusion": None,
            "is_success": False,
            "should_stop": False,
            "consecutive_errors": 0,
            "start_time": time.time(),
            "llm_call_count": 0,
            "total_tokens": 0,
            "has_encountered_error": False,
            "last_error_detail": None,
            "is_blocked": False,
            "blocked_reason": None,
            "pending_approval_id": None,
            "emitted_events": [],
        }

        if new_prompt:
            forked["user_prompt"] = new_prompt

        return forked
