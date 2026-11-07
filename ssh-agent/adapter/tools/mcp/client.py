"""MCP stdio 传输客户端（JSON-RPC 2.0，逐行分隔）

仅依赖标准库，实现 MCP 最小必要能力：initialize / tools/list / tools/call。
"""

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger("smartops.mcp")

MCP_PROTOCOL_VERSION = "2024-11-05"


class McpError(Exception):
    """MCP 协议或传输层异常"""


class McpStdioClient:
    """通过 stdio 子进程与 MCP Server 通信"""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 30.0,
    ):
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.timeout = timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._request_id = 0

    @property
    def started(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        if self.started:
            return
        merged_env = {**os.environ, **self.env}
        self._proc = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=merged_env,
        )
        await self._request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "OpsCopilot", "version": "1.0.0"},
            },
        )
        await self._notify("notifications/initialized", {})
        logger.info("MCP server '%s' initialized (pid=%s)", self.name, self._proc.pid)

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._request("tools/list", {})
        return list(result.get("tools") or [])

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "tools/call",
            {"name": tool_name, "arguments": arguments or {}},
        )

    async def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        """发送 JSON-RPC 通知（无 id，不等待响应）"""
        proc = self._ensure_proc()
        assert proc.stdin is not None
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        proc.stdin.write((json.dumps(payload, ensure_ascii=True) + "\n").encode("utf-8"))
        await proc.stdin.drain()

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        proc = self._ensure_proc()
        assert proc.stdin is not None and proc.stdout is not None

        async with self._lock:
            self._request_id += 1
            request_id = self._request_id
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
            proc.stdin.write((json.dumps(payload, ensure_ascii=True) + "\n").encode("utf-8"))
            await proc.stdin.drain()

            try:
                while True:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=self.timeout)
                    if not line:
                        raise McpError(f"MCP server '{self.name}' closed stdout")
                    text = line.decode("utf-8", errors="ignore").strip()
                    if not text:
                        continue
                    try:
                        message = json.loads(text)
                    except json.JSONDecodeError:
                        logger.warning("MCP server '%s' emitted non-JSON line: %s", self.name, text[:200])
                        continue
                    if message.get("id") != request_id:
                        # 忽略服务端主动通知或其它响应
                        continue
                    if "error" in message:
                        error = message["error"]
                        raise McpError(
                            f"MCP {method} failed: {error.get('message') if isinstance(error, dict) else error}"
                        )
                    return message.get("result") or {}
            except asyncio.TimeoutError as exc:
                raise McpError(f"MCP {method} timed out after {self.timeout}s") from exc

    def _ensure_proc(self) -> asyncio.subprocess.Process:
        if not self.started or self._proc is None:
            raise McpError(f"MCP server '{self.name}' is not running")
        return self._proc
