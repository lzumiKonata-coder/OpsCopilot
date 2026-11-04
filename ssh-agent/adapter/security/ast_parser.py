"""适配层 - bashlex AST 解析辅助（包装命令解包与节点遍历）"""

import re
from typing import List, Optional, Tuple

from bashlex import ast

WRAPPER_BINARIES = {
    "sudo", "doas", "su", "runuser",
    "command", "builtin",
    "nohup", "nice", "ionice", "time", "exec",
    "env", "timeout", "xargs",
    "chroot", "watch", "setsid", "flock",
    "busybox", "stdbuf", "unshare", "nsenter",
    "systemd-run", "pkexec", "fakeroot"
}


def _unwrap_wrapper_command(words: List[str]) -> Tuple[Optional[List[str]], bool]:
    """
    解包包装类前缀命令。
    返回 (unwrapped_words, is_wrapper):
      - 若为包装器且提取出目标命令，返回 (words, True)
      - 若为包装器但未能提取出命令或无明确目标，返回 (None, True)
      - 若非包装器，返回 (None, False)
    """
    if not words:
        return None, False
    cmd_name = words[0].lower().split("/")[-1]
    if cmd_name not in WRAPPER_BINARIES:
        return None, False

    args = words[1:]

    if cmd_name in ("command", "builtin"):
        i = 0
        while i < len(args) and args[i].startswith("-"):
            i += 1
        return (args[i:] if i < len(args) else None), True

    elif cmd_name == "busybox":
        i = 0
        while i < len(args) and args[i].startswith("-"):
            i += 1
        return (args[i:] if i < len(args) else None), True

    elif cmd_name == "ionice":
        i = 0
        opts_with_arg = {"-c", "-n", "-p", "--class", "--classdata", "--pid"}
        while i < len(args):
            arg = args[i]
            if any(arg.startswith(f"{opt}=") for opt in ("--class", "--classdata", "--pid")):
                i += 1
            elif arg in opts_with_arg:
                i += 2
            elif arg.startswith(("-c", "-n", "-p")) and len(arg) > 2 and not arg.startswith("--"):
                i += 1
            elif arg.startswith("-"):
                i += 1
            else:
                break
        return (args[i:] if i < len(args) else None), True

    elif cmd_name in ("su", "runuser"):
        for idx, a in enumerate(args):
            if a in ("-c", "--command") and idx + 1 < len(args):
                return ["__SHELL_PAYLOAD__", args[idx + 1]], True
            elif a.startswith("-c") and len(a) > 2 and not a.startswith("--"):
                return ["__SHELL_PAYLOAD__", a[2:]], True
            elif a.startswith("--command="):
                return ["__SHELL_PAYLOAD__", a.split("=", 1)[1]], True
        return None, True

    elif cmd_name in ("sudo", "doas", "pkexec"):
        opts_with_arg = {
            "-u", "-g", "-h", "-p", "-C", "-U", "-D", "-r", "-t",
            "--user", "--group", "--host", "--prompt", "--close-from",
            "--other-user", "--role", "--type"
        }
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--":
                i += 1
                break
            elif any(arg.startswith(f"{opt}=") for opt in opts_with_arg if opt.startswith("--")):
                i += 1
            elif arg in opts_with_arg:
                i += 2
            elif arg.startswith("-"):
                i += 1
            else:
                break
        return (args[i:] if i < len(args) else None), True

    elif cmd_name == "env":
        opts_with_arg = {"-u", "-C", "-S", "--unset", "--chdir", "--split-string"}
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--":
                i += 1
                break
            elif any(arg.startswith(f"{opt}=") for opt in opts_with_arg if opt.startswith("--")):
                i += 1
            elif arg in opts_with_arg:
                i += 2
            elif "=" in arg and not arg.startswith("="):
                i += 1
            elif arg.startswith("-"):
                i += 1
            else:
                break
        return (args[i:] if i < len(args) else None), True

    elif cmd_name == "timeout":
        opts_with_arg = {"-s", "-k", "--signal", "--kill-after"}
        i = 0
        while i < len(args):
            arg = args[i]
            if any(arg.startswith(f"{opt}=") for opt in opts_with_arg if opt.startswith("--")):
                i += 1
            elif arg in opts_with_arg:
                i += 2
            elif arg.startswith("-"):
                i += 1
            else:
                i += 1  # 跳过时长参数
                break
        return (args[i:] if i < len(args) else None), True

    elif cmd_name == "xargs":
        opts_with_arg = {
            "-n", "-I", "-s", "-P", "-d", "-E", "-L", "-a",
            "--max-args", "--max-lines", "--max-chars", "--max-procs",
            "--delimiter", "--eof", "--arg-file"
        }
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--":
                i += 1
                break
            elif any(arg.startswith(f"{opt}=") for opt in opts_with_arg if opt.startswith("--")):
                i += 1
            elif arg in opts_with_arg:
                i += 2
            elif arg.startswith("-"):
                i += 1
            else:
                break
        return (args[i:] if i < len(args) else None), True

    elif cmd_name == "nice":
        i = 0
        while i < len(args):
            arg = args[i]
            if arg in ("-n", "--adjustment"):
                i += 2
            elif arg.startswith(("-n", "--adjustment=")):
                i += 1
            elif re.match(r"^-[0-9]+$", arg):
                i += 1
            elif arg.startswith("-"):
                i += 1
            else:
                break
        return (args[i:] if i < len(args) else None), True

    elif cmd_name == "watch":
        opts_with_arg = {"-n", "-d", "-t", "--interval", "--differences"}
        i = 0
        while i < len(args):
            arg = args[i]
            if any(arg.startswith(f"{opt}=") for opt in ("--interval", "--differences")):
                i += 1
            elif arg in opts_with_arg:
                i += 2
            elif arg.startswith("-n") and len(arg) > 2 and not arg.startswith("--"):
                i += 1
            elif arg.startswith("-"):
                i += 1
            else:
                break
        return (args[i:] if i < len(args) else None), True

    elif cmd_name == "setsid":
        i = 0
        while i < len(args) and args[i].startswith("-"):
            i += 1
        return (args[i:] if i < len(args) else None), True

    elif cmd_name == "flock":
        for idx, a in enumerate(args):
            if a in ("-c", "--command") and idx + 1 < len(args):
                return ["__SHELL_PAYLOAD__", args[idx + 1]], True
        i = 0
        while i < len(args) and args[i].startswith("-"):
            if args[i] in ("-w", "-E", "--timeout", "--conflict-exit-code"):
                i += 2
            else:
                i += 1
        if i < len(args):
            i += 1  # 跳过锁目标
        return (args[i:] if i < len(args) else None), True

    elif cmd_name == "chroot":
        i = 0
        while i < len(args):
            arg = args[i]
            if arg in ("--userspec", "--groups"):
                i += 2
            elif arg.startswith("-"):
                i += 1
            else:
                i += 1  # 跳过 newroot 路径
                break
        return (args[i:] if i < len(args) else None), True

    elif cmd_name in ("nohup", "time", "exec", "stdbuf", "fakeroot"):
        i = 0
        while i < len(args) and args[i].startswith("-"):
            i += 1
        return (args[i:] if i < len(args) else None), True

    return None, True


class _CommandASTVisitor(ast.nodevisitor):
    """
    bashlex AST 遍历访问者
    递归收集命令树中所有独立的 Command 节点并展开其词元 (Words)。
    支持深度遍历 pipeline (|), list (&&, ||, ;), compound, subshell 等复杂结构。
    """

    def __init__(self):
        self.commands: List[List[str]] = []
        self.pipeline_chains: List[List[List[str]]] = []

    def visitpipeline(self, n, parts):
        chain = []
        for part in parts:
            if getattr(part, "kind", None) == "command":
                w = []
                for p in getattr(part, "parts", []):
                    if getattr(p, "kind", None) == "word":
                        w.append(p.word)
                if w:
                    chain.append(w)
        if len(chain) >= 2:
            self.pipeline_chains.append(chain)

    def visitcommand(self, n, parts):
        words: List[str] = []
        for part in parts:
            if getattr(part, "kind", None) == "word":
                words.append(part.word)
            elif getattr(part, "kind", None) == "redirect":
                out = getattr(part, "output", None)
                if out and hasattr(out, "word"):
                    words.append(f">{out.word}")
            elif hasattr(part, "word"):
                words.append(part.word)
        if words:
            self.commands.append(words)
