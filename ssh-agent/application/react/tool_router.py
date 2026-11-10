"""应用层 - 当前步骤动态工具路由

根据用户目标、最近一步执行的命令/回显与用户插话，从全量工具中筛选出与当前步骤
相关的工具，避免每一轮都把全部工具 schema 注入模型上下文。
采用轻量关键词打分，不额外引入模型调用。
"""

import re
from typing import Any

# 基线下发工具：命令执行兜底与任务收敛，任何步骤都必须可用
BASELINE_TOOL_NAMES: tuple[str, ...] = (
    "execute_ssh_command",
    "complete_troubleshooting",
)

# 内置工具语义关键词（中英混合），用于与当前步骤文本做相关性打分
BUILTIN_TOOL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "read_remote_file": (
        "文件", "配置", "日志", "内容", "读取", "打开文件",
        "cat", "tail", "head", "less", "grep", "/etc/", "/var/log/",
    ),
    "edit_remote_file": (
        "配置", "修改", "编辑", "替换", "改成", "改为", "变更",
        "sed", "edit", "replace", "update",
    ),
    "write_remote_file": (
        "配置", "写入", "创建文件", "新建文件", "覆盖", "追加",
        "write", "create", "touch",
    ),
}

# 单轮最多下发的工具数量，防止 MCP 工具过多导致 schema 反向膨胀
MAX_ROUTED_TOOLS = 8

# 路由查询中最近一步文本的截断上限，避免长回显噪声干扰匹配
_MAX_COMMAND_CHARS = 200
_MAX_OBSERVATION_CHARS = 800
_MAX_ANALYSIS_CHARS = 300

_ASCII_WORD_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")
_NAME_SPLIT_PATTERN = re.compile(r"[_\-]+")


def build_routing_query(
    user_prompt: str,
    executed_steps: list[dict[str, Any]] | None = None,
    steering_texts: list[str] | None = None,
) -> str:
    """组装当前步骤路由查询：用户目标 + 最近一步执行信息 + 用户即时插话"""
    parts = [user_prompt or ""]

    steps = executed_steps or []
    if steps:
        last = steps[-1]
        parts.append(str(last.get("command") or "")[-_MAX_COMMAND_CHARS:])
        parts.append(str(last.get("title") or "")[-_MAX_COMMAND_CHARS:])
        parts.append(str(last.get("observation") or "")[-_MAX_OBSERVATION_CHARS:])
        parts.append(str(last.get("analysis") or "")[-_MAX_ANALYSIS_CHARS:])

    parts.extend(steering_texts or [])
    return "\n".join(part for part in parts if part).lower()


def route_tools(
    tool_schemas: list[dict[str, Any]],
    query: str,
    max_tools: int = MAX_ROUTED_TOOLS,
) -> list[dict[str, Any]]:
    """按相关性筛选工具 schema，始终保留基线工具，输出顺序与注册顺序一致"""
    if not tool_schemas:
        return []

    clean_query = (query or "").lower()
    scores: dict[str, int] = {}
    baseline_names = set(BASELINE_TOOL_NAMES)

    for schema in tool_schemas:
        name = _tool_name(schema)
        if name in baseline_names:
            continue
        score = 0
        if clean_query:
            score = sum(1 for kw in _tool_keywords(schema) if kw and kw in clean_query)
        if score > 0:
            scores[name] = score

    ranked = sorted(scores, key=lambda n: scores[n], reverse=True)
    routed_names = set(BASELINE_TOOL_NAMES) | set(ranked[: max(0, max_tools - len(baseline_names))])

    return [schema for schema in tool_schemas if _tool_name(schema) in routed_names]


def _tool_name(schema: dict[str, Any]) -> str:
    return str((schema.get("function") or {}).get("name") or "")


def _tool_keywords(schema: dict[str, Any]) -> set[str]:
    """工具关键词：内置语义词 + 工具名分段；MCP 工具额外纳入描述中的英文词"""
    function = schema.get("function") or {}
    name = _tool_name(schema)
    keywords = set(BUILTIN_TOOL_KEYWORDS.get(name, ()))

    for token in _NAME_SPLIT_PATTERN.split(name.lower()):
        if len(token) >= 3 and token != "mcp":
            keywords.add(token)

    if name.startswith("mcp__"):
        description = str(function.get("description") or "")
        keywords.update(_ASCII_WORD_PATTERN.findall(description.lower()))

    return keywords
