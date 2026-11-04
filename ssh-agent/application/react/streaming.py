"""应用层 - 流式 Thought 增量提取过滤器"""

import re
from typing import Callable


class StreamingThoughtFilter:
    """
    流式 Thought 提取过滤器
    在 ReAct 状态机中过滤大模型流式输出：
    1. 自动剥离前缀（如 'Thought:', '**Thought:**', '思考:' 等）；
    2. 实时向下游回调推送 Thought 字符增量；
    3. 截断后续 Action: / Final_Answer: / Step_Title: 等动作字段，防止污染思考卡片。
    """

    PREFIX_PATTERNS = [
        re.compile(r"^\s*(?:\*\*)?(?:Thought|思考|分析|推演)(?:\*\*)?[:：]?(?:\*\*)?[:：]?\s*", re.IGNORECASE),
        re.compile(r"^\s*###\s*(?:\d+\.\s*)?(?:Thought|思考|分析|推演|根因分析)(?:\*\*)?[:：]?(?:\*\*)?[:：]?\s*", re.IGNORECASE),
    ]

    BOUNDARY_PATTERNS = [
        re.compile(r"\n\s*(?:Action|Final_Answer|FinalAnswer|Step_Title|Plan|方案|Tool_Call)[:：]", re.IGNORECASE),
    ]

    def __init__(self, on_thought_delta: Callable[[str], None]):
        self.on_thought_delta = on_thought_delta
        self.buffer = ""
        self.prefix_stripped = False
        self.stopped = False
        self.emitted_length = 0

    def feed(self, chunk: str) -> None:
        if self.stopped or not chunk:
            return

        self.buffer += chunk

        # 阶段 1：去除前缀
        if not self.prefix_stripped:
            clean_buf = self.buffer.strip().lower()
            if len(self.buffer) < 25:
                possible_prefix = False
                for kw in ["**thought", "thought", "思考", "###"]:
                    if kw.startswith(clean_buf) or clean_buf.startswith(kw):
                        possible_prefix = True
                        break
                if possible_prefix and ":" not in self.buffer and "：" not in self.buffer:
                    return

            for p in self.PREFIX_PATTERNS:
                m = p.match(self.buffer)
                if m and (":" in m.group(0) or "：" in m.group(0) or "###" in m.group(0)):
                    self.buffer = self.buffer[m.end():]
                    break
            self.prefix_stripped = True

        # 阶段 2：检测截断边界（如 \nAction:）
        for bp in self.BOUNDARY_PATTERNS:
            m = bp.search(self.buffer)
            if m:
                thought_part = self.buffer[:m.start()]
                if len(thought_part) > self.emitted_length:
                    delta = thought_part[self.emitted_length:]
                    self.emitted_length += len(delta)
                    self._emit(delta)
                self.stopped = True
                return

        # 阶段 3：安全发射（保留末尾 15 字符缓冲区，防止截断边界被切割跨 chunk）
        SAFETY_MARGIN = 15
        if len(self.buffer) > SAFETY_MARGIN:
            safe_length = len(self.buffer) - SAFETY_MARGIN
            if safe_length > self.emitted_length:
                delta = self.buffer[self.emitted_length:safe_length]
                self.emitted_length += len(delta)
                self._emit(delta)

    def close(self) -> None:
        if self.stopped:
            return
        if not self.prefix_stripped:
            for p in self.PREFIX_PATTERNS:
                m = p.match(self.buffer)
                if m and (":" in m.group(0) or "：" in m.group(0) or "###" in m.group(0)):
                    self.buffer = self.buffer[m.end():]
                    break
            self.prefix_stripped = True

        for bp in self.BOUNDARY_PATTERNS:
            m = bp.search(self.buffer)
            if m:
                self.buffer = self.buffer[:m.start()]
                break

        if len(self.buffer) > self.emitted_length:
            delta = self.buffer[self.emitted_length:]
            self.emitted_length += len(delta)
            self._emit(delta)
        self.stopped = True

    def _emit(self, text: str) -> None:
        if text:
            self.on_thought_delta(text)
