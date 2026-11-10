"""动态工具路由测试"""

from adapter.tools.builtin import build_default_registry
from application.react.nodes.think.llm import resolve_tool_call
from application.react.tool_router import build_routing_query, route_tools


def _names(tool_schemas):
    return [schema["function"]["name"] for schema in tool_schemas]


def _mcp_schema(server: str, tool_name: str, description: str = "") -> dict:
    return {
        "type": "function",
        "function": {
            "name": f"mcp__{server}__{tool_name}",
            "description": description or tool_name,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_generic_query_keeps_only_baseline_tools():
    schemas = build_default_registry().specs()

    routed = route_tools(schemas, "检查服务器负载并查看磁盘使用率")

    assert _names(routed) == ["execute_ssh_command", "complete_troubleshooting"]


def test_file_keywords_route_file_tools():
    schemas = build_default_registry().specs()

    read_names = _names(route_tools(schemas, "查看 nginx 配置文件"))
    write_names = _names(route_tools(schemas, "写入新的配置内容"))
    edit_names = _names(route_tools(schemas, "把监听端口改成 8080"))

    assert "read_remote_file" in read_names
    assert "write_remote_file" in write_names
    assert "edit_remote_file" in edit_names
    # 基线工具始终存在
    for names in (read_names, write_names, edit_names):
        assert {"execute_ssh_command", "complete_troubleshooting"} <= set(names)


def test_mcp_tool_routed_by_name_token_and_excluded_otherwise():
    schemas = [_mcp_schema("github", "search_issues", "Search issues in repository")]

    assert _names(route_tools(schemas, "帮我搜索 github issues")) == ["mcp__github__search_issues"]
    assert route_tools(schemas, "重启 nginx 服务") == []


def test_max_tools_caps_non_baseline_selection():
    schemas = build_default_registry().specs()
    schemas.append(_mcp_schema("ops", "config_backup", "backup config file"))

    routed = route_tools(schemas, "读取配置文件并备份配置", max_tools=2)

    # max_tools=2 时仅保留两个基线工具
    assert _names(routed) == ["execute_ssh_command", "complete_troubleshooting"]


def test_build_routing_query_includes_last_step_and_steering():
    query = build_routing_query(
        "排查 nginx",
        executed_steps=[{"command": "cat /etc/nginx/nginx.conf", "observation": "server { listen 80; }"}],
        steering_texts=["顺便检查日志"],
    )

    assert "排查 nginx" in query
    assert "cat /etc/nginx/nginx.conf" in query
    assert "listen 80" in query
    assert "顺便检查日志" in query


def test_resolve_tool_call_rejects_tool_outside_whitelist():
    registry = build_default_registry()
    tool_call = {
        "name": "read_remote_file",
        "arguments": {"path": "/etc/nginx/nginx.conf", "step_title": "读取配置"},
    }

    assert resolve_tool_call(
        registry,
        tool_call,
        1,
        "",
        allowed_tool_names={"execute_ssh_command", "complete_troubleshooting"},
    ) is None

    invocation = resolve_tool_call(registry, tool_call, 1, "", allowed_tool_names={"read_remote_file"})
    assert invocation is not None
    assert invocation["tool_name"] == "read_remote_file"


def test_resolve_tool_call_without_whitelist_keeps_legacy_fallback():
    registry = build_default_registry()
    tool_call = {"name": "unknown_tool", "arguments": {"command": "uptime", "step_title": "检查负载"}}

    invocation = resolve_tool_call(registry, tool_call, 1, "")

    assert invocation is not None
    assert invocation["command"] == "uptime"
