"""应用层端口 - 工具层契约（内置 / MCP / 插件工具统一抽象）"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class ToolOperation(str, Enum):
    """工具执行语义，状态机据此选择审计方式与执行通道"""

    COMMAND = "command"
    READ_FILE = "read_file"
    EDIT_FILE = "edit_file"
    WRITE_FILE = "write_file"
    COMPLETE = "complete"
    MCP = "mcp"


@dataclass(frozen=True)
class ToolSpec:
    """工具的声明式描述，可直接映射为 OpenAI / MCP 工具协议"""

    name: str
    description: str
    parameters: dict[str, Any]
    operation: ToolOperation = ToolOperation.COMMAND
    requires_approval: bool = False
    source: str = "builtin"

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolInvocation:
    """工具调用的归一化结果，供 think/audit/act 节点统一消费"""

    thought: str
    command: str | None
    step_title: str
    final_answer: str | None = None
    plan: str | None = None
    args: dict[str, Any] = field(default_factory=dict)


class ITool(ABC):
    """工具实现端口契约"""

    spec: ToolSpec

    @abstractmethod
    def normalize(
        self,
        args: dict[str, Any],
        step_idx: int,
        fallback_thought: str = "",
    ) -> ToolInvocation:
        ...


@dataclass
class ToolContext:
    """工具执行上下文（脱敏后的运行环境，不直接暴露内部状态）"""

    host: Any = None
    session_id: str = ""
    cancel_check: Callable[[], Any] | None = None


@dataclass
class ToolResult:
    """工具执行结果"""

    observation: str
    is_error: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


class IExecutableTool(ITool):
    """可执行外部工具端口契约（如 MCP 工具）"""

    @abstractmethod
    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        ...
