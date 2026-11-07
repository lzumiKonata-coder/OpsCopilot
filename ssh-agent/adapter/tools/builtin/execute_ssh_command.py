"""内置工具：在远端主机执行单行 Shell 命令"""

from typing import Any

from application.ports import ToolInvocation, ToolOperation, ToolSpec


class ExecuteSshCommandTool:
    spec = ToolSpec(
        name="execute_ssh_command",
        description="在目标 Linux 远程主机上执行一条 Shell 运维命令。用于服务启停、交互式操作、复杂管道命令及无法通过直接文件读写完成的操作。",
        operation=ToolOperation.COMMAND,
        requires_approval=True,
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要下发的标准 Shell 单行命令（如 uptime, df -h, systemctl status nginx 等），禁止包含 markdown 代码块反引号。",
                },
                "step_title": {
                    "type": "string",
                    "description": "当前排障步骤的操作简短标题（例如：检查根分区磁盘使用率、查看 Nginx 进程状态）。",
                },
                "thought": {
                    "type": "string",
                    "description": "下发该命令的推演逻辑与预期意图。",
                },
            },
            "required": ["command", "step_title"],
        },
    )

    def normalize(
        self,
        args: dict[str, Any],
        step_idx: int,
        fallback_thought: str = "",
    ) -> ToolInvocation:
        thought = args.get("thought") or (
            fallback_thought.strip() if fallback_thought.strip() else f"推进排查第 {step_idx} 步"
        )
        return ToolInvocation(
            thought=thought,
            command=args.get("command"),
            step_title=args.get("step_title") or f"步骤 {step_idx}",
            args=args,
        )
