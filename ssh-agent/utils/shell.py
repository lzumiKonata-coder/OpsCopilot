"""Shell 命令解析与清洗通用工具"""

import re
from typing import List, Optional


def detect_os_family(terminal_buffer: Optional[str], host_name: Optional[str] = None) -> str:
    """根据终端快照中的命令提示符 (Prompt)、主机资产名称或回显文本，推断 Linux 发行版族系"""
    clean = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", terminal_buffer) if terminal_buffer else ""

    # 1. 匹配 RHEL/CentOS/Rocky/AlmaLinux/openEuler/Kylin 提示符：[user@host path]# 或 [user@host path]$
    if clean and re.search(r"\[[a-zA-Z0-9_\.-]+@[a-zA-Z0-9_\.-]+\s+[^\]]+\][#$]", clean):
        return "CentOS / RHEL / Rocky / openEuler (RPM 体系，包管理器为 yum/dnf，提示符为 [user@host]#)"

    # 2. 匹配 Ubuntu/Debian 提示符：user@host:path# 或 user@host:path$
    if clean and re.search(r"[a-zA-Z0-9_\.-]+@[a-zA-Z0-9_\.-]+:[^#$]+[#$]", clean):
        return "Ubuntu / Debian (DEB 体系，包管理器为 apt/apt-get)"

    # 3. 关键字与主机名启发式匹配（例如 hadoop100、centos-01 等集群节点）
    check_str = (clean + " " + (host_name or "")).lower()
    if any(k in check_str for k in ["hadoop", "centos", "red hat", "redhat", "rocky", "almalinux", "euler", "kylin", "fedora", "rhel"]):
        return "CentOS / RHEL / Rocky / openEuler (RPM 体系，包管理器为 yum/dnf)"
    if any(k in check_str for k in ["ubuntu", "debian", "mint"]):
        return "Ubuntu / Debian (DEB 体系，包管理器为 apt/apt-get)"
    if any(k in check_str for k in ["arch", "manjaro"]):
        return "Arch Linux (Pacman 体系)"
    if any(k in check_str for k in ["alpine"]):
        return "Alpine Linux (apk 体系)"

    return "Linux (通用环境)"


def is_valid_shell_command(cmd: str) -> bool:
    """
    严格校验提取出的命令行是否为合法的 Linux Shell 指令。
    杜绝大模型将纯中文描述（如'无需执行安装命令'、'跳过'、'已安装最新版本'）误作为 Action 注入终端。
    """
    if not cmd or not cmd.strip():
        return False
    clean = cmd.strip()

    # 1. 如果第一个字符是中文字符，直接判定为非法自然语言说明
    if '一' <= clean[0] <= '鿿':
        return False

    # 2. 提取首个可执行 token（跳过环境变量赋值 VAR=xxx 与前缀包装如 sudo/nohup）
    tokens = clean.split()
    while tokens and "=" in tokens[0] and not tokens[0].startswith("="):
        tokens = tokens[1:]
    while tokens and tokens[0].lower() in {"sudo", "nohup", "time", "exec", "command", "builtin", "sh", "bash"}:
        tokens = tokens[1:]
    if not tokens:
        return False

    bin_name = tokens[0].rsplit("/", 1)[-1].strip()
    # 3. 可执行文件名必须符合 Linux 命名规范（ASCII 字母、数字、点、下划线、加减号等），绝不可含中文
    if re.search(r"[一-龥]", bin_name):
        return False
    if not re.match(r"^[a-zA-Z0-9_\.\+-]+$", bin_name):
        return False

    return True


