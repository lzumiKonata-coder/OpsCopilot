"""应用层 - 多轮跨会话历史压缩器"""

import logging
import re

from utils.text import TokenBudgetEstimator, smart_truncate

from .profiles import resolve_context_profile

logger = logging.getLogger("smartops.application.react.compaction.session_compactor")


class SessionCompactor:
    """
    多轮跨会话历史压缩器。
    支持 Token 预算与轮次双重驱动：
    当历史轮次超出上限或 Token 消耗超出预算时，提炼前序任务结论与核心实体，保留最近活跃多轮。
    """

    def __init__(
        self,
        max_recent_turns: int | None = None,
        max_summary_chars: int = 500,
        max_session_tokens: int | None = None,
        model_name: str | None = None,
        reserved_tokens: int = 0,
    ):
        profile = resolve_context_profile(model_name)
        self.max_recent_turns = max_recent_turns if max_recent_turns is not None else profile["max_recent_turns"]
        self.max_summary_chars = max_summary_chars
        base_session_tokens = (
            max_session_tokens if max_session_tokens is not None else profile["max_session_tokens"]
        )
        # 预留固定上下文开销（如 tools schema），但至少保留 40% 预算避免过度压缩
        self.reserved_tokens = max(0, reserved_tokens)
        self.max_session_tokens = max(int(base_session_tokens * 0.4), base_session_tokens - self.reserved_tokens)
        self.estimator = TokenBudgetEstimator()

    def compact_session_history(
        self,
        session_history: list[dict[str, str]],
        current_user_prompt: str,
    ) -> list[dict[str, str]]:
        """
        清洗并压缩多轮会话历史：
        1. 过滤空消息与与当前输入重复的提问；
        2. 基于 Token 预算与轮次切分旧会话与最近活跃轮次；
        3. 将旧轮次提炼为结构化前序运维小结。
        """
        cleaned: list[dict[str, str]] = []
        clean_curr = (current_user_prompt or "").strip()

        for m in session_history:
            role = m.get("role", "user")
            raw_c = (m.get("content") or "").strip()
            if not raw_c:
                continue
            if role == "user" and raw_c == clean_curr:
                continue

            # 剥离 Markdown 报告长篇模板，提炼简明结论
            if role == "assistant" and ("###" in raw_c or "智能排障报告" in raw_c):
                m_concl = re.search(r"\*\*最终结论\*\*：\s*(.*?)(?=\n\n|\*\*|\Z)", raw_c, re.DOTALL)
                if m_concl:
                    brief = m_concl.group(1).strip()[:180]
                    cleaned.append({"role": "assistant", "content": f"历史任务已完成：{brief}"})
                else:
                    first_line = raw_c.splitlines()[0][:120]
                    cleaned.append({"role": "assistant", "content": first_line})
            else:
                cleaned.append({"role": role, "content": raw_c[:300]})

        total_tokens = self.estimator.estimate_messages_tokens(cleaned)
        needs_compaction = (len(cleaned) > self.max_recent_turns) or (total_tokens > self.max_session_tokens)

        if not needs_compaction:
            return cleaned

        # 动态确定保留的最近轮次数
        target_recent = min(self.max_recent_turns, len(cleaned))
        while target_recent > 2:
            subset = cleaned[-target_recent:]
            if self.estimator.estimate_messages_tokens(subset) + 150 <= self.max_session_tokens:
                break
            target_recent -= 1

        older = cleaned[:-target_recent]
        recent = cleaned[-target_recent:]

        # 提取前序轮次关键点（语义过滤，提取核心实体与动作）
        compacted_entry = None
        if older:
            summaries: list[str] = []
            for msg in older:
                r = msg.get("role", "")
                c = msg.get("content", "")
                if r == "user":
                    summaries.append(f"前序用户需求: {c[:80]}")
                elif r == "assistant":
                    summaries.append(f"前序执行结果: {c[:100]}")

            summary_text = "；".join(summaries)[:self.max_summary_chars]
            compacted_entry = {
                "role": "assistant",
                "content": f"[前序多轮会话历史小结]: {summary_text}。"
            }

        # ==========================================================
        # 最终兜底保障机制 (Hard Fallback Pruning)：
        # 若拼接小结后多轮会话依然超预算，执行强制二次压制：
        # 1. 允许活跃轮次降级至最近 2 条（1 轮问答）；
        # 2. 依然超预算时，对活跃轮次长文本强制执行智能截断。
        # ==========================================================
        result = ([compacted_entry] + recent) if compacted_entry else list(recent)
        total_result_tokens = self.estimator.estimate_messages_tokens(result)

        if total_result_tokens > self.max_session_tokens:
            # 1. 降级轮次
            if len(recent) > 2:
                recent = recent[-2:]
                result = ([compacted_entry] + recent) if compacted_entry else list(recent)
                total_result_tokens = self.estimator.estimate_messages_tokens(result)

            # 2. 截断活跃轮次过长内容
            if total_result_tokens > self.max_session_tokens:
                summary_tokens = self.estimator.estimate_tokens(compacted_entry["content"]) if compacted_entry else 0
                avail_tokens = max(40, self.max_session_tokens - summary_tokens - (len(recent) * 4))
                per_msg_tokens = max(20, avail_tokens // len(recent))
                # 转换 token 预算为安全字符限制
                per_msg_chars = max(50, int(per_msg_tokens * 1.5))

                pruned_recent = []
                for m in recent:
                    c = m.get("content", "")
                    if len(c) > per_msg_chars:
                        pruned_c = smart_truncate(c, max_chars=per_msg_chars, head_ratio=0.3)
                        pruned_recent.append({"role": m.get("role", "user"), "content": pruned_c})
                    else:
                        pruned_recent.append(m)
                result = ([compacted_entry] + pruned_recent) if compacted_entry else pruned_recent

        return result
