"""
应用层 - 抽象接口契约定义 (Ports / Interfaces)
遵循依赖倒置原则 (DIP)，应用层仅定义所需能力的抽象契约，由外层适配层 (Adapter) 予以实现。
按能力拆分于本包内各模块，本文件统一聚合导出，保持 `from application.ports import X` 的既有用法。
"""

from application.ports.audit import ICommandAuditor
from application.ports.llm import IAgentBypassHook, ILLMClient, LLMResponse, ToolCall
from application.ports.react_engine import IReActEngine
from application.ports.repository import (
    IChatSessionRepository,
    IMemoryRepository,
    ISSHConnectionRepository,
    ISSHSessionLogRepository,
)
from application.ports.ssh import (
    ISSHGateway,
    ISSHTerminalGateway,
    ISSHTerminalSessionManager,
)
from application.ports.tools import (
    IExecutableTool,
    ITool,
    ToolContext,
    ToolInvocation,
    ToolOperation,
    ToolResult,
    ToolSpec,
)

__all__ = [
    "ICommandAuditor",
    "IAgentBypassHook",
    "ILLMClient",
    "LLMResponse",
    "ToolCall",
    "IReActEngine",
    "IChatSessionRepository",
    "IMemoryRepository",
    "ISSHConnectionRepository",
    "ISSHSessionLogRepository",
    "ISSHGateway",
    "ISSHTerminalGateway",
    "ISSHTerminalSessionManager",
    "IExecutableTool",
    "ITool",
    "ToolContext",
    "ToolInvocation",
    "ToolOperation",
    "ToolResult",
    "ToolSpec",
]
