"""应用层 - 模型上下文容量自适应画像"""

import logging
import re
from typing import Any

logger = logging.getLogger("smartops.application.react.compaction.profiles")

MODEL_CONTEXT_PROFILES: dict[str, dict[str, Any]] = {
    # 1M 超长上下文模型：Gemini 2.5 Pro, Qwen2.5-1M 等
    "1m": {
        "context_pool_tokens": 48000,
        "max_history_tokens": 32000,
        "default_window_size": 12,
        "max_recent_turns": 16,
        "max_observation_chars": 10000,
        "max_session_tokens": 16000,
        "max_output_tokens": 6000,
    },
    # 128k 超长上下文模型：GLM-4-Plus, DeepSeek-V3/R1, Qwen-2.5-72B, GPT-4o
    "128k": {
        "context_pool_tokens": 24000,
        "max_history_tokens": 16000,
        "default_window_size": 8,
        "max_recent_turns": 10,
        "max_observation_chars": 6000,
        "max_session_tokens": 8000,
        "max_output_tokens": 3000,
    },
    # 32k / 64k 模型：GLM-4-Flash, Claude 3.5 Sonnet
    "32k": {
        "context_pool_tokens": 12000,
        "max_history_tokens": 8000,
        "default_window_size": 5,
        "max_recent_turns": 6,
        "max_observation_chars": 4000,
        "max_session_tokens": 4000,
        "max_output_tokens": 2000,
    },
    # 8k / 16k 标准基准（默认保底配置）
    "standard": {
        "context_pool_tokens": 5500,
        "max_history_tokens": 3500,
        "default_window_size": 3,
        "max_recent_turns": 4,
        "max_observation_chars": 2000,
        "max_session_tokens": 2000,
        "max_output_tokens": 1200,
    },
}

# 共享输入预算池在步骤历史与会话历史之间的分配比例（约 2:1）
STEP_HISTORY_BUDGET_RATIO = 2 / 3
# 固定开销挤占池子时的最低保留比例，避免预算被工具 schema 与输出预留吃光
MIN_AVAILABLE_POOL_RATIO = 0.4
# 1M 画像的显式版本标识，要求 1m 作为独立片段出现（如 qwen2.5-1m），避免宽松子串误判
_ONE_M_TOKEN_PATTERN = re.compile(r"(^|[-_])1m($|[-_])")
# 已知提供 1M 及以上窗口的 Gemini 版本前缀
_ONE_M_GEMINI_PREFIXES = ("gemini-2", "gemini-3")


def allocate_context_budgets(
    profile: dict[str, Any],
    tool_tokens: int = 0,
) -> tuple[int, int]:
    """
    从共享输入预算池中统一扣除工具 schema 与输出预留，再分配给两个消费方。
    返回: (步骤历史预算, 会话历史预算)；固定开销只扣一次，避免两个压缩器各自重复预留。
    """
    # 兼容缺少新字段的旧画像：回落到两个消费方预算之和，输出预留按 0 处理
    pool = profile.get("context_pool_tokens")
    if pool is None:
        pool = profile["max_history_tokens"] + profile["max_session_tokens"]
    fixed_tokens = max(0, tool_tokens) + profile.get("max_output_tokens", 0)

    available = max(int(pool * MIN_AVAILABLE_POOL_RATIO), pool - fixed_tokens)
    if pool - fixed_tokens < int(pool * MIN_AVAILABLE_POOL_RATIO):
        logger.warning(
            "Context fixed cost (%d tokens: tools+output) exceeds %.0f%% of shared pool (%d); "
            "available budget clamped to %d, actual prompt may exceed model window",
            fixed_tokens,
            (1 - MIN_AVAILABLE_POOL_RATIO) * 100,
            pool,
            available,
        )

    step_history_budget = int(available * STEP_HISTORY_BUDGET_RATIO)
    return step_history_budget, available - step_history_budget


def resolve_context_profile(model_name: str | None = None) -> dict[str, Any]:
    """根据大模型名称动态解析上下文预算配置画像"""
    if not model_name:
        return dict(MODEL_CONTEXT_PROFILES["standard"])

    clean = model_name.strip().lower()
    is_one_m_model = (
        bool(_ONE_M_TOKEN_PATTERN.search(clean))
        or "million" in clean
        or any(prefix in clean for prefix in _ONE_M_GEMINI_PREFIXES)
    )
    if is_one_m_model:
        return dict(MODEL_CONTEXT_PROFILES["1m"])
    if any(k in clean for k in ["glm-4-plus", "glm-4.7", "deepseek", "gpt-4o", "qwen-2.5", "qwen2.5", "128k"]):
        return dict(MODEL_CONTEXT_PROFILES["128k"])
    if any(k in clean for k in ["glm-4", "flash", "claude", "32k", "64k"]):
        return dict(MODEL_CONTEXT_PROFILES["32k"])

    return dict(MODEL_CONTEXT_PROFILES["standard"])
