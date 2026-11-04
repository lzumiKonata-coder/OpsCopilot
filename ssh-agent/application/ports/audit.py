"""应用层端口 - 命令安全审查契约"""

from abc import ABC, abstractmethod

from domain.entities import AuditDecision, Command


class ICommandAuditor(ABC):
    """
    Shell 命令安全审查端口契约 (Driven Port)
    负责对即将执行的命令进行语法树分析与安全策略匹配。
    """

    @abstractmethod
    async def audit(self, command: Command) -> AuditDecision:
        """
        对待执行命令进行 AST 语法级安全审查，产出判定决策。
        """
        pass
