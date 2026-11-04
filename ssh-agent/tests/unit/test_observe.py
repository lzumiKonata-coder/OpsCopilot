"""Observe 节点回归：成功步骤必须清空上一轮的 last_error_detail"""

import pytest

from adapter.tools.builtin import build_default_registry
from application.react.engine.observation_analysis import ObservationAnalysisMixin
from application.react.nodes.observe import ObserveAndReflectNodeMixin
from application.tool_registry import ToolRegistry


class _Harness(ObservationAnalysisMixin, ObserveAndReflectNodeMixin):
    tool_registry = ToolRegistry()


def _stale_error_state():
    return {
        "current_command": "echo ok",
        "current_observation": "ok",
        "current_thought": "执行确认",
        "current_step_title": "确认状态",
        "step_idx": 1,
        "planned_steps_queue": [],
        "consecutive_errors": 2,
        "has_encountered_error": True,
        "last_error_detail": "上一轮遗留错误",
        "executed_steps": [],
        "react_history": [],
    }


@pytest.mark.asyncio
async def test_command_success_clears_stale_error_detail():
    result = await _Harness()._node_observe_and_reflect(_stale_error_state())

    assert result["last_error_detail"] is None
    assert result["has_encountered_error"] is False


@pytest.mark.asyncio
async def test_read_file_success_clears_stale_error_detail():
    harness = _Harness()
    harness.tool_registry = build_default_registry()
    state = _stale_error_state()
    state.update({
        "current_command": "cat /tmp/app.conf",
        "current_tool_name": "read_remote_file",
        "current_tool_args": {"path": "/tmp/app.conf"},
        "current_observation": "port = 8080",
        "current_step_title": "读取配置",
    })

    result = await harness._node_observe_and_reflect(state)

    assert result["last_error_detail"] is None
    assert result["has_encountered_error"] is False
