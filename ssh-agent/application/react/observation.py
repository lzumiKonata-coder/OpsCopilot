"""应用层 - 终端回显解析、错误检测与归一化"""

import re

from domain.policies.intent import is_inspection_command


def detect_interactive_prompt(text: str | None) -> str | None:
    """
    从终端物理回显中检测是否包含交互式确认提示符（如 [y/N]、是否删除...？、Do you want to continue? 等）
    若检测到，返回提示关键短语；否则返回 None
    """
    if not text:
        return None
    clean = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text).strip()
    if not clean:
        return None

    patterns = [
        r"(\[[a-zA-Z0-9/,\s]*[yY]/[nN][a-zA-Z0-9/,\s]*\]|\([a-zA-Z0-9/,\s]*[yY]/[nN][a-zA-Z0-9/,\s]*\))",
        r"(是否(?:删除|覆盖|继续|确定|移除).*?[?？])",
        r"((?:remove|delete|overwrite|continue|proceed|abort).*?[?？]\s*$)",
        r"(is this ok\s*\[[a-zA-Z0-9/]+\]:?)",
        r"(do you want to continue\s*\[[a-zA-Z0-9/]+\])",
        r"(?:^|\n).*(?:rm|cp|mv|apt|yum|dnf).*?[?？]\s*$",
    ]

    for pat in patterns:
        m = re.search(pat, clean, re.IGNORECASE)
        if m:
            return m.group(0).strip()

    tail = clean.splitlines()[-1].strip() if clean.splitlines() else ""
    if tail and (tail.endswith("?") or tail.endswith("？")):
        return tail

    return None


PROMPT_TERMINAL_RE = re.compile(r"(?:[#$]|\][#$]|:[~/\w\.-]+[#$])\s*$")


def extract_command_payload(command: str, raw_observation: str) -> str:
    """
    从 PTY 物理终端回显中彻底剥离键盘回显与 Prompt 提示符，提取命令真正产生的 stdout 内容：
    1. 剥离 ANSI 转义字符与兜底提示；
    2. 若第一行包含当前下发命令（PTY 键盘回显），剔除首行；
    3. 若末行是 shell 提示符（如 [root@hadoop100 ~]# 或 $ 结尾），剔除末行。
    """
    if not raw_observation:
        return ""
    raw_stripped = raw_observation.strip()
    if raw_stripped in {"(命令执行完成，无标准输出回显)", "(终端执行完成)", "(无标准输出回显)"}:
        return ""

    clean = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", raw_stripped).strip()
    if not clean:
        return ""
    lines = [ln.rstrip() for ln in clean.split("\n")]

    clean_cmd = (command or "").strip()
    if lines and clean_cmd and (clean_cmd in lines[0] or lines[0].endswith(clean_cmd)):
        lines = lines[1:]

    if lines and PROMPT_TERMINAL_RE.search(lines[-1]):
        lines = lines[:-1]

    return "\n".join(lines).strip()


def is_empty_observation(command: str, raw_observation: str) -> bool:
    """判断命令回显在去除键盘回显与提示符后，是否真正没有任何有效输出"""
    return not extract_command_payload(command, raw_observation)


def detect_command_error(command: str, observation: str) -> str | None:
    """
    从 PTY 终端回显中检测指令物理执行失败特征
    已增强：智能区分查看日志产生的包含 error 输出 vs 指令自身崩溃
    """
    if not observation:
        return None
    # 过滤 ANSI 转义字符
    clean_obs = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", observation)
    obs_lower = clean_obs.lower()
    cmd_lower = (command or "").strip().lower()

    # 判定是否为排障只读查看类指令 (grep, cat, tail, journalctl, dmesg, ss 等)
    is_inspecting = is_inspection_command(command)

    # 1. 命令未找到
    cmd_not_found_keys = ["command not found", "找不到命令", "未找到命令", "could not find command"]
    if any(k in obs_lower for k in cmd_not_found_keys):
        # 排除命令参数中本身包含此关键字的场景 (如 grep -rn "command not found" /var/log/messages)
        if not any(k in cmd_lower for k in cmd_not_found_keys):
            # 若是查看类指令，只有当 shell 明确提示该查看工具自身未找到时才算
            if not is_inspecting or re.search(r"(?:bash|sh|zsh|line \d+):\s*[\w\-\.\/]+:\s*(?:command not found|找不到命令|未找到命令)", obs_lower):
                return "命令未找到 (command not found)，目标系统未安装该工具或环境变量不包含该路径"

    # 2. 系统服务单元不存在
    if not is_inspecting and any(k in obs_lower for k in ["unit not found", "service could not be found", "not loaded"]):
        return "服务单元不存在 (Unit not found)，可能服务未安装或服务名不匹配"

    # 3. 文件或目录不存在（只读检查类命令查不到文件属于正常排查回显，非指令崩溃）
    if not is_inspecting and any(k in obs_lower for k in ["no such file or directory", "没有那个文件或目录"]):
        return "文件或目录不存在 (No such file or directory)"

    # 4. 权限不足
    perm_denied_keys = ["permission denied", "权限不够", "operation not permitted"]
    if any(k in obs_lower for k in perm_denied_keys):
        # 排除命令参数中本身在检索权限错误（如 grep -i "permission denied"）
        if not any(k in cmd_lower for k in perm_denied_keys):
            if not is_inspecting:
                return "操作权限不足 (Permission denied)"
            else:
                # 查看类命令只有当命令工具自身抛出权限不足（如 cat: /etc/shadow: Permission denied）才算；检索到的日志内容不算
                tokens = cmd_lower.split()
                while tokens and tokens[0] in {"sudo", "nohup", "command", "exec"}:
                    tokens = tokens[1:]
                bin_name = tokens[0].rsplit("/", 1)[-1] if tokens else ""
                tool_pattern = rf"\b(?:sudo|bash|sh|zsh|{re.escape(bin_name)}):\s*.*(?:permission denied|权限不够|operation not permitted)"
                if re.search(tool_pattern, obs_lower):
                    return "操作权限不足 (Permission denied)"

    # 5. 包管理器检索与安装异常 (apt / yum / dnf)
    if not is_inspecting and any(k in obs_lower for k in [
        "unable to locate package", "no package", "no match for argument",
        "cannot find a valid baseurl", "could not resolve host",
        "failed to download", "package not found", "error: nothing to do",
        "e: unable to", "e: failed to fetch", "is not available, but is referred to"
    ]):
        return "软件包仓库检索或下载失败 (Package not found / Repository error)"

    # 6. 服务启动或操作执行失败 (仅对启动、重启、状态变更类指令生效)
    if not is_inspecting and any(k in obs_lower for k in [
        "failed to start", "failed to execute", "control process exited with error code",
        "job for", "fatal:", "fatal error"
    ]):
        return "服务启动或命令执行失败 (Execution failed)"

    return None


def normalize_observation_payload(obs: str) -> str:
    """归一化物理回显快照，剔除 ANSI 控制码、时间戳与动态易碎字段，供进展指纹比对"""
    if not obs:
        return ""
    clean = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", obs)
    # 剔除 ISO 时间戳与常见 Linux 日志时间格式
    clean = re.sub(r"\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?", "", clean)
    clean = re.sub(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+\s+\d{2}:\d{2}:\d{2}\b", "", clean)
    # 剔除 uptime 动态变化的运行时长与 load average
    clean = re.sub(r"up\s+[\d:,\s\w]+load average[s]?:\s*[\d.,\s]+", "load average", clean)
    # 剔除连续空白
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean
