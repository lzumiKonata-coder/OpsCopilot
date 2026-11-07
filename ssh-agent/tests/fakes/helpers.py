"""测试共享辅助：Mock LLM 客户端"""

from typing import AsyncGenerator

from application.ports import ILLMClient


class MockLLMClient(ILLMClient):
    """用于测试的 Mock LLM 驱动"""

    def __init__(self, chunks: list[str] = None):
        self.chunks = chunks or ["### 1. 根因分析\n", "磁盘空间耗尽\n\n", "### 2. 建议指令\n", "```bash\ndf -h\n```"]
        self.received_messages = []

    async def stream_chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
    ) -> AsyncGenerator[str, None]:
        self.received_messages = messages
        for chunk in self.chunks:
            yield chunk
