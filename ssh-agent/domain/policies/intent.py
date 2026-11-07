"""领域层 - 运维意图与命令类型判定策略"""

import re
from typing import Any

_READ_ONLY_INTENT_RE = re.compile(
    r"^(?:(?:请|麻烦|帮我|帮忙|给我|能否|可以)\s*)*"
    r"(?:查看|检查|确认|列出|显示|查询|获取|看下|看一下|看看|展示|list|show|get|check)",
    re.IGNORECASE,
)

_ACTION_SEQUENCE_RE = re.compile(
    r"(?:然后|接着|之后|随后|再|并|后)\s*"
    r"(?:安装|部署|配置|修改|编辑|重启|启动|停止|删除|清理|卸载|新建|创建|修复|设置)",
    re.IGNORECASE,
)

_MULTI_TARGET_SEPARATOR_RE = re.compile(
    r"(?:和|与|以及|、|，|,|同时|还有|外加|加上|\band\b)",
    re.IGNORECASE,
)


def is_read_only_query_prompt(prompt: str) -> bool:
    """判断用户需求是否为单纯的只读查询/信息查看任务（严禁在获取结果后无端发散深挖）"""
    # 与 is_mutation_task 复用同一套显式只读意图判断，避免“查看已安装/帮我查看”等场景两套规则不一致
    return has_explicit_read_only_intent(prompt)


def has_explicit_read_only_intent(prompt: str) -> bool:
    """
    判断用户是否以明确的只读动作发起请求。
    仅当句首为查询类动词，且后续未通过“并/然后/后”等连接词追加变更动作时成立，
    用于避免“查看配置/查看已安装软件/检查是否已启动”等只读请求被关键词误判为变更任务。
    """
    clean = (prompt or "").strip().lower()
    if not _READ_ONLY_INTENT_RE.match(clean):
        return False
    if _ACTION_SEQUENCE_RE.search(clean):
        return False
    return True


def is_multi_target_read_only_prompt(prompt: str) -> bool:
    """判断是否为包含多个查询目标的只读请求，避免第一条命令成功后过早收敛。"""
    if not is_read_only_query_prompt(prompt):
        return False
    return bool(_MULTI_TARGET_SEPARATOR_RE.search(prompt or ""))


def is_mutation_task(prompt: str) -> bool:
    """判断是否为带有系统状态变更（安装/部署/配置/启动/停止/重启/删除/创建）的目标任务"""
    clean = (prompt or "").strip().lower()
    if has_explicit_read_only_intent(clean):
        return False
    keywords = [
        "安装", "install", "部署", "deploy", "配置", "config", "setup",
        "启动", "start", "停止", "stop", "重启", "restart",
        "删除", "清理", "卸载", "remove", "delete", "uninstall", "clean",
        "创建", "新建", "create", "mkdir", "touch", "修复", "fix", "自愈",
    ]
    return any(k in clean for k in keywords)


def is_inspection_command(command: str) -> bool:
    """判断是否为排障只读查看类指令（日志/文件/进程/版本状态查询，输出不等于命令自身执行崩溃）"""
    clean_cmd = (command or "").strip().lower()
    tokens = clean_cmd.split()
    while tokens and tokens[0] in {"sudo", "nohup", "command", "exec"}:
        tokens = tokens[1:]
    if not tokens:
        return False
    bin_name = tokens[0].rsplit("/", 1)[-1]
    inspection_bins = {
        "journalctl", "dmesg", "cat", "grep", "egrep", "fgrep",
        "tail", "head", "less", "more", "awk", "sed", "strings", "file",
        "ls", "stat", "find", "which", "whereis", "type", "uname",
        "df", "du", "free", "ps", "top", "uptime", "ss", "netstat",
        "lsof", "ip", "ifconfig", "ping", "curl", "rpm", "dpkg",
    }
    if bin_name in inspection_bins:
        return True

    # 探查参数（如 --version, -v, -V, version, status, info）
    version_or_status_args = {"--version", "-v", "-V", "-version", "version", "status", "-h", "--help", "info"}
    if any(arg in tokens[1:] for arg in version_or_status_args):
        return True

    return False


def is_mutation_already_satisfied(
    thought: str,
    final_answer: str | None,
    executed_steps: list[dict[str, Any]],
) -> bool:
    """
    检查变更/安装类任务是否经由物理探查确认目标已达成/无需重复安装。
    若前序已执行探查命令，且探查物理回显或模型推演明确表明服务/软件包“已安装/已就绪/正在运行/环境完整”，
    允许基于探测事实自然收敛交付，杜绝误杀为“任务未达成”。
    """
    check_text = (thought + " " + (final_answer or "")).lower()

    # 核心防误判：若文本在描述前置执行方案、探查思路或征询用户确认，绝不能判定为“已达成”
    plan_indicators = [
        "是否按这个思路", "是否开始执行", "方案确认", "分步执行思路",
        "是否已安装", "是否安装", "需先探测", "需先检查", "再选择适配",
        "整个安装过程", "风险主要为", "可控可退", "执行思路", "前期方案",
        "执行计划", "操作风险",
    ]
    if any(p in check_text for p in plan_indicators):
        return False

    if not executed_steps:
        # 若未在本次会话中执行物理探查，但模型结合长期记忆明确指出已知事实（已安装且正常运行/无需重复安装）
        has_verified_memory_fact = (
            any(k in check_text for k in ["无需安装", "无需重复安装", "已安装且", "已经安装完成", "已处于 active", "已是最新版本", "目标已达成，无需"])
            and any(k in check_text for k in ["长期记忆", "此前运维记忆", "此前记录", "已知事实", "记忆"])
        )
        return has_verified_memory_fact

    check_text = (thought + " " + (final_answer or "")).lower()
    satisfied_keywords = [
        "已安装", "已就绪", "正在运行", "已存在", "已经存在", "无需安装", "无需重复",
        "无需处理", "已是最新", "正常运行", "active (running)", "is active", "already installed",
        "目标已达成", "已处于运行状态", "环境完整可用", "就绪", "无需执行", "已配置",
    ]
    if any(kw in check_text for kw in satisfied_keywords):
        return True

    last_step = executed_steps[-1]
    last_obs = (last_step.get("observation") or "").lower()
    if any(k in last_obs for k in ["active", "installed", "version 2", "version 1", "version 3"]):
        return True

    return False
