"""
领域层 - 实体与值对象定义 (Domain Entities and Value Objects)
严格基于 Python 标准库与 Pydantic v2，保证纯粹的领域逻辑与无外部基础设施依赖。
包含 SSH 连接/配置、交互式终端会话日志、对话会话/消息、关键里程碑及核心/长期记忆。
"""

from datetime import datetime, timezone
from typing import Any, List, Literal, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from domain.rules import (
    AuthType,
    DEFAULT_MAX_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    InvalidHostCredentialError,
    MAX_TIMEOUT_SECONDS,
    RiskLevel,
)


# ====================================================================
# 1. 领域值对象 (Value Objects - 属性不可变，无生命周期与唯一标识)
# ====================================================================

class Command(BaseModel):
    """
    待执行命令值对象 (Value Object)
    封装原始执行指令及单次调用的防过载约束。
    """
    model_config = ConfigDict(frozen=True)

    raw_command: str = Field(..., description="待执行的原始 Shell 命令行文本")
    timeout_seconds: int = Field(
        default=DEFAULT_TIMEOUT_SECONDS,
        description="单次执行最大超时限制（秒）"
    )
    max_bytes: int = Field(
        default=DEFAULT_MAX_BYTES,
        description="最大回显捕获字节数，超出则触发截断保护"
    )

    @field_validator("raw_command")
    @classmethod
    def validate_raw_command(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("Execution command cannot be empty or whitespace only.")
        return clean

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("timeout_seconds must be positive.")
        if v > MAX_TIMEOUT_SECONDS:
            raise ValueError(f"timeout_seconds cannot exceed system hard limit {MAX_TIMEOUT_SECONDS}s.")
        return v

    @field_validator("max_bytes")
    @classmethod
    def validate_max_bytes(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_bytes must be strictly positive.")
        return v


class AuditDecision(BaseModel):
    """
    安全审查决策结论值对象 (Value Object)
    不可变判定结果，由安全审计引擎根据语法树与安全规则判定生成。
    """
    model_config = ConfigDict(frozen=True)

    risk_level: RiskLevel = Field(..., description="安全风险评定等级")
    hit_rules: List[str] = Field(default_factory=list, description="命中的安全规则标识列表")
    reason: str = Field(default="", description="审查裁定详情或拦截原因描述")
    requires_manual_review: bool = Field(
        default=False,
        description="是否必须人工复核（为真时 full_access 与预授权白名单均不得豁免）"
    )

    @property
    def is_blocked(self) -> bool:
        """业务规则：FORBIDDEN 与 HIGH_RISK 默认被核心引擎阻断"""
        return self.risk_level in (RiskLevel.FORBIDDEN, RiskLevel.HIGH_RISK)


class ExecutionResult(BaseModel):
    """
    命令执行结果值对象 (Value Object)
    表达底层网关执行结束后的客观物理状态，包含控制字符清洗后的纯净输出。
    """
    model_config = ConfigDict(frozen=True)

    exit_status: int = Field(..., description="子进程退出状态码 (0 表示成功)")
    stdout: str = Field(default="", description="清洗/截断后的标准输出文本")
    stderr: str = Field(default="", description="清洗/截断后的错误输出文本")
    is_truncated: bool = Field(default=False, description="是否因超过 max_bytes 限制而被截断")
    duration_ms: float = Field(default=0.0, description="端到端网络与执行总耗时（毫秒）")

    @property
    def is_success(self) -> bool:
        return self.exit_status == 0


class SSHConnectionConfig(BaseModel):
    """
    SSH 连接高级配置值对象 (ssh_connection_config)
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    connect_timeout: int = Field(default=10, ge=1, le=60, description="连接超时时间(秒)")
    keepalive_interval: int = Field(default=60, ge=0, le=3600, description="保活间隔(秒)")
    startup_command: Optional[str] = Field(default=None, description="连接后执行的启动命令")
    compression: int = Field(default=0, description="是否压缩: 0-否, 1-是")
    strict_host_key_check: int = Field(default=0, description="严格主机密钥检查: 0-否, 1-是")
    known_hosts: Optional[str] = Field(default=None, description="已知主机密钥列表")


# ====================================================================
# 2. 领域实体 (Entities - 具有全局唯一标识、状态变更与完整生命周期)
# ====================================================================

class SSHConnection(BaseModel):
    """
    目标主机 SSH 连接实体 (ssh_connection)
    代表受管的远程目标机器连接凭据、网络拓扑与生命周期
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    connection_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="连接唯一标识(UUID)")
    connection_name: str = Field(..., min_length=1, max_length=128, description="连接名称")
    host: str = Field(..., min_length=1, max_length=255, description="主机地址 (IPv4/IPv6/域名)")
    port: int = Field(default=22, ge=1, le=65535, description="端口号")
    username: str = Field(..., min_length=1, max_length=128, description="用户名")
    auth_type: AuthType = Field(default=AuthType.PASSWORD, description="认证类型: 1-密码, 2-私钥")
    password: Optional[str] = Field(default=None, description="密码(加密存储)")
    private_key: Optional[str] = Field(default=None, description="私钥内容(加密存储)")
    passphrase: Optional[str] = Field(default=None, description="私钥口令(可选)")
    encrypted: int = Field(default=1, description="是否加密: 0-否, 1-是")
    status: int = Field(default=0, description="连接状态: 0-未连接, 1-已连接, 2-连接中, 3-连接失败")
    user_id: str = Field(default="default", description="所属用户ID")
    deleted: int = Field(default=0, description="逻辑删除: 0-未删除, 1-已删除")
    config: SSHConnectionConfig = Field(default_factory=SSHConnectionConfig, description="高级连接配置")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="创建时间"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="更新时间"
    )

    @model_validator(mode="before")
    @classmethod
    def pre_process_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # 兼容 legacy 字段名 host_id -> connection_id
            if "host_id" in data and "connection_id" not in data:
                data["connection_id"] = data["host_id"]
            # 兼容 legacy 字段名 name -> connection_name
            if "name" in data and "connection_name" not in data:
                data["connection_name"] = data["name"]
            # 兼容 legacy 字段名 ip -> host
            if "ip" in data and "host" not in data:
                data["host"] = data["ip"]
            # 兼容 legacy 字段 connection_timeout -> config.connect_timeout
            if "connection_timeout" in data:
                timeout_val = data.pop("connection_timeout")
                cfg = data.get("config") or {}
                if isinstance(cfg, dict) and "connect_timeout" not in cfg:
                    cfg["connect_timeout"] = timeout_val
                    data["config"] = cfg
        return data

    def validate_credentials(self) -> None:
        """领域行为：验证主机的凭据合法性"""
        if self.auth_type == AuthType.PASSWORD:
            if not self.password:
                raise InvalidHostCredentialError(f"Host '{self.connection_name}' ({self.host}) requires a non-empty password.")
        elif self.auth_type == AuthType.PRIVATE_KEY:
            if not self.private_key or not self.private_key.strip():
                raise InvalidHostCredentialError(f"Host '{self.connection_name}' ({self.host}) requires a non-empty private_key.")


