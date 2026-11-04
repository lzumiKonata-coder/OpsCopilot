"""适配器层 - 工具实现（内置工具与 MCP 工具统一注册到应用层 ToolRegistry）"""

from adapter.tools.builtin import BUILTIN_TOOLS, build_default_registry
from adapter.tools.mcp import McpManager, McpTool, load_mcp_server_configs

__all__ = [
    "BUILTIN_TOOLS",
    "build_default_registry",
    "McpManager",
    "McpTool",
    "load_mcp_server_configs",
]
