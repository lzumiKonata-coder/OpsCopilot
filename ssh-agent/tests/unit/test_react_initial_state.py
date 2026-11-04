"""跨请求初始状态回归：复用检查点时必须重置上一轮运行态标志"""

from application.usecases.react import (
    ReActExecutionContext,
    ReActRunOptions,
    ReActUseCase,
)
from domain.entities import AuthType, SSHConnection


def test_initial_state_resets_transient_runtime_flags():
    host = SSHConnection(
        name="h",
        host="1.1.1.1",
        port=22,
        username="root",
        auth_type=AuthType.PASSWORD,
    )
    options = ReActRunOptions(identifier="h", user_prompt="查看磁盘")
    ctx = ReActExecutionContext(
        host=host,
        session=None,
        options=options,
        os_family="Linux (通用环境)",
        is_root_user=False,
        memory_extra="",
        session_history=[],
        is_branching=False,
        forked_state={},
    )

    state = ReActUseCase.__new__(ReActUseCase)._build_initial_state(ctx)

    assert state["budget_exceeded"] is False
    assert state["is_stalled"] is False
    assert state["current_thought"] == ""
    assert state["current_command"] is None
    assert state["current_tool_name"] is None
    assert state["current_tool_args"] is None
    assert state["current_step_title"] == ""
    assert state["current_observation"] == ""
    assert state["current_reflection"] == ""
    assert state["is_step_normal"] is True
