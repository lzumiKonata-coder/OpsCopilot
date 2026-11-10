"""应用层 - ReAct 状态机状态类型定义"""

from typing import Any, TypedDict

from domain.entities import SSHConnection


class ReActGraphState(TypedDict, total=False):
    # 上下文
    host: SSHConnection
    user_prompt: str
    images: list[str]
    session_id: str | None
    max_steps: int | None
    steering_message: str | None
    terminal_buffer: str | None
    os_family: str
    is_root: bool
    core_memories_text: str
    session_history: list[dict[str, str]]
    allowed_tool_names: list[str]

    # 权限与审批
    permission_mode: str
    approved_commands: list[str]
    pending_approval_id: str | None
    is_plan_confirmed: bool

    # 循环推进状态
    step_idx: int
    planned_steps_queue: list[dict[str, str]]
    react_history: list[str]
    executed_steps: list[dict[str, Any]]

    # 当前步骤数据
    current_thought: str
    current_command: str | None
    current_tool_name: str | None
    current_tool_call_id: str | None
    current_tool_args: dict[str, Any] | None
    current_step_title: str
    current_observation: str
    current_reflection: str
    is_step_normal: bool

    # 阻断与自愈追踪
    is_blocked: bool
    blocked_reason: str | None
    has_encountered_error: bool
    last_error_detail: str | None
    consecutive_errors: int

    # 软预算与即时取消
    start_time: float
    max_run_seconds: float | None
    llm_call_count: int
    max_llm_calls: int | None
    total_tokens: int
    max_tokens: int | None
    budget_exceeded: bool
    is_stalled: bool

    # 终态标记
    final_answer: str | None
    final_conclusion: str | None
    is_success: bool
    should_stop: bool

    # 本节点产生的事件列表（供外部流式消费者读取）
    emitted_events: list[dict[str, Any]]
