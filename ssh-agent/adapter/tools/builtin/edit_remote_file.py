"""内置工具：通过安全静态通道局部修改远端文件"""

from typing import Any, Dict

from application.ports import ToolInvocation, ToolOperation, ToolSpec


class EditRemoteFileTool:
    spec = ToolSpec(
        name="edit_remote_file",
        description="通过安全静态通道对远端文本文件（如配置文件）进行局部单处精确查找与替换。比整文件覆写更安全且节省 tokens，受并发写排他锁保护。",
        operation=ToolOperation.EDIT_FILE,
        requires_approval=True,
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "远端文本文件的绝对路径（例如：/etc/nginx/nginx.conf）。",
                },
                "target": {
                    "type": "string",
                    "description": "待替换的目标原始文本块（必须在文件中精确存在且保持上下文唯一）。",
                },
                "replacement": {
                    "type": "string",
                    "description": "替换后的新文本内容。",
                },
                "step_title": {
                    "type": "string",
                    "description": "当前配置修改步骤标题（例如：将 Nginx 监听端口修改为 8080）。",
                },
                "thought": {
                    "type": "string",
                    "description": "本次局部修改的具体原因与意图说明。",
                },
            },
            "required": ["path", "target", "replacement", "step_title"],
        },
    )

    def normalize(
        self,
        args: Dict[str, Any],
        step_idx: int,
        fallback_thought: str = "",
    ) -> ToolInvocation:
        path = args.get("path")
        return ToolInvocation(
            thought=args.get("thought") or f"局部修改远端文件 {path}",
            command=f"edit {path}",
            step_title=args.get("step_title") or f"修改文件 {path}",
            args=args,
        )
