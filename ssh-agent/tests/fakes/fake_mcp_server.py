"""测试用最小 MCP stdio Server：实现 initialize / tools/list / tools/call"""

import json
import sys
import time

TOOLS = [
    {
        "name": "echo_text",
        "description": "回显传入的文本",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "fail_tool",
        "description": "固定失败的演示工具",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "slow_tool",
        "description": "用于超时测试的慢工具",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = message.get("method")
        request_id = message.get("id")
        if request_id is None:
            # 客户端通知（notifications/initialized）无需响应
            continue

        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-mcp", "version": "1.0.0"},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name == "echo_text":
                result = {
                    "content": [{"type": "text", "text": f"echo: {arguments.get('text', '')}"}],
                    "isError": False,
                }
            elif name == "fail_tool":
                result = {
                    "content": [{"type": "text", "text": "intentional failure"}],
                    "isError": True,
                }
            elif name == "slow_tool":
                time.sleep(5)
                result = {"content": [{"type": "text", "text": "slow done"}], "isError": False}
            else:
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": f"unknown tool: {name}"},
                    }
                )
                continue
        else:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"unknown method: {method}"},
                }
            )
            continue

        _send({"jsonrpc": "2.0", "id": request_id, "result": result})


if __name__ == "__main__":
    main()
