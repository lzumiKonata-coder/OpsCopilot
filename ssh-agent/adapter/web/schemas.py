"""
Web 适配层 - 外部交互 DTO (Data Transfer Objects)
隔离领域实体与 HTTP 接口协议，集中维护所有请求/响应模型。
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from domain.rules import AuthType

# 单张图片 data URL 长度上限（约 2MB 原始图片），防止超大 base64 常驻内存与 Token 预算暴涨
MAX_IMAGE_DATA_URL_LENGTH = 2_800_000


class RegisterHostRequest(BaseModel):
    """注册主机/SSH连接请求 DTO"""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "prod-ubuntu-01",
                "ip": "192.168.132.100",
                "port": 22,
                "username": "root",
                "auth_type": 1,
                "password": "YourPassword123!",
                "connection_timeout": 10
            }
        }
    )
    name: Optional[str] = Field(default=None, description="主机显示名称")
    connection_name: Optional[str] = Field(default=None, description="连接名称")
    ip: Optional[str] = Field(default=None, description="主机 IPv4/IPv6 或域名")
    host: Optional[str] = Field(default=None, description="主机地址")
    port: int = Field(default=22, ge=1, le=65535, description="SSH 端口")
    username: str = Field(..., description="登录用户名")
    auth_type: AuthType = Field(default=AuthType.PASSWORD, description="认证类型: 1/PASSWORD 或 2/PRIVATE_KEY")
    password: Optional[str] = Field(default=None, description="登录密码（PASSWORD 认证必填）")
    private_key: Optional[str] = Field(default=None, description="OpenSSH 格式私钥内容（PRIVATE_KEY 认证选填）")
    passphrase: Optional[str] = Field(default=None, description="私钥解锁口令（可选）")
    connection_timeout: int = Field(default=10, ge=1, le=60, description="连接超时时间（秒）")
    keepalive_interval: int = Field(default=60, ge=0, le=3600, description="保活间隔时间（秒）")
    strict_host_key_check: int = Field(default=0, description="是否严格校验主机密钥: 0-否, 1-是")
    known_hosts: Optional[str] = Field(default=None, description="已知主机密钥文件路径或公钥")
    user_id: str = Field(default="default", description="所属用户ID")


class HostResponse(BaseModel):
    """主机展示响应 DTO（绝密凭据脱敏）"""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "host_id": "4b129ecf-061c-425e-897f-0f1fb2e449b3",
                "connection_id": "4b129ecf-061c-425e-897f-0f1fb2e449b3",
                "name": "prod-ubuntu-01",
                "ip": "192.168.132.100",
                "port": 22,
                "username": "root",
                "auth_type": 1,
                "connection_timeout": 10,
                "status": 0,
                "user_id": "default"
            }
        }
    )
    host_id: str
    connection_id: str
    name: str
    connection_name: str
    ip: str
    host: str
    port: int
    username: str
    auth_type: Any
    connection_timeout: int
    strict_host_key_check: Optional[int] = 0
    known_hosts: Optional[str] = None
    status: int = 0
    user_id: str = "default"


class AgentChatRequest(BaseModel):
    """OpsAgent 对话请求 DTO"""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "prompt": "磁盘空间不足，该如何安全排查？",
                "terminal_buffer": "Filesystem Size Used Avail Use% Mounted on\n/dev/sda1 50G 48G 2G 96% /",
                "task_id": "9a38f3cc-5e58-45b0-8198-d1cb05f6c8d1",
                "session_id": "b7d7207a-4ff4-404a-963b-e67e72cc2d13",
                "permission_mode": "request_approval"
            }
        }
    )
    prompt: str = Field(..., min_length=1, description="用户运维需求或疑问")
    terminal_buffer: Optional[str] = Field(default=None, description="终端屏幕缓冲区快照")
    task_id: Optional[str] = Field(default=None, description="可选的任务ID，不传则自动生成")
    session_id: Optional[str] = Field(default=None, description="会话全局唯一 ID，传空则自动关联或创建")
    permission_mode: Optional[Literal["request_approval", "full_access"]] = Field(
        default="request_approval",
        description="工具权限模式：request_approval (只读放行，写入和执行需确认) 或 full_access (跳过内置风险分级，仅平台硬性规则拦截)"
    )


class ReActRequest(BaseModel):
    """自治 ReAct 闭环排障请求 DTO"""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "prompt": "排查磁盘使用情况",
                "terminal_buffer": "Filesystem Size Used Avail Use% Mounted on\n/dev/sda1 50G 48G 2G 96% /",
                "session_id": "b7d7207a-4ff4-404a-963b-e67e72cc2d13",
                "permission_mode": "request_approval"
            }
        }
    )
    prompt: str = Field(..., min_length=1, description="运维排障目标或需求")
    images: Optional[List[str]] = Field(
        default=None,
        max_length=4,
        description="用户随消息上传的图片（data URL / base64，最多 4 张，单张 data URL 不超过 2MB），将以多模态消息传给大模型",
    )
    terminal_buffer: Optional[str] = Field(default=None, description="终端屏幕缓冲区快照")
    session_id: Optional[str] = Field(default=None, description="会话唯一 ID")
    max_steps: Optional[int] = Field(default=None, ge=1, description="最大推演步数 (可选，默认不限制步数，由任务自然收敛与安全熔断驱动)")
    max_run_seconds: Optional[float] = Field(default=None, ge=10.0, le=3600.0, description="最大运行时间软预算（秒，默认 600s，上限 3600s）")
    max_llm_calls: Optional[int] = Field(default=None, ge=1, le=200, description="最大 LLM 调用次数软预算（默认 30 次，上限 200 次）")
    max_tokens: Optional[int] = Field(default=None, ge=1000, le=500_000, description="最大 Token 消耗预算（默认 100,000，上限 500,000）")
    pre_planned_steps: Optional[List[Dict[str, str]]] = Field(default=None, description="预规划排查步骤列表")
    permission_mode: Optional[Literal["request_approval", "full_access"]] = Field(
        default="request_approval",
        description="工具权限模式：request_approval (高危命令需人机协同审批) 或 full_access (放行高危，平台硬性禁止拦截)"
    )
    approved_commands: Optional[List[str]] = Field(default=None, description="预先白名单授权的命令行列表")
    branch_from_session_id: Optional[str] = Field(default=None, description="从指定父会话 ID 切出新排障分支")
    branch_step_idx: Optional[int] = Field(default=None, description="分叉切入的父检查点步骤序号")
    branch_title: Optional[str] = Field(default=None, description="分支标题名称")
    cancel_on_disconnect: bool = Field(
        default=False,
        description="客户端网络连接断开时是否自动取消任务（默认 False：网络波动或刷新不终止服务端后台任务，需调用 cancel 端点显式取消）",
    )

    @field_validator("images")
    @classmethod
    def _validate_images(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        """限制单张图片的 data URL 体积，避免超大 base64 撑爆内存与 Token 预算"""
        if not value:
            return value
        for idx, item in enumerate(value):
            if not isinstance(item, str) or not item.startswith("data:image/"):
                raise ValueError(f"第 {idx + 1} 张图片必须是 data URL 格式（data:image/...;base64,...）")
            if len(item) > MAX_IMAGE_DATA_URL_LENGTH:
                raise ValueError(
                    f"第 {idx + 1} 张图片体积过大（单张 data URL 上限 {MAX_IMAGE_DATA_URL_LENGTH} 字符，约 2MB），请压缩后重试"
                )
        return value


class ReActCancelRequest(BaseModel):
    """ReAct 任务主动取消请求 DTO"""
    session_id: Optional[str] = Field(default=None, description="待取消的会话/任务 ID")


class ReActSteerRequest(BaseModel):
    """ReAct 即时插话/方向纠偏请求 DTO"""
    session_id: str = Field(..., min_length=1, description="待插话排障会话 ID")
    content: str = Field(..., min_length=1, description="用户即时插话/纠偏要求（例如：方向偏了，先检查端口占用）")


class ReActApprovalRequest(BaseModel):
    """ReAct 人机协同审批决断 DTO"""
    approval_id: str = Field(..., description="待审批任务全局唯一 ID")
    approved: bool = Field(..., description="是否批准执行该高危命令")
    scope: Optional[Literal["once", "session"]] = Field(default="once", description="授权范围：once 仅本次, session 当前会话同类放行")


class ChatMessageDTO(BaseModel):
    id: Optional[int] = None
    role: str
    content: str
    tool_name: Optional[str] = None
    priority: Optional[str] = None
    token_count: Optional[int] = 0
    created_at: datetime


class ChatMilestoneDTO(BaseModel):
    id: Optional[int] = None
    session_id: str
    type: str
    content: str
    created_at: datetime


class AgentSessionSummaryResponse(BaseModel):
    session_id: str
    id: Optional[str] = None
    host_id: str
    agent_id: Optional[str] = None
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime


class AgentSessionDetailResponse(BaseModel):
    session_id: str
    id: Optional[str] = None
    host_id: str
    agent_id: Optional[str] = None
    title: str
    message_count: int
    messages: List[ChatMessageDTO]
    milestones: List[ChatMilestoneDTO] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CreateAgentSessionRequest(BaseModel):
    title: Optional[str] = Field(default="新会话", description="会话标题")
    user_id: Optional[str] = Field(default="default", description="用户 ID")


class StepAnalyzeRequest(BaseModel):
    """单步执行终端回显分析请求 DTO"""
    step_number: int = Field(default=1, description="当前步骤序号")
    step_title: Optional[str] = Field(default="执行指令", description="当前步骤简述")
    command: str = Field(..., description="当前步骤执行的 Shell 命令")
    terminal_snapshot: str = Field(default="", description="命令执行后的终端回显输出快照")


class StepAnalyzeResponse(BaseModel):
    """单步执行终端回显分析响应 DTO"""
    step_number: int
    command: str
    analysis: str
    is_normal: bool


class CoreMemoryResponse(BaseModel):
    id: Optional[int]
    user_id: str
    scope: str
    category: str
    title: str
    keywords: Optional[str]
    content: str
    priority: int
    use_count: int
    created_at: datetime
    last_used_at: datetime


class LongTermMemoryResponse(BaseModel):
    id: Optional[int] = None
    user_id: str
    session_id: Optional[str] = None
    memory_type: str
    memory_key: str
    content: str
    keywords: Optional[str] = None
    confidence: float
    hit_count: int
    created_at: datetime
    updated_at: datetime


class CreateCoreMemoryRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="记忆标题")
    content: str = Field(..., min_length=1, description="记忆正文")
    category: Optional[str] = Field(default="Rule", description="分类: Rule/Preference/Decision/Correction/Fact")
    scope: Optional[str] = Field(default="user", description="作用域: user/session")
    keywords: Optional[str] = Field(default=None, description="关键词(逗号分隔)")
    priority: Optional[int] = Field(default=3, ge=1, le=5, description="优先级 1-5")
    user_id: Optional[str] = Field(default="default", description="用户 ID")
