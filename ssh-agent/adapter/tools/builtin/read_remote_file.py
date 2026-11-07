"""内置工具：通过只读静态通道读取远端文件"""

from typing import Any

from application.ports import ToolInvocation, ToolOperation, ToolSpec


class ReadRemoteFileTool:
    spec = ToolSpec(
        name="read_remote_file",
        description="通过只读静态通道直接读取远端主机的配置文件或日志。获取纯净文本内容，不经过 PTY 终端，避免屏幕混淆和 ANSI 控制符干扰。",
        operation=ToolOperation.READ_FILE,
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "远端文件的绝对路径（例如：/etc/nginx/nginx.conf 或 /var/log/messages）。",
                },
                "offset": {
                    "type": "integer",
                    "description": "读取起始行号（从 1 开始，可选，默认 1）。",
                },
                "limit": {
                    "type": "integer",
                    "description": "读取最大行数（默认 200 行，可选）。",
                },
                "step_title": {
                    "type": "string",
                    "description": "当前步骤标题（例如：读取 Nginx 配置文件）。",
                },
                "thought": {
                    "type": "string",
                    "description": "读取该文件的排查意图。",
                },
            },
            "required": ["path", "step_title"],
        },
    )

    def normalize(
        self,
        args: dict[str, Any],
        step_idx: int,
        fallback_thought: str = "",
    ) -> ToolInvocation:
        path = args.get("path")
        return ToolInvocation(
            thought=args.get("thought") or f"读取远端文件 {path}",
            command=f"cat {path}",
            step_title=args.get("step_title") or f"读取文件 {path}",
            args=args,
        )
