"""应用层 - ReAct 引擎抽象契约"""

from abc import ABC, abstractmethod
from typing import Any

from domain.entities import SSHConnection


class IReActEngine(ABC):
    """ReAct 状态机引擎能力契约（供用例层依赖注入）"""

    checkpointer: Any
    steering_manager: Any
    session_tree_manager: Any

    @abstractmethod
    async def _execute_command_in_pty(
        self,
        host: SSHConnection,
        command: str,
        wait_seconds: float = 1.6,
    ) -> str: ...

    @abstractmethod
    async def fork_branch(
        self,
        parent_session_id: str,
        branch_point_step: int,
        new_branch_session_id: str | None = None,
        branch_title: str | None = None,
        new_prompt: str | None = None,
    ) -> tuple[str, dict[str, Any]]: ...

    @abstractmethod
    def get_compiled_graph(self) -> Any: ...

    @abstractmethod
    async def get_session_checkpoints(self, session_id: str) -> list[dict[str, Any]]: ...
