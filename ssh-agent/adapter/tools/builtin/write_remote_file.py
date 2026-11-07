"""内置工具：通过安全文件通道原子写入远端文件"""

from typing import Any

from application.ports import ToolInvocation, ToolOperation, ToolSpec


class WriteRemoteFileTool:
    spec = ToolSpec(
        name="write_remote_file",
        description="通过安全文件通道向远端主机原子写入或修改文件内容。受远端文件写排队锁保护，防止并发写竞争与配置损坏。",
        operation=ToolOperation.WRITE_FILE,
        requires_approval=True,
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "远端文件的绝对路径（例如：/etc/nginx/conf.d/test.conf）。",
                },
                "content": {
                    "type": "string",
                    "description": "要写入的纯文本完整内容。",
                },
                "step_title": {
                    "type": "string",
                    "description": "当前步骤标题（例如：写入 Nginx 虚拟主机配置）。",
                },
                "thought": {
                    "type": "string",
                    "description": "写入或修改该配置的原因与内容说明。",
                },
            },
            "required": ["path", "content", "step_title"],
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
            thought=args.get("thought") or f"写入远端文件 {path}",
            command=f"write {path}",
            step_title=args.get("step_title") or f"写入文件 {path}",
            args=args,
        )
