"""内置工具集合与默认注册表构建"""


from application.ports import ITool
from application.tool_registry import ToolRegistry

from .complete_troubleshooting import CompleteTroubleshootingTool
from .edit_remote_file import EditRemoteFileTool
from .execute_ssh_command import ExecuteSshCommandTool
from .read_remote_file import ReadRemoteFileTool
from .write_remote_file import WriteRemoteFileTool

BUILTIN_TOOLS: list[ITool] = [
    ExecuteSshCommandTool(),
    ReadRemoteFileTool(),
    EditRemoteFileTool(),
    WriteRemoteFileTool(),
    CompleteTroubleshootingTool(),
]


def build_default_registry() -> ToolRegistry:
    """构建仅包含内置工具的注册表（后续 MCP/插件工具可注册到同一实例）"""
    return ToolRegistry(BUILTIN_TOOLS)
