"""应用层 - ReAct 单任务步骤滑动窗口压缩器"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from utils.text import TokenBudgetEstimator, smart_truncate

from .profiles import resolve_context_profile
from .semantic import extract_semantic_step_fact

logger = logging.getLogger("smartops.application.react.compaction.react_compactor")


class ReActContextCompactor:
    """
    ReAct 单次排障任务内的步骤滑动窗口与阶段压缩器。
    双重约束决策机制：结合模型 Token 预算与活跃窗口尺寸，自适应切分前序旧步骤与活跃窗口步骤。
    前序步骤转化为结构化语义运维事实小结，活跃窗口保留最近步骤的详细物理回显。
    """

    def __init__(
        self,
        window_size: Optional[int] = None,
        max_observation_chars: Optional[int] = None,
        max_history_tokens: Optional[int] = None,
        model_name: Optional[str] = None,
        min_recent_steps: int = 2,
    ):
        profile = resolve_context_profile(model_name)
        self.model_name = model_name
        self.window_size = window_size if window_size is not None else profile["default_window_size"]
        self.max_observation_chars = (
            max_observation_chars if max_observation_chars is not None else profile["max_observation_chars"]
        )
        self.max_history_tokens = (
            max_history_tokens if max_history_tokens is not None else profile["max_history_tokens"]
        )
        self.min_recent_steps = max(1, min_recent_steps)
        self.estimator = TokenBudgetEstimator()

        # 最近一次压缩的 Token 统计指标（便于调试与遥测）
        self.last_stats: Dict[str, Any] = {}

    def truncate_observation(self, observation: Optional[str]) -> str:
        """对每步物理回显进行智能截断"""
        return smart_truncate(observation, max_chars=self.max_observation_chars, head_ratio=0.3)

    def estimate_step_tokens(self, step: Dict[str, Any]) -> int:
        """预估单步格式化后的 Token 消耗"""
        formatted = self._format_single_step(step)
        return self.estimator.estimate_tokens(formatted)

    def compact_react_history(
        self,
        executed_steps: List[Dict[str, Any]],
        window_size: Optional[int] = None,
    ) -> Tuple[Optional[str], List[str]]:
        summary, recent, _ = self.compact_react_history_with_stats(
            executed_steps=executed_steps,
            window_size=window_size,
        )
        return summary, recent

    def compact_react_history_with_stats(
        self,
        executed_steps: List[Dict[str, Any]],
        window_size: Optional[int] = None,
    ) -> Tuple[Optional[str], List[str], Dict[str, Any]]:
        """
        对已执行步骤进行动态 Token 预算与滑动窗口切分：
        1. 当步数与 Token 预算均在安全范围内时，保持全量详细输出；
        2. 当超出步数窗口或超出 max_history_tokens 时，动态保留最近活跃步骤，将更早步骤浓缩为结构化运维事实小结。
        返回: (compacted_summary, recent_formatted_steps, stats_dict)
        """
        if not executed_steps:
            stats = {"total_tokens": 0, "compressed": False}
            self.last_stats = stats
            return None, [], stats

        effective_window = window_size if window_size is not None else self.window_size
        total_steps = len(executed_steps)
        # 计算所有步骤单步格式化文本与各自 Token 预估
        step_tokens = [self.estimate_step_tokens(s) for s in executed_steps]
        total_uncompressed_tokens = sum(step_tokens)

        # 判定是否需要触发压缩：
        # 条件 A: 步数超出活跃窗口配置
        # 条件 B: 总未压缩 Token 超出历史 Token 预算
        needs_compaction = (total_steps > effective_window) or (total_uncompressed_tokens > self.max_history_tokens)

        if not needs_compaction:
            formatted = [self._format_single_step(s) for s in executed_steps]
            stats = {
                "total_tokens": total_uncompressed_tokens,
                "budget": self.max_history_tokens,
                "compressed": False,
                "recent_count": total_steps,
                "older_count": 0,
            }
            self.last_stats = stats
            return None, formatted, stats

        # 动态规划活跃窗口尺寸：
        # 初始目标活跃步数为 min(effective_window, total_steps)
        # 若最近活跃步数仍导致 Token 超标，逐步下调活跃步数至 min_recent_steps
        target_recent_count = min(effective_window, total_steps)
        if target_recent_count == total_steps and total_steps > self.min_recent_steps:
            target_recent_count = total_steps - 1

        while target_recent_count > self.min_recent_steps:
            recent_tokens = sum(step_tokens[-target_recent_count:])
            # 预留 300 tokens 用于小结
            if (recent_tokens + 300) <= self.max_history_tokens:
                break
            target_recent_count -= 1

        older_steps = executed_steps[:-target_recent_count]
        recent_steps = executed_steps[-target_recent_count:]

        # 生成语义结构化运维事实小结
        compacted_summary = None
        summary_tokens = 0
        if older_steps:
            summary_lines = [
                f"【历史排查阶段性小结 (已紧凑压缩前序 {len(older_steps)} 个步骤)】:",
                f"- 已推进轮次: 步骤 1 至步骤 {len(older_steps)} 已执行验证。",
                "- 关键事实与已排除/确认项:",
            ]
            for s in older_steps:
                summary_lines.append(extract_semantic_step_fact(s))
            compacted_summary = "\n".join(summary_lines)
            summary_tokens = self.estimator.estimate_tokens(compacted_summary)

        recent_formatted = [self._format_single_step(s) for s in recent_steps]
        recent_total_tokens = sum(self.estimator.estimate_tokens(f) for f in recent_formatted)
        current_total = summary_tokens + recent_total_tokens

        # ==========================================================
        # 最终兜底保障机制 (Hard Fallback Pruning)：
        # 若最近 min_recent_steps 步回显本身过大，导致压缩后依然超出 max_history_tokens，
        # 则启动最终兜底：
        # 1. 若近期步数 > 1 且超限严重，优先退化保留至最新 1 步；
        # 2. 对活跃窗口各步的物理回显实施动态强力首尾截断，严格确保总 Token 压制在预算以内。
        # ==========================================================
        fallback_pruned = False
        if current_total > self.max_history_tokens:
            fallback_pruned = True

            # 1. 深度降级：若有 2 步活跃窗口且总预算告急，将倒数第二步也压入历史小结
            if len(recent_steps) > 1 and len(executed_steps) > 1:
                # 预估仅保留最后 1 步的回显需要多少空间
                self.estimate_step_tokens(executed_steps[-1])
                # 若降为 1 步更有利于控制预算，则转移倒数第二步
                older_steps = executed_steps[:-1]
                recent_steps = executed_steps[-1:]

                summary_lines = [
                    f"【历史排查阶段性小结 (已紧凑压缩前序 {len(older_steps)} 个步骤)】:",
                    f"- 已推进轮次: 步骤 1 至步骤 {len(older_steps)} 已执行验证。",
                    "- 关键事实与已排除/确认项:",
                ]
                for s in older_steps:
                    summary_lines.append(extract_semantic_step_fact(s))
                compacted_summary = "\n".join(summary_lines)
                summary_tokens = self.estimator.estimate_tokens(compacted_summary)

            # 2. 强制压缩活跃窗口各步物理回显 (Observation)
            avail_recent_tokens = max(100, self.max_history_tokens - summary_tokens)
            per_step_token_budget = max(60, avail_recent_tokens // len(recent_steps))

            pruned_recent_formatted = []
            for s in recent_steps:
                # 计算 Thought / Action / Reflection 等非 Observation 开销
                overhead = self.estimator.estimate_tokens(
                    f"Step {s.get('step')}:\n- Thought: {s.get('thought', '')}\n- Action: `{s.get('command', '')}`\n- Reflection: {s.get('analysis', '')}"
                )
                avail_obs_tokens = max(20, per_step_token_budget - overhead)
                max_obs_chars = max(80, int(avail_obs_tokens * 3.0))

                raw_obs = s.get("observation", "")
                if len(raw_obs) > max_obs_chars:
                    pruned_obs = smart_truncate(raw_obs, max_chars=max_obs_chars, head_ratio=0.3)
                    pruned_s = dict(s, observation=pruned_obs)
                else:
                    pruned_s = s
                pruned_recent_formatted.append(self._format_single_step(pruned_s))

            recent_formatted = pruned_recent_formatted
            recent_total_tokens = sum(self.estimator.estimate_tokens(f) for f in recent_formatted)
            current_total = summary_tokens + recent_total_tokens

        stats = {
            "total_tokens": current_total,
            "budget": self.max_history_tokens,
            "compressed": True,
            "recent_count": len(recent_steps),
            "older_count": len(older_steps),
            "fallback_pruned": fallback_pruned,
        }
        self.last_stats = stats

        return compacted_summary, recent_formatted, stats

    def format_prompt_history(
        self,
        executed_steps: List[Dict[str, Any]],
        raw_react_history: Optional[List[str]] = None,
        window_size: Optional[int] = None,
    ) -> str:
        text, _ = self.format_prompt_history_with_stats(
            executed_steps=executed_steps,
            raw_react_history=raw_react_history,
            window_size=window_size,
        )
        return text

    def format_prompt_history_with_stats(
        self,
        executed_steps: List[Dict[str, Any]],
        raw_react_history: Optional[List[str]] = None,
        window_size: Optional[int] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        组装注入大模型提示词的历史执行记录，并纯函数式返回本调用的监控指标字典：
        返回: (formatted_history_text, stats_dict)
        """
        if not executed_steps:
            stats = {"total_tokens": 0, "compressed": False}
            if raw_react_history:
                return "\n".join(raw_react_history), stats
            return "（暂无历史执行记录，请开始第 1 步探测）", stats

        compacted_summary, recent_formatted, stats = self.compact_react_history_with_stats(
            executed_steps, window_size=window_size
        )

        if compacted_summary is None:
            if raw_react_history and len(raw_react_history) == len(executed_steps):
                return "\n".join(raw_react_history), stats
            return "\n".join(recent_formatted), stats

        text = (
            f"{compacted_summary}\n\n"
            f"【近期详细推演与物理回显 (活跃窗口)】:\n"
            + "\n".join(recent_formatted)
        )
        return text, stats

    @staticmethod
    def _format_single_step(step: Dict[str, Any]) -> str:
        step_idx = step.get("step", 1)
        thought = step.get("thought", "")
        cmd = step.get("command", "")
        obs = step.get("observation", "")
        analysis = step.get("analysis", "")
        return (
            f"Step {step_idx}:\n"
            f"- Thought: {thought}\n"
            f"- Action: `{cmd}`\n"
            f"- Observation:\n{obs}\n"
            f"- Reflection: {analysis}"
        )
