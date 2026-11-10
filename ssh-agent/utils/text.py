"""文本处理与 Token 估算通用工具"""

import json
import re
from typing import Any

EMOJI_PATTERN = re.compile(
    r"[\U0001F600-\U0001F64F]"
    r"|[\U0001F300-\U0001F5FF]"
    r"|[\U0001F680-\U0001F6FF]"
    r"|[\U0001F1E0-\U0001F1FF]"
    r"|[\U0001F900-\U0001F9FF]"
    r"|[\U0001FA70-\U0001FAFF]"
    r"|[\u2600-\u26FF]"
    r"|[\u2700-\u27BF]"
    r"|[\uFE00-\uFE0F]"
)


def remove_emojis(text: str | None) -> str:
    """移除文本中的 Emoji，None 原样归一为空字符串"""
    if not text:
        return "" if text is not None else ""
    return EMOJI_PATTERN.sub("", text)


def smart_truncate(text: str | None, max_chars: int = 2000, head_ratio: float = 0.3) -> str:
    """
    智能首尾截断长文本：
    当物理终端回显或文件内容过长时，简单头部截断会破坏尾部最新的错误日志。
    本函数保留前部 head_ratio 比例的字符（包含命令提示符与环境头信息）
    以及尾部 (1 - head_ratio) 比例字符（包含最新产生的报错与堆栈），
    并在中间插入带有明确省略指标的技术标记。
    """
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 60:
        cut = max(10, max_chars - 16)
        return f"{text[:cut]}... [已自动截断]"

    target_head = int(max_chars * head_ratio)
    head_chars = max(15, min(target_head, max_chars // 2))
    tail_chars = max(15, max_chars - head_chars)

    head = text[:head_chars]
    tail = text[-tail_chars:]

    # 尝试在换行符处优雅切分，避免截断单词或控制字符
    last_head_nl = head.rfind("\n")
    if last_head_nl > int(head_chars * 0.7):
        head = head[:last_head_nl]

    first_tail_nl = tail.find("\n")
    if 0 <= first_tail_nl < int(tail_chars * 0.3):
        tail = tail[first_tail_nl + 1:]

    omitted_chars = len(text) - len(head) - len(tail)
    omitted_slice = text[len(head):len(text) - len(tail)]
    omitted_lines = omitted_slice.count("\n")

    marker = f"\n... [已自动截断: 中间省略 {omitted_chars} 字符 / 约 {omitted_lines} 行回显] ...\n"
    return f"{head.rstrip()}{marker}{tail.lstrip()}"


class TokenBudgetEstimator:
    """轻量级 Token 预估器（针对中英混合及终端命令代码场景）"""

    @staticmethod
    def estimate_tokens(text: str | None) -> int:
        if not text:
            return 0
        # 中文字符（约 1.5 字符 / token）
        zh_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        # 英文及代码符号（约 3.5~4 字符 / token）
        other_chars = len(text) - zh_chars
        return int(zh_chars / 1.5 + other_chars / 3.5) + 1

    @classmethod
    def estimate_tools_tokens(cls, tools: list[dict[str, Any]] | None) -> int:
        """估算 tools schema 的 Token 开销（JSON 序列化近似 + 单个工具的协议结构开销）"""
        if not tools:
            return 0
        return sum(
            cls.estimate_tokens(json.dumps(tool, ensure_ascii=False)) + 8
            for tool in tools
        )

    @classmethod
    def estimate_messages_tokens(
        cls,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> int:
        total = 0
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, list):
                # 多模态消息：文本部分按字符估算，每张图片按固定开销估算
                text_parts = [
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ]
                image_count = sum(
                    1
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "image_url"
                )
                total += cls.estimate_tokens("".join(text_parts)) + image_count * 800 + 4
            else:
                total += cls.estimate_tokens(content) + 4  # 角色与元数据基准开销
        total += cls.estimate_tools_tokens(tools)
        return total
