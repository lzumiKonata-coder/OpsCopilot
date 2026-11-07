"""内置工具的声明式定义测试"""

from adapter.tools.builtin import build_default_registry
from application.ports import ToolOperation
from application.tool_registry import ToolRegistry


def test_builtin_registry_exposes_openai_tool_schemas():
    registry = build_default_registry()

    assert len(registry) == 5
    specs = registry.specs()
    for spec in specs:
        assert spec["type"] == "function"
        assert spec["function"]["name"]
        assert spec["function"]["parameters"]["type"] == "object"


def test_builtin_registry_operations_and_normalize():
    registry = build_default_registry()

    read_tool = registry.get("read_remote_file")
    assert read_tool is not None
    assert read_tool.spec.operation == ToolOperation.READ_FILE
    invocation = read_tool.normalize({"path": "/etc/nginx/nginx.conf"}, 2)
    assert invocation.command == "cat /etc/nginx/nginx.conf"
    assert invocation.step_title == "读取文件 /etc/nginx/nginx.conf"

    complete_tool = registry.get("complete_troubleshooting")
    assert complete_tool is not None
    invocation = complete_tool.normalize({"final_answer": "已完成。"}, 3)
    assert invocation.command is None
    assert invocation.final_answer == "已完成。"


def test_registry_rejects_duplicate_tool_names():
    registry = build_default_registry()
    tool = registry.get("execute_ssh_command")
    assert tool is not None
    try:
        registry.register(tool)
    except ValueError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("重复注册同名工具必须报错")


def test_empty_registry_has_no_specs():
    registry = ToolRegistry()
    assert registry.specs() == []
