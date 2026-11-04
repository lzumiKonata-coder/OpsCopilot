"""MCP Server 配置加载与工具注册生命周期管理"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from application.tool_registry import ToolRegistry
from config.settings import load_settings

from .client import McpStdioClient
from .tool import McpTool

logger = logging.getLogger("smartops.mcp")


@dataclass
class McpServerConfig:
    name: str
    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0


def load_mcp_server_configs() -> List[McpServerConfig]:
    """从 MCP_SERVERS 环境变量加载配置（JSON 数组）"""
    raw = load_settings().mcp_servers_raw
    if not raw:
        return []
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Invalid MCP_SERVERS JSON: %s", exc)
        return []

    if isinstance(data, dict):
        data = data.get("servers") or []
    if not isinstance(data, list):
        logger.error("MCP_SERVERS must be a JSON array of server configs")
        return []

    configs: List[McpServerConfig] = []
    for item in data:
        if not isinstance(item, dict) or not item.get("name") or not item.get("command"):
            logger.warning("Skip invalid MCP server config: %s", item)
            continue
        configs.append(
            McpServerConfig(
                name=str(item["name"]),
                command=str(item["command"]),
                args=[str(arg) for arg in item.get("args") or []],
                env={str(k): str(v) for k, v in (item.get("env") or {}).items()},
                timeout=float(item.get("timeout") or 30.0),
            )
        )
    return configs


class McpManager:
    """按配置启动 MCP Server、发现工具并注册到 ToolRegistry"""

    def __init__(self, configs: Optional[List[McpServerConfig]] = None):
        self.configs = configs if configs is not None else load_mcp_server_configs()
        self.clients: List[McpStdioClient] = []

    async def connect_all(self, registry: ToolRegistry) -> int:
        """连接全部配置的 MCP Server，返回成功注册的工具数量"""
        registered = 0
        for cfg in self.configs:
            client = McpStdioClient(
                name=cfg.name,
                command=cfg.command,
                args=cfg.args,
                env=cfg.env,
                timeout=cfg.timeout,
            )
            try:
                await client.start()
                tools = await client.list_tools()
                for item in tools:
                    if not isinstance(item, dict) or not item.get("name"):
                        continue
                    registry.register(
                        McpTool(
                            client=client,
                            server_name=cfg.name,
                            tool_name=str(item["name"]),
                            description=str(item.get("description") or ""),
                            input_schema=item.get("inputSchema") or item.get("input_schema"),
                        )
                    )
                    registered += 1
            except Exception as exc:
                logger.warning("MCP server '%s' unavailable, skipped: %s", cfg.name, exc)
                await client.close()
                continue
            self.clients.append(client)
            logger.info("MCP server '%s' registered %d tools", cfg.name, len(tools))
        return registered

    async def close(self) -> None:
        for client in self.clients:
            await client.close()
        self.clients.clear()
