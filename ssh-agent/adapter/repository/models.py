"""
适配层 - ORM 持久化模型定义 (Persistence Objects / PO)
严格遵循领域驱动设计中“领域实体与持久化模型分离”原则：
包含 8 张核心标准表 (MySQL / SQLite 兼容)：
1. chat_session (对话会话)
2. chat_message (对话消息)
3. chat_milestone (对话里程碑)
4. core_memory (核心记忆表)
5. long_term_memory (长期记忆表)
6. ssh_connection (SSH 连接配置表)
7. ssh_connection_config (SSH 连接高级配置表)
8. ssh_session_log (SSH 会话记录表)
"""

from datetime import datetime
from decimal import Decimal
from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 声明式基类"""
    pass


# 针对 SQLite 与 MySQL 兼容的 BigInteger 自增主键类型定义
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


# ====================================================================
# 1. 对话会话与消息体系 (chat_session, chat_message, chat_milestone)
# ====================================================================

class ChatSessionPO(Base):
    """
    对话会话表 (chat_session)
    """
    __tablename__ = "chat_session"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="会话ID")
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="智能体ID")
    parent_session_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="父会话ID（分叉来源，根会话为空）"
    )
    branch_step: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="分叉切入点步骤（根会话为空）"
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", comment="用户ID")
    title: Mapped[str | None] = mapped_column(String(200), nullable=True, default="新会话", comment="会话标题")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="更新时间"
    )
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="消息数量")

    __table_args__ = (
        Index("idx_user_agent", "user_id", "agent_id"),
    )


class ChatMessagePO(Base):
    """
    对话消息表 (chat_message)
    """
    __tablename__ = "chat_message"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True, comment="自增主键")
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="会话ID")
    role: Mapped[str] = mapped_column(String(20), nullable=False, comment="角色: user/assistant/tool/system")
    content: Mapped[str | None] = mapped_column(Text, nullable=True, comment="消息内容")
    tool_name: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="工具名称")
    tool_call_id: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="工具调用ID")
    priority: Mapped[str] = mapped_column(String(20), default="MEDIUM", nullable=False, comment="优先级: CRITICAL/HIGH/MEDIUM/LOW")
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="预估 token 数")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间"
    )

    __table_args__ = (
        Index("idx_session_time", "session_id", "created_at"),
    )


class ChatMilestonePO(Base):
    """
    对话里程碑表 (chat_milestone)
    """
    __tablename__ = "chat_milestone"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True, comment="自增主键")
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="会话ID")
    type: Mapped[str] = mapped_column(String(30), nullable=False, comment="类型: DIAGNOSIS/ROOT_CAUSE/TASK_CHANGE/ERROR/DECISION")
    content: Mapped[str | None] = mapped_column(Text, nullable=True, comment="内容摘要")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间"
    )

    __table_args__ = (
        Index("idx_milestone_session_time", "session_id", "created_at"),
    )


# ====================================================================
# 2. 记忆增强体系 (core_memory, long_term_memory)
# ====================================================================

class CoreMemoryPO(Base):
    """
    核心记忆表(对话内学习+跨会话记忆) (core_memory)
    """
    __tablename__ = "core_memory"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True, comment="自增主键")
    user_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False, comment="用户ID")
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="user", comment="作用域: user/session")
    category: Mapped[str] = mapped_column(String(30), nullable=False, default="Rule", comment="分类: Rule/Preference/Decision/Correction/Fact")
    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="记忆标题")
    keywords: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="关键词(逗号分隔)")
    content: Mapped[str | None] = mapped_column(Text, nullable=True, comment="记忆正文")
    priority: Mapped[int] = mapped_column(Integer, default=3, nullable=False, comment="优先级1-5(越高越重要)")
    source_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="来源会话ID")
    use_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False, comment="使用次数")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间"
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="最后使用时间"
    )

    __table_args__ = (
        Index("idx_user_priority", "user_id", "priority"),
        Index("idx_user_last_used", "user_id", "last_used_at"),
        Index("idx_keywords", "keywords"),
    )


class LongTermMemoryPO(Base):
    """
    长期记忆表 (long_term_memory)
    """
    __tablename__ = "long_term_memory"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True, comment="自增主键")
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="用户ID")
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="来源会话ID")
    memory_type: Mapped[str] = mapped_column(String(50), nullable=False, comment="记忆类型")
    memory_key: Mapped[str] = mapped_column(String(128), nullable=False, comment="记忆去重键")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="记忆内容")
    keywords: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="关键词集合")
    source_role: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="来源角色")
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0.50"), nullable=False, comment="置信度")
    hit_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False, comment="命中/更新次数")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="更新时间"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "memory_type", "memory_key", name="uk_user_type_key"),
        Index("idx_user_time", "user_id", "updated_at"),
    )


# ====================================================================
# 3. SSH 连接与配置管理 (ssh_connection, ssh_connection_config, ssh_session_log)
# ====================================================================

class SSHConnectionPO(Base):
    """
    SSH 连接配置表 (ssh_connection)
    """
    __tablename__ = "ssh_connection"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True, comment="自增主键")
    connection_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, comment="连接唯一标识(UUID)")
    connection_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="连接名称")
    host: Mapped[str] = mapped_column(String(255), nullable=False, comment="主机地址")
    port: Mapped[int] = mapped_column(Integer, default=22, nullable=False, comment="端口号")
    username: Mapped[str] = mapped_column(String(128), nullable=False, comment="用户名")
    auth_type: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False, comment="认证类型:1-密码,2-私钥")
    password: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="密码(加密存储)")
    private_key: Mapped[str | None] = mapped_column(Text, nullable=True, comment="私钥内容(加密存储)")
    passphrase: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="私钥口令(加密存储)")
    encrypted: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False, comment="是否加密:0-否,1-是")
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False, comment="连接状态:0-未连接,1-已连接,2-连接中,3-连接失败")
    user_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False, comment="用户ID")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="更新时间"
    )
    deleted: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False, comment="逻辑删除:0-未删除,1-已删除")

    __table_args__ = (
        Index("idx_conn_user_id", "user_id"),
        Index("idx_conn_status", "status"),
        Index("idx_conn_created_at", "created_at"),
    )


class SSHConnectionConfigPO(Base):
    """
    SSH 连接高级配置表 (ssh_connection_config)
    """
    __tablename__ = "ssh_connection_config"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True, comment="自增主键")
    connection_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, comment="关联的连接ID")
    connect_timeout: Mapped[int] = mapped_column(Integer, default=10, nullable=False, comment="连接超时时间(秒)")
    keepalive_interval: Mapped[int] = mapped_column(Integer, default=60, nullable=False, comment="保活间隔(秒)")
    startup_command: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="连接后执行的启动命令")
    compression: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False, comment="是否压缩:0-否,1-是")
    strict_host_key_check: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False, comment="严格主机密钥检查:0-否,1-是")
    known_hosts: Mapped[str | None] = mapped_column(Text, nullable=True, comment="已知主机密钥列表")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="更新时间"
    )


class SSHSessionLogPO(Base):
    """
    SSH 会话记录表 (ssh_session_log)
    """
    __tablename__ = "ssh_session_log"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True, comment="自增主键")
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, comment="会话唯一标识(UUID)")
    connection_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="关联的连接ID")
    user_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False, comment="用户ID")
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False, comment="会话状态:0-打开,1-关闭")
    remote_addr: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="远程地址")
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="会话开始时间"
    )
    end_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="会话结束时间"
    )
    error_msg: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="错误信息")

    __table_args__ = (
        Index("idx_sess_connection_id", "connection_id"),
        Index("idx_sess_user_id", "user_id"),
        Index("idx_sess_start_time", "start_time"),
    )

