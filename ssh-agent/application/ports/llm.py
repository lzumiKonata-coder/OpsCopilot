"""应用层端口 - 大模型客户端与扩展 Hook 契约"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from domain.entities import AuditDecision, Command, SSHConnection


@dataclass
class ToolCall:
    """标准大模型工具调用描述"""
    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


class LLMResponse(str):
    """
    大模型响应对象，同时继承 str 保证现有字符串消费逻辑完全平滑兼容。
    提供结构化的 content 与 tool_calls。
    """
    content: Optional[str]
    tool_calls: List[ToolCall]
    reasoning_content: Optional[str]

    def __new__(
        cls,
        content: Optional[str] = None,
        tool_calls: Optional[List[ToolCall]] = None,
        reasoning_content: Optional[str] = None,
    ):
        text = content if content is not None else ""
        instance = super().__new__(cls, text)
        instance.content = content
        instance.tool_calls = list(tool_calls or [])
        instance.reasoning_content = reasoning_content
        return instance


class ILLMClient(ABC):
    """大模型客户端基础设施端口契约 (Driven Port)"""

    @abstractmethod
    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.2,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncGenerator[str, None]:
        """流式生成大模型回答分块 (Chunks)"""
        pass

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.2,
        tools: Optional[List[Dict[str, Any]]] = None,
        on_token: Optional[Callable[[str], Any]] = None,
    ) -> LLMResponse:
        """非流式获取大模型完整回答文本及结构化工具调用，支持 on_token 回调监听增量 Token"""
        chunks = []
        async for chunk in self.stream_chat(messages, temperature=temperature, tools=tools):
            chunks.append(chunk)
            if on_token:
                res = on_token(chunk)
                if asyncio.iscoroutine(res):
                    await res
        return LLMResponse(content="".join(chunks))


class IAgentBypassHook(ABC):
    """
    LLM / AI Agent 旁路介入开放契约
    """

    @abstractmethod
    async def on_pre_audit(self, host: SSHConnection, command: Command) -> Optional[Command]:
        pass

    @abstractmethod
    async def on_security_alert(self, host: SSHConnection, command: Command, decision: AuditDecision) -> Optional[AuditDecision]:
        pass