class SSHSessionLog(BaseModel):
    """
    SSH 终端会话记录实体 (ssh_session_log)
    追踪每次 WebSSH WebSocket 伪终端连接的生命周期与客户端来源
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: Optional[int] = Field(default=None, description="自增主键")
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="会话唯一标识(UUID)")
    connection_id: str = Field(..., description="关联的连接 ID")
    user_id: str = Field(default="default", description="用户 ID")
    status: int = Field(default=0, description="会话状态: 0-打开, 1-关闭")
    remote_addr: Optional[str] = Field(default=None, description="远程客户端 IP 地址")
    start_time: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="会话开始时间"
    )
    end_time: Optional[datetime] = Field(default=None, description="会话结束时间")
    error_msg: Optional[str] = Field(default=None, description="异常报错信息")


# ====================================================================
# 3. 对话消息、里程碑与会话实体 (chat_session / chat_message / chat_milestone)
# ====================================================================

class ChatMessage(BaseModel):
    """
    对话消息模型 (chat_message)
    """
    model_config = ConfigDict(frozen=True)

    id: Optional[int] = Field(default=None, description="自增主键")
    session_id: Optional[str] = Field(default=None, description="关联的会话 ID")
    role: Literal["system", "user", "assistant", "tool"] = Field(..., description="角色: user/assistant/tool/system")
    content: str = Field(..., description="消息内容")
    tool_name: Optional[str] = Field(default=None, description="工具名称")
    tool_call_id: Optional[str] = Field(default=None, description="工具调用 ID")
    priority: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] = Field(default="MEDIUM", description="优先级")
    token_count: int = Field(default=0, description="预估 token 数")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="消息生成时间戳"
    )


class ChatMilestone(BaseModel):
    """
    对话关键里程碑实体 (chat_milestone)
    记录排障/对话过程中的任务变更、重大决策或错误定位摘要
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: Optional[int] = Field(default=None, description="自增主键")
    session_id: str = Field(..., description="关联会话 ID")
    type: str = Field(..., description="里程碑类型: TASK_CHANGE/ERROR/DECISION/ROOT_CAUSE")
    content: str = Field(..., description="内容摘要")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="记录时间"
    )