def strip_trailing_comment_outside_quotes(cmd: str) -> str:
    """
    仅在引号外部剥离大模型在命令末尾附带的中文说明、行内注释（#）、二选一描述（或...）等，
    绝不破坏引号内部合法的参数字符串（如 echo "进程仍在运行，请等待..."）
    """
    in_single = False
    in_double = False
    escaped = False
    cut_idx = len(cmd)

    i = 0
    while i < len(cmd):
        char = cmd[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if char == "\\":
            escaped = True
            i += 1
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue

        # 仅在引号外部匹配截断特征
        if not in_single and not in_double:
            # 1. 行末注释 #（排除 $# 变量）
            if char == "#" and (i == 0 or cmd[i - 1] in " \t;" and (i < 2 or cmd[i - 2] != "$")):
                cut_idx = min(cut_idx, i)
                break

            # 2. 中文全角括号 （...）
            if char == "（":
                cut_idx = min(cut_idx, i)
                break

            # 3. 英文括号 (...) 且非 $() 或 ()
            if char == "(":
                if not (i > 0 and cmd[i - 1] == "$") and not (i + 1 < len(cmd) and cmd[i + 1] == ")"):
                    cut_idx = min(cut_idx, i)
                    break

            # 4. 逗号后紧随中文字符（模型在命令后追加的中文句子）
            m = re.match(r"^[，,]\s*[\u4e00-\u9fa5]", cmd[i:])
            if m:
                cut_idx = min(cut_idx, i)
                break

            # 5. 或 / 或者 / or 后跟命令
            m = re.match(r"^[\s,，;；]+(?:或|或者|or)\s+", cmd[i:])
            if m:
                cut_idx = min(cut_idx, i)
                break

        i += 1

    return cmd[:cut_idx].strip()


def join_shell_lines(lines: List[str]) -> str:
    """将多行 Shell 脚本合并为可直接在 bash 中单行执行的复合语句"""
    if len(lines) <= 1:
        return lines[0] if lines else ""
    parts: List[str] = []
    for i, line in enumerate(lines):
        l = line.strip()
        if not l:
            continue
        parts.append(l)
        if i < len(lines) - 1:
            if not l.endswith((";", "&", "|", "then", "do", "else", "elif", "{", "(")):
                parts.append(";")
    joined = " ".join(parts)
    joined = re.sub(r"\s*;\s*;+", ";", joined)
    joined = re.sub(r";\s*(then|do|else|elif|fi|done)\b", r"; \1", joined)
    return joined


def clean_extracted_command(raw_action: str) -> str:
    """
    清洗大模型 Action 字段生成的命令行，彻底剥离反引号、markdown 标签与解释性文字，
    确保其为可在 bash 中安全直接执行的纯净单行命令
    """
    if not raw_action:
        return ""
    # 1. 过滤 markdown 语法标记
    text = re.sub(r"^```(?:bash|sh)?\s*", "", raw_action.strip())
    text = re.sub(r"\s*```$", "", text).strip()

    # 2. 如果混入了模板格式词（如 【格式 A... 或 Thought: 等），截断掉
    text = re.split(r"【|Thought:|Step_Title:|Final_Answer:|Final Answer:", text)[0].strip()

    # 3. 按行分割，优先取非注释、非空行
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
    if not lines:
        return ""

    first_token = lines[0].split()[0] if lines[0].split() else ""
    if len(lines) > 1 and first_token in {"if", "for", "while", "until"}:
        cmd = join_shell_lines(lines)
    else:
        cmd = lines[0]

    # 4. 仅当整条命令首尾成对包裹引号时才剔除外层包裹引号（严禁直接 strip 导致参数末尾正常闭合引号被误删引发语法截断）
    cmd = cmd.replace("`", "").strip()
    if (cmd.startswith('"') and cmd.endswith('"')) or (cmd.startswith("'") and cmd.endswith("'")):
        cmd = cmd[1:-1].strip()

    # 5. 仅在引号外部剥离模型附带的中文注释、二选一描述（或...）与行末注释，严禁截断引号内的合法参数
    cmd = strip_trailing_comment_outside_quotes(cmd)

    # 6. 严格校验是否为合法的可执行 Linux 指令，严禁中文自然语言穿透至 bash 终端
    if not is_valid_shell_command(cmd):
        return ""

    return cmd
