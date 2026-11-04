"""应用层 - Agent 会话生命周期用例 (chat_session)"""

import logging
from typing import Any, List, Optional

from application.ports import IChatSessionRepository, ISSHConnectionRepository
from domain.entities import ChatSession
from domain.rules import EntityNotFoundError

logger = logging.getLogger("smartops.application.usecases.agent_session")


class CreateAgentSessionUseCase:
    """用例：为目标主机创建新的 Agent 会话 (chat_session)"""

    def __init__(self, session_repo: IChatSessionRepository, host_repo: ISSHConnectionRepository):
        self.session_repo = session_repo
        self.host_repo = host_repo

    async def execute(self, host_identifier: str, title: str = "新会话", user_id: str = "default") -> ChatSession:
        host = await self.host_repo.get_by_identifier(host_identifier)
        if not host:
            raise EntityNotFoundError(f"Host '{host_identifier}' not found.")
        new_session = ChatSession(
            agent_id=host.connection_id,
            user_id=user_id,
            title=title or "新会话",
            messages=[],
        )
        return await self.session_repo.save(new_session)


class ListAgentSessionsUseCase:
    """用例：查询目标主机的历史会话列表（按活跃时间降序）"""

    def __init__(self, session_repo: IChatSessionRepository, host_repo: ISSHConnectionRepository):
        self.session_repo = session_repo
        self.host_repo = host_repo

    async def execute(self, host_identifier: str, limit: int = 50) -> List[ChatSession]:
        host = await self.host_repo.get_by_identifier(host_identifier)
        if not host:
            raise EntityNotFoundError(f"Host '{host_identifier}' not found.")
        return await self.session_repo.list_by_host(host.connection_id, limit=limit)


class GetAgentSessionUseCase:
    """用例：获取指定会话的完整历史消息详情"""

    def __init__(self, session_repo: IChatSessionRepository):
        self.session_repo = session_repo

    async def execute(self, session_id: str) -> Optional[ChatSession]:
        return await self.session_repo.get_by_id(session_id)


class DeleteAgentSessionUseCase:
    """用例：删除指定会话，并清理该会话的运行期检查点与分支元数据"""

    def __init__(
        self,
        session_repo: IChatSessionRepository,
        checkpointer: Optional[Any] = None,
        session_tree_manager: Optional[Any] = None,
    ):
        self.session_repo = session_repo
        self.checkpointer = checkpointer
        self.session_tree_manager = session_tree_manager

    async def execute(self, session_id: str) -> bool:
        deleted = await self.session_repo.delete(session_id)
        if deleted:
            self._clear_runtime_state(session_id)
        return deleted

    def _clear_runtime_state(self, session_id: str) -> None:
        """清理进程内检查点与会话树节点，防止内存只增不减"""
        if self.session_tree_manager is not None:
            try:
                self.session_tree_manager.remove_session(session_id)
            except Exception as exc:
                logger.warning("Failed to remove session tree node %s: %s", session_id, exc)
        if self.checkpointer is not None:
            try:
                delete_thread = getattr(self.checkpointer, "delete_thread", None)
                if callable(delete_thread):
                    delete_thread(session_id)
            except Exception as exc:
                logger.warning("Failed to delete checkpoints for session %s: %s", session_id, exc)