class ChatSession(BaseModel):
    """
    对话会话实体 (chat_session)
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="会话全局唯一 ID")
    agent_id: str = Field(..., description="智能体标识 (主机 ID 或专用 Agent ID)")
    user_id: str = Field(default="default", description="用户 ID")
    title: str = Field(default="新会话", description="会话标题")
    message_count: int = Field(default=0, description="消息数量")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="会话创建时间"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="会话最后活跃时间"
    )
    messages: List[ChatMessage] = Field(default_factory=list, description="对话历史消息序列")
    milestones: List[ChatMilestone] = Field(default_factory=list, description="关键里程碑序列")

    @model_validator(mode="before")
    @classmethod
    def pre_process_session_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "session_id" in data and "id" not in data:
                data["id"] = data["session_id"]
            if "host_id" in data and "agent_id" not in data:
                data["agent_id"] = data["host_id"]
        return data

    def append_message(
        self,
        role: Literal["system", "user", "assistant", "tool"],
        content: str,
        tool_name: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        priority: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] = "MEDIUM",
        token_count: int = 0,
    ) -> ChatMessage:
        msg = ChatMessage(
            session_id=self.id,
            role=role,
            content=content,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            priority=priority,
            token_count=token_count or max(1, len(content) // 4),
        )
        self.messages.append(msg)
        self.message_count = len(self.messages)
        self.updated_at = datetime.now(timezone.utc)
        if self.title == "新会话" and role == "user":
            first_line = content.strip().split("\n")[0].strip()
            self.title = first_line[:24] if len(first_line) > 24 else (first_line or "新会话")
        return msg

    def add_milestone(self, type_name: str, content: str) -> ChatMilestone:
        ms = ChatMilestone(session_id=self.id, type=type_name, content=content)
        self.milestones.append(ms)
        return ms

    def get_recent_history(self, limit: int = 10) -> List[ChatMessage]:
        return self.messages[-limit:]


# ====================================================================
# 4. 记忆增强领域实体 (core_memory / long_term_memory)
# ====================================================================

class CoreMemory(BaseModel):
    """
    核心记忆实体 (core_memory)
    负责存储对话内学习沉淀与跨会话规则、用户偏好、重要事实和纠错记录
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: Optional[int] = Field(default=None, description="自增主键")
    user_id: str = Field(default="default", description="用户 ID")
    scope: Literal["user", "session"] = Field(default="user", description="作用域: user/session")
    category: Literal["Rule", "Preference", "Decision", "Correction", "Fact"] = Field(
        default="Rule",
        description="分类: Rule/Preference/Decision/Correction/Fact"
    )
    title: str = Field(..., min_length=1, max_length=200, description="记忆标题")
    keywords: Optional[str] = Field(default=None, max_length=500, description="关键词(逗号分隔)")
    content: str = Field(..., description="记忆正文")
    priority: int = Field(default=3, ge=1, le=5, description="优先级 1-5 (越高越重要)")
    source_session_id: Optional[str] = Field(default=None, description="来源会话 ID")
    use_count: int = Field(default=1, description="使用/引用次数")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="创建时间"
    )
    last_used_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="最后使用时间"
    )


class LongTermMemory(BaseModel):
    """
    长期记忆实体 (long_term_memory)
    具备按 (user_id, memory_type, memory_key) 去重更新与置信度打分的知识沉淀
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: Optional[int] = Field(default=None, description="自增主键")
    user_id: str = Field(..., description="用户 ID")
    session_id: Optional[str] = Field(default=None, description="来源会话 ID")
    memory_type: str = Field(..., max_length=50, description="记忆类型 (如 ServerEnv, NetworkTopo, Habit)")
    memory_key: str = Field(..., max_length=128, description="去重键")
    content: str = Field(..., description="记忆内容")
    keywords: Optional[str] = Field(default=None, max_length=512, description="关键词集合")
    source_role: Optional[str] = Field(default=None, description="来源角色")
    confidence: float = Field(default=0.50, ge=0.0, le=1.0, description="置信度")
    hit_count: int = Field(default=1, description="命中/更新次数")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="创建时间"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="更新时间"
    )
