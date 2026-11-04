"""应用层 - 模型上下文容量自适应画像"""

from typing import Any, Dict, Optional

MODEL_CONTEXT_PROFILES: Dict[str, Dict[str, Any]] = {
    # 128k 超长上下文模型：GLM-4-Plus, DeepSeek-V3/R1, Qwen-2.5-72B, GPT-4o
    "128k": {
        "max_history_tokens": 16000,
        "default_window_size": 8,
        "max_recent_turns": 10,
        "max_observation_chars": 6000,
        "max_session_tokens": 8000,
    },
    # 32k / 64k 模型：GLM-4-Flash, Claude 3.5 Sonnet
    "32k": {
        "max_history_tokens": 8000,
        "default_window_size": 5,
        "max_recent_turns": 6,
        "max_observation_chars": 4000,
        "max_session_tokens": 4000,
    },
    # 8k / 16k 标准基准（默认保底配置）
    "standard": {
        "max_history_tokens": 3500,
        "default_window_size": 3,
        "max_recent_turns": 4,
        "max_observation_chars": 2000,
        "max_session_tokens": 2000,
    },
}


def resolve_context_profile(model_name: Optional[str] = None) -> Dict[str, Any]:
    """根据大模型名称动态解析上下文预算配置画像"""
    if not model_name:
        return dict(MODEL_CONTEXT_PROFILES["standard"])

    clean = model_name.strip().lower()
    if any(k in clean for k in ["glm-4-plus", "glm-4.7", "deepseek", "gpt-4o", "qwen-2.5", "qwen2.5", "128k"]):
        return dict(MODEL_CONTEXT_PROFILES["128k"])
    if any(k in clean for k in ["glm-4", "flash", "claude", "32k", "64k"]):
        return dict(MODEL_CONTEXT_PROFILES["32k"])

    return dict(MODEL_CONTEXT_PROFILES["standard"])
