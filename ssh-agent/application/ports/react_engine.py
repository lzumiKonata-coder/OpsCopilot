"""应用层 - ReAct 引擎抽象契约"""

from typing import Any, Dict, List, Optional, Protocol, Tuple

from domain.entities import SSHConnection


class IReActEngine(Protocol):
    """ReAct 状态机引擎能力契约（供用例层依赖注入）"""

    checkpointer: Any
    steering_manager: Any
    session_tree_manager: Any

    async def _execute_command_in_pty(
        self,
        host: SSHConnection,
        command: str,
        wait_seconds: float = 1.6,
    ) -> str: ...

    async def fork_branch(
        self,
        parent_session_id: str,
        branch_point_step: int,
        new_branch_session_id: Optional[str] = None,
        branch_title: Optional[str] = None,
        new_prompt: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]: ...

    def get_compiled_graph(self) -> Any: ...

    async def get_session_checkpoints(self, session_id: str) -> List[Dict[str, Any]]: ...
