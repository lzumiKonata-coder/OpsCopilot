"""
应用层 - 工具注册表实现

工具协议与数据结构（ToolSpec / ToolInvocation / ITool）定义在 application.ports，
本模块只负责工具的统一注册、查询与 OpenAI Schema 导出。
"""

from typing import Any, Dict, Iterable, List, Optional

from application.ports import ITool


class ToolRegistry:
    """工具注册表：集中管理工具声明与参数归一化"""

    def __init__(self, tools: Iterable[ITool] = ()):
        self._tools: Dict[str, ITool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: ITool) -> None:
        name = tool.spec.name
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")
        self._tools[name] = tool

    def get(self, name: Optional[str]) -> Optional[ITool]:
        if not name:
            return None
        return self._tools.get(name)

    def specs(self) -> List[Dict[str, Any]]:
        """返回可直接传给大模型 tools 参数的声明列表"""
        return [tool.spec.to_openai_schema() for tool in self._tools.values()]

    def names(self) -> List[str]:
        return list(self._tools.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
