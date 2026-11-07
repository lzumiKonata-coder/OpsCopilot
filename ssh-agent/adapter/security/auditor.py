"""适配层 - bashlex AST 命令安全审查器核心实现"""

import logging
import re

import bashlex
from bashlex.errors import ParsingError

from adapter.security.ast_parser import _CommandASTVisitor, _unwrap_wrapper_command
from adapter.security.payload_auditor import _audit_python_payload
from application.ports import ICommandAuditor
from domain.entities import AuditDecision, Command
from domain.rules import (
    FORBIDDEN_COMMAND_NAMES,
    FORBIDDEN_DESTRUCTIVE_PATHS,
    HIGH_RISK_COMMAND_NAMES,
    RiskLevel,
    SENSITIVE_FILES,
)

logger = logging.getLogger("smartops.security")


class BashlexCommandAuditor(ICommandAuditor):
    """
    基于 bashlex AST 抽象语法树的企业级安全审查实现
    """

    # 正则兜底规则（当 AST 解析异常时实施防御性降级扫描）
    FALLBACK_FORBIDDEN_REGEX = re.compile(
        r"(rm\s+-(?:r[fF]|[fF]r|R)\s+[/~*])|"
        r"(:()\s*{\s*:\|:&\s*};\s*:)|"
        r"(mkfs(?:\.\w+)?\s+/dev/)|"
        r"(dd\s+if=.*of=/dev/)|"
        r"(>\s*/dev/sd[a-z])",
        re.IGNORECASE,
    )

    FALLBACK_HIGH_RISK_REGEX = re.compile(
        r"\b(shutdown|reboot|poweroff|init\s+0|kill\s+-9|pkill|iptables\s+-F|ufw\s+disable|rm)\b|"
        r"(curl.*\|\s*(?:bash|sh))|"
        r"(wget.*\|\s*(?:bash|sh))|"
        r"(/etc/shadow|/etc/sudoers)",
        re.IGNORECASE,
    )

    async def audit(self, command: Command) -> AuditDecision:
        raw_cmd = command.raw_command.strip()
        hit_rules: list[str] = []
        forbidden_reasons: list[str] = []
        high_risk_reasons: list[str] = []

        try:
            # 1. 解析抽象语法树 (AST)
            trees = bashlex.parse(raw_cmd)
            visitor = _CommandASTVisitor()
            for tree in trees:
                visitor.visit(tree)

            cmd_queue = list(visitor.commands)
            processed_cmds: list[list[str]] = []

            # 检查 pipeline 链条是否输入解释器
            for pipe_chain in visitor.pipeline_chains:
                for i in range(len(pipe_chain) - 1):
                    sender = pipe_chain[i]
                    receiver = pipe_chain[i + 1]
                    recv_name = receiver[0].lower().split("/")[-1]
                    if recv_name in ("bash", "sh", "zsh", "dash", "ash", "ksh", "python", "python3", "perl", "ruby", "node", "php"):
                        sender_name = sender[0].lower().split("/")[-1]
                        if sender_name in ("echo", "printf"):
                            p_words = [w for w in sender[1:] if not w.startswith("-")]
                            p_str = " ".join(p_words).strip()
                            if (p_str.startswith('"') and p_str.endswith('"')) or (p_str.startswith("'") and p_str.endswith("'")):
                                p_str = p_str[1:-1]
                            try:
                                sub_trees = bashlex.parse(p_str)
                                sub_vis = _CommandASTVisitor()
                                for st in sub_trees:
                                    sub_vis.visit(st)
                                cmd_queue.extend(sub_vis.commands)
                            except Exception:
                                hit_rules.append("PIPE_TO_SHELL")
                                high_risk_reasons.append("管道向 Shell 解释器输入数据流执行")
                        else:
                            hit_rules.append("PIPE_TO_SHELL")
                            high_risk_reasons.append(f"管道输出重定向至解释器 '{recv_name}'")

            # 2. 递归解包包装命令与解释器 -c 载荷
            while cmd_queue:
                cmd_parts = cmd_queue.pop(0)
                if not cmd_parts:
                    continue
                processed_cmds.append(cmd_parts)

                cmd_name = cmd_parts[0].lower().split("/")[-1]
                args = cmd_parts[1:]

                # 2.1 解包 sudo / nohup / env / timeout / xargs 等前缀包装命令
                unwrapped, is_wrapper = _unwrap_wrapper_command(cmd_parts)
                if is_wrapper:
                    if unwrapped:
                        if unwrapped[0] == "__SHELL_PAYLOAD__":
                            payload = unwrapped[1]
                            try:
                                sub_trees = bashlex.parse(payload)
                                sub_vis = _CommandASTVisitor()
                                for st in sub_trees:
                                    sub_vis.visit(st)
                                cmd_queue.extend(sub_vis.commands)
                            except Exception:
                                hit_rules.append("WRAPPER_SHELL_PAYLOAD_PARSE_FAIL")
                                high_risk_reasons.append(f"包装器 Shell 载荷解析失败: {payload[:30]}")
                        elif unwrapped[0].startswith("-"):
                            hit_rules.append("WRAPPER_UNWRAP_FAILED")
                            high_risk_reasons.append(f"包装器 '{cmd_name}' 参数解析异常，降级为高危强制人工复核")
                        else:
                            cmd_queue.append(unwrapped)
                    else:
                        hit_rules.append("WRAPPER_NO_COMMAND")
                        high_risk_reasons.append(f"包装器 '{cmd_name}' 未指定目标指令，降级为高危强制人工复核")

                # 2.2 eval 递归解析
                if cmd_name == "eval":
                    payload = " ".join(args).strip()
                    if (payload.startswith('"') and payload.endswith('"')) or (payload.startswith("'") and payload.endswith("'")):
                        payload = payload[1:-1]
                    if payload:
                        try:
                            sub_trees = bashlex.parse(payload)
                            sub_vis = _CommandASTVisitor()
                            for st in sub_trees:
                                sub_vis.visit(st)
                            if sub_vis.commands:
                                cmd_queue.extend(sub_vis.commands)
                            else:
                                hit_rules.append("EVAL_EMPTY")
                                high_risk_reasons.append("eval 命令参数为空")
                        except Exception:
                            hit_rules.append("EVAL_DYNAMIC_PAYLOAD")
                            high_risk_reasons.append("eval 载荷无法静态解析，降级为高危人工复核")
                    else:
                        hit_rules.append("EVAL_EMPTY")
                        high_risk_reasons.append("eval 缺少参数")

                # 2.3 find -exec 提取
                if cmd_name == "find":
                    if "-exec" in args or "-execdir" in args:
                        exec_idx = args.index("-exec") if "-exec" in args else args.index("-execdir")
                        exec_cmd = []
                        for a in args[exec_idx + 1:]:
                            if a in (";", r"\;", "+"):
                                break
                            exec_cmd.append(a)
                        if exec_cmd:
                            cmd_queue.append(exec_cmd)
                        else:
                            hit_rules.append("FIND_EXEC_EMPTY")
                            high_risk_reasons.append("find -exec 未指定目标命令，降级为高危人工复核")
                    if "-delete" in args:
                        hit_rules.append("FIND_DELETE_FLAG")
                        high_risk_reasons.append("find 命令带有 -delete 批量删除参数，属于高危操作")

                # 2.4 awk system() 提取
                if cmd_name in ("awk", "gawk", "mawk", "nawk"):
                    combined_args = " ".join(args)
                    m = re.search(r"system\s*\(\s*(['\"])(.*?)\1\s*\)", combined_args)
                    if m:
                        sys_cmd = m.group(2).strip()
                        try:
                            sub_trees = bashlex.parse(sys_cmd)
                            sub_vis = _CommandASTVisitor()
                            for st in sub_trees:
                                sub_vis.visit(st)
                            cmd_queue.extend(sub_vis.commands)
                        except Exception:
                            hit_rules.append("AWK_SYSTEM_PARSE_FAIL")
                            high_risk_reasons.append(f"awk system() 载荷解析失败: {sys_cmd}")
                    elif re.search(r"system\s*\(", combined_args):
                        hit_rules.append("AWK_DYNAMIC_SYSTEM")
                        high_risk_reasons.append("awk 脚本中调用了动态 system() 执行命令，降级为高危人工复核")

                # 2.5 source / . 脚本执行
                if cmd_name in ("source", "."):
                    hit_rules.append("SOURCE_SCRIPT_EXECUTION")
                    high_risk_reasons.append(f"使用 '{cmd_name}' 载入执行外部脚本，可能运行非受信代码，降级为高危人工复核")

                # 2.6 递归解包 Shell 解释器 -c 载荷
                if cmd_name in ("bash", "sh", "zsh", "dash", "ash", "ksh"):
                    for idx, a in enumerate(args):
                        if a == "-c" or (a.startswith("-") and "c" in a and not a.startswith("--")):
                            if idx + 1 < len(args):
                                sub_payload = args[idx + 1]
                                try:
                                    sub_trees = bashlex.parse(sub_payload)
                                    sub_visitor = _CommandASTVisitor()
                                    for st in sub_trees:
                                        sub_visitor.visit(st)
                                    cmd_queue.extend(sub_visitor.commands)
                                except Exception as e:
                                    logger.warning("Failed to parse subshell payload '%s': %s", sub_payload, e)
                                    sub_decision = self._fallback_regex_audit(sub_payload)
                                    if sub_decision.risk_level == RiskLevel.FORBIDDEN:
                                        hit_rules.extend(sub_decision.hit_rules)
                                        forbidden_reasons.append(sub_decision.reason)
                                    elif sub_decision.risk_level == RiskLevel.HIGH_RISK:
                                        hit_rules.extend(sub_decision.hit_rules)
                                        high_risk_reasons.append(sub_decision.reason)

                # 2.7 递归审计 Python 解释器 -c 载荷
                if cmd_name.startswith("python") or cmd_name.startswith("pypy"):
                    for idx, a in enumerate(args):
                        if a == "-c" or (a.startswith("-") and "c" in a and not a.startswith("--")):
                            if idx + 1 < len(args):
                                py_payload = args[idx + 1]
                                p_rules, p_forbid, p_high = _audit_python_payload(py_payload)
                                hit_rules.extend(p_rules)
                                forbidden_reasons.extend(p_forbid)
                                high_risk_reasons.extend(p_high)

                # 2.8 递归审计 Perl 解释器 -e 载荷
                if cmd_name == "perl":
                    for idx, a in enumerate(args):
                        if a in ("-e", "-E") or (a.startswith("-") and ("e" in a or "E" in a)):
                            if idx + 1 < len(args):
                                perl_payload = args[idx + 1]
                                if re.search(r"\b(rmdir|unlink|system|exec)\b", perl_payload):
                                    hit_rules.append("PERL_HIGH_RISK_OP")
                                    high_risk_reasons.append(f"Perl payload contains high risk command execution: {perl_payload[:40]}")

                # 2.9 递归审计 Node.js -e / --eval 载荷
                if cmd_name in ("node", "nodejs"):
                    for idx, a in enumerate(args):
                        if a in ("-e", "--eval") and idx + 1 < len(args):
                            node_payload = args[idx + 1]
                            if re.search(r"(?:rmSync|rm|rmdirSync|rmdir|unlinkSync|unlink)\s*\(\s*['\"](?:/|/\*)['\"]", node_payload):
                                hit_rules.append("NODE_FORBIDDEN_DELETION")
                                forbidden_reasons.append("Node.js payload attempts destructive root deletion.")
                            else:
                                hit_rules.append("NODE_EVAL")
                                high_risk_reasons.append("Node.js inline eval execution.")

                # 2.10 递归审计 Ruby -e 载荷
                if cmd_name == "ruby":
                    for idx, a in enumerate(args):
                        if a == "-e" and idx + 1 < len(args):
                            ruby_payload = args[idx + 1]
                            if re.search(r"(?:rm_rf|rmdir|unlink|delete)\s*\(?\s*['\"](?:/|/\*)['\"]", ruby_payload):
                                hit_rules.append("RUBY_FORBIDDEN_DELETION")
                                forbidden_reasons.append("Ruby payload attempts destructive root deletion.")
                            else:
                                hit_rules.append("RUBY_EVAL")
                                high_risk_reasons.append("Ruby inline eval execution.")

                # 2.11 递归审计 PHP -r 载荷
                if cmd_name == "php":
                    for idx, a in enumerate(args):
                        if a == "-r" and idx + 1 < len(args):
                            php_payload = args[idx + 1]
                            m_sys = re.search(r"system\s*\(\s*['\"](.*?)['\"]\s*\)", php_payload)
                            if m_sys:
                                sys_cmd = m_sys.group(1).strip()
                                try:
                                    sub_trees = bashlex.parse(sys_cmd)
                                    sub_vis = _CommandASTVisitor()
                                    for st in sub_trees:
                                        sub_vis.visit(st)
                                    cmd_queue.extend(sub_vis.commands)
                                except Exception:
                                    hit_rules.append("PHP_SYSTEM_PARSE_FAIL")
                                    high_risk_reasons.append(f"PHP system() command parse fail: {sys_cmd}")
                            elif re.search(r"(?:unlink|rmdir)\s*\(\s*['\"](?:/|/\*)['\"]", php_payload):
                                hit_rules.append("PHP_FORBIDDEN_DELETION")
                                forbidden_reasons.append("PHP payload attempts destructive root deletion.")
                            else:
                                hit_rules.append("PHP_RUN")
                                high_risk_reasons.append("PHP inline code execution.")

            # 3. 遍历每一个提炼出的原子命令及其参数进行规则裁定
            for cmd_parts in processed_cmds:
                if not cmd_parts:
                    continue

                cmd_name = cmd_parts[0].lower().split("/")[-1]  # 获取可执行文件名，如 /bin/rm -> rm
                args = cmd_parts[1:]

                # 规则 A: 绝对禁止命令 (FORBIDDEN)
                if cmd_name in FORBIDDEN_COMMAND_NAMES:
                    hit_rules.append(f"FORBIDDEN_COMMAND:{cmd_name}")
                    forbidden_reasons.append(f"Destructive disk command '{cmd_name}' is strictly prohibited.")

                # 规则 B: rm 极端破坏性递归删除 (FORBIDDEN)
                if cmd_name == "rm":
                    is_recursive = any(
                        arg in ("-r", "-rf", "-fr", "-R", "--recursive") or
                        (arg.startswith("-") and ("r" in arg or "R" in arg))
                        for arg in args
                    )
                    has_dangerous_target = any(
                        arg in FORBIDDEN_DESTRUCTIVE_PATHS or
                        arg.rstrip("/") in ("/", "", "*")
                        for arg in args if not arg.startswith("-")
                    )
                    if is_recursive and has_dangerous_target:
                        hit_rules.append("FORBIDDEN_RM_ROOT")
                        forbidden_reasons.append("Catastrophic 'rm -rf' on critical root or system path is strictly blocked.")
                    elif has_dangerous_target:
                        hit_rules.append("HIGH_RISK_RM_SYSTEM_PATH")
                        high_risk_reasons.append("Executing 'rm' directly against critical system or root path is strictly restricted.")

                # 规则 C: 破坏性重定向到裸块设备 (FORBIDDEN)
                for part in cmd_parts:
                    if part.startswith(">") and any(dev in part for dev in ("/dev/sd", "/dev/nvme", "/dev/vd")):
                        hit_rules.append("FORBIDDEN_RAW_DEVICE_OVERWRITE")
                        forbidden_reasons.append(f"Direct raw disk redirection write '{part}' is strictly blocked.")

                # 规则 D: 高危命令 (HIGH_RISK)
                if cmd_name in HIGH_RISK_COMMAND_NAMES:
                    hit_rules.append(f"HIGH_RISK_COMMAND:{cmd_name}")
                    high_risk_reasons.append(f"Command '{cmd_name}' has broad operational impact and requires escalation.")

                # 规则 E: 敏感凭据/特权文件探查 (HIGH_RISK)
                for arg in args:
                    for sensitive in SENSITIVE_FILES:
                        if sensitive in arg:
                            hit_rules.append(f"SENSITIVE_FILE_ACCESS:{sensitive}")
                            high_risk_reasons.append(f"Accessing sensitive system credential '{sensitive}' is restricted.")

                # 规则 F: 危险网络管道执行 (HIGH_RISK: curl | bash / wget | sh)
                if cmd_name in ("curl", "wget"):
                    if any(other[0] in ("bash", "sh") for other in processed_cmds if other):
                        hit_rules.append("REMOTE_SCRIPT_PIPE_EXECUTION")
                        high_risk_reasons.append("Piping untrusted remote content into shell interpreter is high risk.")

        except (ParsingError, NotImplementedError, Exception) as parse_err:
            # 防御式兜底：当 AST 无法解析时采用启发式正则规则，确保安全无死角
            logger.warning(
                "bashlex failed to parse AST for command '%s', falling back to regex: %s",
                raw_cmd,
                str(parse_err),
            )
            return self._fallback_regex_audit(raw_cmd)

        # 3. 汇总裁定
        if forbidden_reasons:
            return AuditDecision(
                risk_level=RiskLevel.FORBIDDEN,
                hit_rules=list(set(hit_rules)),
                reason="; ".join(forbidden_reasons),
            )
        elif high_risk_reasons:
            return AuditDecision(
                risk_level=RiskLevel.HIGH_RISK,
                hit_rules=list(set(hit_rules)),
                reason="; ".join(high_risk_reasons),
            )

        return AuditDecision(
            risk_level=RiskLevel.SAFE,
            hit_rules=[],
            reason="Command AST passed all safety checks.",
        )

    def _fallback_regex_audit(self, raw_cmd: str) -> AuditDecision:
        """
        兜底审查：AST 解析失败意味着语法级审查能力已失效，绝不能默认放行。
        命中毁灭性特征时直接硬拦截；其余一律升级为强制人工复核。
        """
        if self.FALLBACK_FORBIDDEN_REGEX.search(raw_cmd):
            return AuditDecision(
                risk_level=RiskLevel.FORBIDDEN,
                hit_rules=["FALLBACK_REGEX_FORBIDDEN", "AST_PARSE_FAILED"],
                reason="AST 解析失败且命令命中毁灭性特征，已硬性拦截。",
            )

        matched_high_risk = bool(self.FALLBACK_HIGH_RISK_REGEX.search(raw_cmd))
        hit_rules = ["AST_PARSE_FAILED"]
        if matched_high_risk:
            hit_rules.append("FALLBACK_REGEX_HIGH_RISK")
        return AuditDecision(
            risk_level=RiskLevel.HIGH_RISK,
            hit_rules=hit_rules,
            reason=(
                "AST 语法树解析失败，无法完成语法级安全审查"
                + ("，且命中高危操作特征" if matched_high_risk else "")
                + "，已升级为强制人工复核。"
            ),
            requires_manual_review=True,
        )
