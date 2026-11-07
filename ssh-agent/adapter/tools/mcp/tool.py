"""MCP 工具：把外部 MCP Server 暴露的工具适配为统一 ITool"""

import json
import logging
from typing import Any

from application.ports import (
    ToolContext,
    ToolInvocation,
    ToolOperation,
    ToolResult,
    ToolSpec,
)

from .client import McpError, McpStdioClient

logger = logging.getLogger("smartops.mcp")


def _extract_text(result: dict[str, Any]) -> str:
    """把 MCP tools/call 的 content 数组拼接为纯文本 observation"""
    parts: list[str] = []
    for block in result.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
        else:
            parts.append(json.dumps(block, ensure_ascii=False))
    return "\n".join(part for part in parts if part) or "(工具未返回文本内容)"


class McpTool:
    """单个 MCP 工具适配器"""

    def __init__(
        self,
        client: McpStdioClient,
        server_name: str,
        tool_name: str,
        description: str,
        input_schema: dict[str, Any] | None = None,
    ):
        self.client = client
        self.server_name = server_name
        self.remote_tool_name = tool_name
        self.spec = ToolSpec(
            name=f"mcp__{server_name}__{tool_name}",
            description=f"{description or tool_name}（外部 MCP 工具，来源：{server_name}）",
            parameters=input_schema or {"type": "object", "properties": {}},
            operation=ToolOperation.MCP,
            requires_approval=True,
            source=f"mcp:{server_name}",
        )

    def normalize(
        self,
        args: dict[str, Any],
        step_idx: int,
        fallback_thought: str = "",
    ) -> ToolInvocation:
        return ToolInvocation(
            thought=args.get("thought") or f"调用外部工具 {self.server_name}/{self.remote_tool_name}",
            command=f"mcp:{self.server_name}.{self.remote_tool_name}",
            step_title=args.get("step_title") or f"调用外部工具 {self.remote_tool_name}",
            args=args,
        )

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if ctx.cancel_check is not None:
            try:
                if await ctx.cancel_check():
                    return ToolResult(observation="[外部工具调用已取消]", is_error=True)
            except Exception:
                pass

        # thought / step_title 属于推演元数据，不传给外部工具
        call_args = {
            key: value
            for key, value in args.items()
            if key not in {"thought", "step_title"}
        }

        try:
            result = await self.client.call_tool(self.remote_tool_name, call_args)
        except McpError as exc:
            logger.warning("MCP tool '%s' failed: %s", self.spec.name, exc)
            return ToolResult(observation=f"[外部工具调用失败] {exc}", is_error=True)
        except Exception as exc:
            logger.exception("MCP tool '%s' raised unexpected error", self.spec.name)
            return ToolResult(observation=f"[外部工具调用失败] {exc}", is_error=True)

        output = _extract_text(result)
        if result.get("isError"):
            return ToolResult(observation=f"[外部工具调用失败] {output}", is_error=True)
        return ToolResult(observation=output, meta={"mcp_server": self.server_name})
