"""应用层端口 - 数据仓储契约"""

from abc import ABC, abstractmethod

from domain.entities import (
    ChatMessage,
    ChatMilestone,
    ChatSession,
    CoreMemory,
    LongTermMemory,
    SSHConnection,
    SSHSessionLog,
)


class ISSHConnectionRepository(ABC):
    """
    SSH 连接与资产仓储端口契约 (Driven Port)
    """

    @abstractmethod
    async def save(self, host: SSHConnection) -> SSHConnection:
        """持久化保存或更新主机资产与高级配置"""
        pass

    @abstractmethod
    async def get_by_id(self, connection_id: str) -> SSHConnection | None:
        """根据唯一标识获取主机资产"""
        pass

    @abstractmethod
    async def get_by_identifier(self, identifier: str) -> SSHConnection | None:
        """根据 connection_id、主机名 connection_name 或 IP 智能检索目标主机"""
        pass

    @abstractmethod
    async def list_all(self, skip: int = 0, limit: int = 100) -> list[SSHConnection]:
        """分页获取主机资产列表 (过滤已删除 deleted=1)"""
        pass

    @abstractmethod
    async def delete(self, connection_id: str) -> bool:
        """软删除或物理删除指定主机资产"""
        pass


class ISSHSessionLogRepository(ABC):
    """
    SSH 终端长连接会话日志仓储契约 (Driven Port)
    """

    @abstractmethod
    async def create_log(self, log: SSHSessionLog) -> SSHSessionLog:
        """记录终端开启日志"""
        pass

    @abstractmethod
    async def close_log(self, session_id: str, error_msg: str | None = None) -> SSHSessionLog | None:
        """更新终端关闭日志与错误信息"""
        pass

    @abstractmethod
    async def get_by_session_id(self, session_id: str) -> SSHSessionLog | None:
        """查询指定终端会话记录"""
        pass

    @abstractmethod
    async def list_by_connection(self, connection_id: str, limit: int = 50) -> list[SSHSessionLog]:
        """查询指定连接的历史终端会话记录"""
        pass


class IChatSessionRepository(ABC):
    """
    对话会话与消息流仓储契约 (Driven Port)
    """

    @abstractmethod
    async def save(self, session: ChatSession) -> ChatSession:
        """持久化或更新会话基本信息及消息"""
        pass

    @abstractmethod
    async def get_by_id(self, session_id: str) -> ChatSession | None:
        """根据 session_id 获取会话实体及全量历史消息与里程碑"""
        pass

    @abstractmethod
    async def list_by_host(self, host_id: str, limit: int = 50) -> list[ChatSession]:
        """按目标主机(agent_id)获取历史会话列表（按活跃时间倒序）"""
        pass

    @abstractmethod
    async def delete(self, session_id: str) -> bool:
        """删除指定会话及其关联的所有消息与里程碑"""
        pass

    @abstractmethod
    async def append_message(self, message: ChatMessage) -> ChatMessage:
        """追加单条消息并自增会话 message_count"""
        pass

    @abstractmethod
    async def add_milestone(self, milestone: ChatMilestone) -> ChatMilestone:
        """记录对话关键里程碑"""
        pass


class IMemoryRepository(ABC):
    """
    核心记忆与长期记忆仓储契约 (Driven Port)
    """

    @abstractmethod
    async def save_core_memory(self, memory: CoreMemory) -> CoreMemory:
        """保存或更新核心记忆"""
        pass

    @abstractmethod
    async def list_core_memories(
        self,
        user_id: str = "default",
        category: str | None = None,
        limit: int = 20,
    ) -> list[CoreMemory]:
        """查询用户的核心记忆（按优先级倒序）"""
        pass

    @abstractmethod
    async def find_relevant_core_memories(
        self,
        user_id: str,
        query_text: str,
        limit: int = 5,
    ) -> list[CoreMemory]:
        """根据用户指令或运维场景匹配最相关的核心记忆并更新使用计数"""
        pass

    @abstractmethod
    async def save_long_term_memory(self, memory: LongTermMemory) -> LongTermMemory:
        """按 (user_id, memory_type, memory_key) 幂等保存或更新长期记忆"""
        pass

    @abstractmethod
    async def get_long_term_memories(
        self,
        user_id: str,
        memory_type: str | None = None,
        limit: int = 50,
    ) -> list[LongTermMemory]:
        """获取长期记忆列表"""
        pass
