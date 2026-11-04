"""应用层 - ReAct 引擎回显研判与根用户探测"""

import re
from typing import Optional

from application.react.observation import detect_interactive_prompt, is_empty_observation


class ObservationAnalysisMixin:
    """对每步回显进行物理状态研判"""

    @staticmethod
    def _extract_command_name(command: str) -> str:
        tokens = (command or "").strip().split()
        while tokens and tokens[0] in {"sudo", "nohup", "command", "exec"}:
            tokens = tokens[1:]
        if not tokens:
            return ""
        return tokens[0].rsplit("/", 1)[-1].lower()

    @classmethod
    async def analyze_observation(cls, command: str, observation: str) -> str:
        """对每一步回显进行物理状态研判：基于程序名与真实回显做事实性归纳"""
        cmd_clean = (command or "").strip().lower()
        if cmd_clean in {"y", "yes", "n", "no"}:
            return f"已向终端发送交互确认 '{command}'，交互式指令已得到响应推进。"

        # 优先检测终端是否正在等待 y/n 等交互确认输入
        interactive_prompt = detect_interactive_prompt(observation)
        if interactive_prompt:
            return f"[提示] 终端正在等待交互式确认输入（检测到提示: '{interactive_prompt}'）！请在下一步 Action 中直接下发 'y' 确认（或 'n' 取消），切勿下发新命令。"

        obs_lower = (observation or "").lower()
        empty_output = is_empty_observation(command, observation)

        if any(err in obs_lower for err in ["command not found", "未找到命令"]):
            return "[警告] 回显报错：命令未找到（command not found），目标机器可能未安装该工具包。"
        if any(err in obs_lower for err in ["permission denied", "权限不够"]):
            return "[警告] 回显报错：操作权限不足（Permission denied），该指令需要 sudo 提权。"

        cmd_name = cls._extract_command_name(command)
        factual_map = {
            "ls": "【目录/文件列表已成功获取】：终端已返回目录中的文件与目录清单。若用户仅需查看文件或目录，当前目标已完全达成，请直接输出 Final_Answer 汇总展示文件列表并结束任务。",
            "dir": "【目录/文件列表已成功获取】：终端已返回目录中的文件与目录清单。若用户仅需查看文件或目录，当前目标已完全达成，请直接输出 Final_Answer 汇总展示文件列表并结束任务。",
            "free": "【内存指标采集】：已捕获物理内存与 Swap 水位快照，具体水位需结合回显数值研判。",
            "uptime": "【系统负载采集】：已捕获系统平均负载与运行时长，具体负载需结合回显数值研判。",
            "ss": "【网络连接采集】：已捕获活跃套接字与监听端口列表，连接状态需结合回显研判。",
            "netstat": "【网络连接采集】：已捕获活跃套接字与监听端口列表，连接状态需结合回显研判。",
            "df": "【磁盘容量采集】：已捕获文件系统挂载点与容量利用率快照，具体利用率需结合回显数值研判。",
            "du": "【磁盘占用采集】：已捕获目标路径磁盘占用统计，具体占用需结合回显数值研判。",
            "ps": "【进程状态采集】：已检索主机进程列表，进程状态需结合回显研判。",
            "top": "【资源排行采集】：已捕获进程资源消耗排行快照，具体消耗需结合回显数值研判。",
        }

        if empty_output:
            if "grep" in command or "find" in command or "which" in command:
                return f"指令 `{command}` 已执行完毕，未检索到匹配结果（回显为空）。针对删除或清理验证，无回显即表明目标已成功消除，任务达成，请输出 Final_Answer 正常退出。"
            elif cmd_name in {"rm", "mv", "kill", "chmod", "chown", "mkdir", "touch"}:
                return f"指令 `{command}` 已执行完成，终端无异常报错（符合 Linux '无报错即成功' 范式），变更已落地。"
            else:
                return f"指令 `{command}` 已执行完成。（注意：本步未捕获到任何标准输出回显，命令可能仍在执行或无匹配结果，不能据此判定成功）"

        # 包管理器执行特征归纳 (yum / dnf / apt / apt-get / rpm / dpkg)
        if cmd_name in {"yum", "dnf", "apt", "apt-get", "rpm", "dpkg"}:
            if any(k in obs_lower for k in [
                "已安装并且是最新版本", "无须任何处理", "无须处理",
                "already installed and latest version", "nothing to do",
                "is already installed"
            ]):
                return (
                    f"【软件包已就绪确认】：包管理器确认目标软件包已安装且处于最新版本（无需重复下发安装命令）。"
                    f"若目标软件已就绪，请直接输出 Final_Answer 宣布任务完成；"
                    f"若需要验证或启动，下一步请通过 `systemctl status <服务名>` 检查服务单元并尝试 `systemctl start <服务名>` 启动，"
                    f"或通过 `rpm -ql <包名> | grep bin` 确认二进制文件路径，切勿在终端下发纯中文说明或反复修改环境变量。"
                )
            if any(k in obs_lower for k in ["complete!", "完毕!", "installed:", "setting up"]):
                return f"【软件包安装成功】：包管理器已顺利安装目标组件。下一步请通过 `systemctl start <服务名>` 启动服务并验证运行状态。"

        if cmd_name in factual_map:
            analysis = factual_map[cmd_name]
        else:
            analysis = f"指令 `{command}` 已执行完成，已捕获终端物理输出，系统状态需结合回显内容研判。"
        return analysis

    @staticmethod
    def detect_root_from_output(text: Optional[str]) -> Optional[bool]:
        if not text or not text.strip():
            return None
        tail = text.strip()[-200:]
        if re.search(r"\][#$]\s*$", tail):
            return tail.rstrip().endswith("#")
        if re.search(r"[a-zA-Z0-9_\.-]+@[a-zA-Z0-9_\.-]+:\S*[#$]\s*$", tail):
            return tail.rstrip().endswith("#")
        return None

    @staticmethod
    def failure_advice(last_error_detail: Optional[str]) -> str:
        detail = (last_error_detail or "").split("(回显")[0]
        if "权限" in detail or "permission" in detail.lower():
            return "建议人工核查目标机登录账户、sudoers 配置（visudo）与 SELinux 策略，确认具备所需操作权限后重试。"
        if "安全阻断" in detail:
            return "指令被平台安全合规规则拦截，请人工评估该操作的必要性，或改用合规的替代命令。"
        if "命令未找到" in detail or "command not found" in detail.lower():
            return "建议人工确认目标机是否安装对应工具，并核对发行版命令体系（RHEL 系用 yum/dnf，Debian 系用 apt）。"
        if "服务单元不存在" in detail or "unit not found" in detail.lower():
            return "建议人工确认目标服务的准确名称与安装情况（如 docker 实际包名可能为 docker-ce）。"
        if "文件或目录不存在" in detail or "no such file" in detail.lower():
            return "建议人工核对目标文件/目录的准确路径与软件安装位置。"
        if "软件包" in detail or "package" in detail.lower() or "repository" in detail.lower():
            return "建议人工检查目标机网络连通性与本地 Yum/Apt 软件源配置后重试。"
        if "服务启动或命令执行失败" in detail or "execution failed" in detail.lower():
            return "建议人工通过 journalctl -u <服务名> 或相关服务日志核查具体失败原因。"
        return "建议人工结合上方各步骤回显复核执行现场，检查目标机网络、权限与服务状态。"
