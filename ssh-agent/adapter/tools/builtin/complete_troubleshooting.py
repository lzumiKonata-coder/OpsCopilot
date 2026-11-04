"""内置工具：收敛任务并输出最终交付报告"""

from typing import Any, Dict

from application.ports import ToolInvocation, ToolOperation, ToolSpec


class CompleteTroubleshootingTool:
    spec = ToolSpec(
        name="complete_troubleshooting",
        description="当任务目标已达成时调用此工具，输出结构化精炼交付报告并收敛结束流程。",
        operation=ToolOperation.COMPLETE,
        parameters={
            "type": "object",
            "properties": {
                "final_answer": {
                    "type": "string",
                    "description": (
                        "最终精炼交付报告，必须严格遵循：\n"
                        "1. 首行一句话结论（如 '已删除 /root/test.txt。'，绝不加 '任务完成:' 前缀）；\n"
                        "2. 空一行后为 '执行过程:' 有序列表（1. 操作前检查属性 2. 执行操作 3. 操作后复查），禁止 Step X 编号与 AI 理论废话；\n"
                        "3. 如有不可逆风险空一行写 '注意:'（如 '注意: 删除操作不可逆，该文件已无法通过终端恢复。'）。"
                    ),
                },
                "thought": {
                    "type": "string",
                    "description": "推演完成的内部思考总结。",
                },
            },
            "required": ["final_answer"],
        },
    )

    def normalize(
        self,
        args: Dict[str, Any],
        step_idx: int,
        fallback_thought: str = "",
    ) -> ToolInvocation:
        return ToolInvocation(
            thought=args.get("thought") or "任务推演完成",
            command=None,
            step_title="任务已完成",
            final_answer=args.get("final_answer"),
            args=args,
        )
