"""应用层 - ReAct 状态机状态类型定义"""

from typing import Any, Dict, List, Optional, TypedDict

from domain.entities import SSHConnection


class ReActGraphState(TypedDict, total=False):
    # 上下文
    host: SSHConnection
    user_prompt: str
    images: List[str]
    session_id: Optional[str]
    max_steps: Optional[int]
    steering_message: Optional[str]
    terminal_buffer: Optional[str]
    os_family: str
    is_root: bool
    core_memories_text: str
    session_history: List[Dict[str, str]]

    # 权限与审批
    permission_mode: str
    approved_commands: List[str]
    pending_approval_id: Optional[str]
    is_plan_confirmed: bool

    # 循环推进状态
    step_idx: int
    planned_steps_queue: List[Dict[str, str]]
    react_history: List[str]
    executed_steps: List[Dict[str, Any]]

    # 当前步骤数据
    current_thought: str
    current_command: Optional[str]
    current_tool_name: Optional[str]
    current_tool_args: Optional[Dict[str, Any]]
    current_step_title: str
    current_observation: str
    current_reflection: str
    is_step_normal: bool

    # 阻断与自愈追踪
    is_blocked: bool
    blocked_reason: Optional[str]
    has_encountered_error: bool
    last_error_detail: Optional[str]
    consecutive_errors: int

    # 软预算与即时取消
    start_time: float
    max_run_seconds: Optional[float]
    llm_call_count: int
    max_llm_calls: Optional[int]
    total_tokens: int
    max_tokens: Optional[int]
    budget_exceeded: bool
    is_stalled: bool

    # 终态标记
    final_answer: Optional[str]
    final_conclusion: Optional[str]
    is_success: bool
    should_stop: bool

    # 本节点产生的事件列表（供外部流式消费者读取）
    emitted_events: List[Dict[str, Any]]
