"""
适配层 - 交互式 WebSSH PTY 活跃会话管理器 (Terminal Session Manager)
实现 application.ports.ISSHTerminalSessionManager。
使后端 ReActUseCase 能够直接与工程师当前打开的 Web 终端保持双工联动，
将指令精准敲入当前真实 Shell 会话中，并实时捕获回显用于闭环思考。
"""

import asyncio
from dataclasses import dataclass, field
import logging
import re
import time
from typing import Any, Callable

from application.ports import ISSHTerminalSessionManager

logger = logging.getLogger("smartops.terminal_manager")

# 正则剥除 VT100 / ANSI 控制字符
ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
# 除 \n 与 \t 外的其余 C0 控制字符与 DEL（BEL、BS 等）
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# 交互式确认提示符特征（y/n, [y/N], 是否...？等停顿等待 stdin 的特征）
INTERACTIVE_PROMPT_END_RE = re.compile(
    r"(?:\([a-zA-Z0-9/,\s]*[yY]/[nN][a-zA-Z0-9/,\s]*\)|\[[a-zA-Z0-9/,\s]*[yY]/[nN][a-zA-Z0-9/,\s]*\]|\[y/d/N\]|\b[yY]/[nN]\b|[?？])\s*$",
    re.IGNORECASE,
)

# Bash 二级提示符特征（未闭合引号或多行语法等待 PS2）
PS2_CONTINUATION_RE = re.compile(
    r"(?:^|\n)\s*(?:>|>>|dquote>|quote>|bquote>)\s*$",
    re.IGNORECASE,
)

# 终端就绪判定：Shell 提示符回归，或停在交互式提问等待输入
READY_AT_END_RE = re.compile(
    r"(?:[#$]|(?:\([a-zA-Z0-9/,\s]*[yY]/[nN][a-zA-Z0-9/,\s]*\)|\[[a-zA-Z0-9/,\s]*[yY]/[nN][a-zA-Z0-9/,\s]*\]|\[y/d/N\]|[yY]/[nN]|[?？]))\s*$",
    re.IGNORECASE,
)

# Shell 提示符整行特征：用于提取注入前提示符的字面指纹，供注入后精确回归匹配
SHELL_PROMPT_LINE_RE = re.compile(
    r"^(?:"
    r"(?:\([^)\n]{1,50}\)\s*)?\[[^\]\n]{1,200}\][#$%]"
    r"|(?:\([^)\n]{1,50}\)\s*)?[^\s@\n]{1,64}@[^\n]{0,200}[#$%]"
    r"|(?:bash|sh|zsh|dash|ksh|ash)(?:-[0-9.]+)?[#$%]"
    r"|[$#%]"
    r"|[}→❯➜λ][^\n]{0,200}"
    r")\s*$"
)

# 常见提示符结尾符号：用于识别未被 SHELL_PROMPT_LINE_RE 覆盖的自定义 PS1
UNKNOWN_PROMPT_TAIL_CHARS = "#$%>}→❯➜λ"

# 指纹失配后的宽限窗口：输出静默超过该时长后降级接受新提示符，避免空等到硬超时
PROMPT_MISMATCH_GRACE_SECONDS = 2.0

# 提示符结构签名：user@host + 权限符，忽略工作目录等易变内容
PROMPT_STRUCTURE_RE = re.compile(
    r"(?:\[|^)([^\s@\[\]]{1,64})@([^\s:\]]{1,64})(?::|\s|\])"
)
PROMPT_PRIVILEGE_RE = re.compile(r"([#$%])\s*$")


def _last_non_empty_line(buffer: str | None) -> str | None:
    """取缓冲区中最后一条非空行（去尾部空白）"""
    if not buffer:
        return None
    for line in reversed(buffer.splitlines()):
        if line.strip():
            return line.rstrip()
    return None


def _is_interactive_waiting(buffer: str | None) -> bool:
    """判断缓冲区末行是否停留在交互式确认提示（y/n、是否...？等）"""
    last_line = _last_non_empty_line(buffer)
    return bool(last_line and INTERACTIVE_PROMPT_END_RE.search(last_line))


def _is_ready_to_inject(buffer: str | None, allow_interactive: bool = False) -> bool:
    """判断终端是否可安全注入：默认要求末行是 Shell 提示符，交互输入场景额外允许交互提示"""
    last_line = _last_non_empty_line(buffer)
    if not last_line:
        return False
    if SHELL_PROMPT_LINE_RE.match(last_line):
        return True
    return allow_interactive and bool(INTERACTIVE_PROMPT_END_RE.search(last_line))


def _extract_prompt_fingerprint(buffer: str | None) -> str | None:
    """从终端缓冲区末尾提取当前 Shell 提示符的字面指纹（保留内部字符原样）"""
    candidate = _last_non_empty_line(buffer)
    return candidate if candidate and SHELL_PROMPT_LINE_RE.match(candidate) else None


def _prompt_structure(line: str | None) -> tuple[str, str, str] | None:
    """提取提示符结构签名 (user, host, 权限符)，用于识别仅工作目录变化的等价提示符"""
    if not line:
        return None
    user_host = PROMPT_STRUCTURE_RE.search(line)
    privilege = PROMPT_PRIVILEGE_RE.search(line)
    if not user_host or not privilege:
        return None
    return user_host.group(1), user_host.group(2), privilege.group(1)


def _prompt_returned(buffer: str, fingerprint: str) -> bool:
    """判断缓冲区末行是否与注入前记录的提示符字面指纹完全一致"""
    last_line = _last_non_empty_line(buffer)
    return bool(last_line) and last_line == fingerprint


def _looks_like_unknown_prompt(line: str | None) -> bool:
    """兜底识别自定义 PS1：短行且包含 user@host 特征或以常见提示符符号结尾"""
    if not line:
        return False
    stripped = line.rstrip()
    if not stripped or len(stripped) > 160:
        return False
    if "@" in stripped:
        return True
    return stripped[-1] in UNKNOWN_PROMPT_TAIL_CHARS


def _is_interactive_response(cmd: str) -> bool:
    clean = (cmd or "").strip().lower()
    return clean in {"y", "n", "yes", "no"}

# 注入前等待终端回到空闲提示符的上限（防止与未完成输出拼接错位）
IDLE_WAIT_SECONDS = 8.0
# 注入后等待命令回显稳定的上限（优先等待提示符回归，超时降级截断观察）
SETTLE_MAX_WAIT_SECONDS = 25.0
# 空闲/稳定检测的轮询间隔
POLL_INTERVAL_SECONDS = 0.25


def _clean_terminal_text(raw: str) -> str:
    """
    终端物理回显文本清洗（还原屏幕最终呈现内容）：
    1. 剥除 ANSI 转义序列；
    2. 模拟行内回车覆盖语义：\r 之后写入的内容自行首逐字符覆盖既有内容（超出部分追加）；
    3. 剥除除 \n 与 \t 之外的其余控制字符。
    """
    stripped = ANSI_ESCAPE_RE.sub("", raw)
    lines: list[str] = []
    for seg in stripped.split("\n"):
        if "\r" not in seg:
            lines.append(seg)
            continue
        buf: list[str] = []
        for part in seg.split("\r"):
            if not buf:
                buf = list(part)
                continue
            for i, ch in enumerate(part):
                if i < len(buf):
                    buf[i] = ch
                else:
                    buf.append(ch)
        lines.append("".join(buf))
    return CONTROL_CHARS_RE.sub("", "\n".join(lines))


@dataclass
class ActiveTerminalSession:
    identifier: str
    process: any
    aliases: set[str] = field(default_factory=set)
    recent_chunks: list[str] = field(default_factory=list)
    active_command_chunks: list[str] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_active_time: float = field(default_factory=time.time)
    last_output_time: float = field(default_factory=time.monotonic)

    def append_chunk(self, chunk: str, max_chunks: int = 500) -> None:
        self.recent_chunks.append(chunk)
        if len(self.recent_chunks) > max_chunks:
            self.recent_chunks = self.recent_chunks[-max_chunks:]
        now = time.monotonic()
        self.last_active_time = time.time()
        self.last_output_time = now
        if self.active_command_chunks is not None:
            self.active_command_chunks.append(chunk)

    def start_capturing(self) -> None:
        self.active_command_chunks = []
        self.last_output_time = time.monotonic()

    @property
    def is_capturing(self) -> bool:
        return self.active_command_chunks is not None

    def get_captured_raw(self) -> str:
        if self.active_command_chunks is not None:
            return "".join(self.active_command_chunks)
        return ""

    def stop_capturing(self) -> str:
        raw = self.get_captured_raw()
        self.active_command_chunks = None
        return raw

    def get_clean_buffer(self, last_n_chunks: int = 50) -> str:
        raw = "".join(self.recent_chunks[-last_n_chunks:])
        return _clean_terminal_text(raw)


class InMemorySSHTerminalSessionManager(ISSHTerminalSessionManager):
    """
    内存级终端会话管理器（线程/协程安全单例实现）
    """

    def __init__(self):
        self._sessions: dict[str, ActiveTerminalSession] = {}
        self._alias_map: dict[str, str] = {}

    def register(self, identifier: str, process: any, aliases: list[str] | None = None) -> None:
        """注册当前建立的 WebSocket PTY 进程"""
        alias_set = set(aliases or [])
        alias_set.add(identifier)

        session = ActiveTerminalSession(
            identifier=identifier,
            process=process,
            aliases=alias_set,
        )
        self._sessions[identifier] = session
        for a in alias_set:
            self._alias_map[a] = identifier
        logger.info("Registered active PTY terminal session for '%s' (aliases: %s)", identifier, list(alias_set))

    def unregister(self, identifier: str) -> None:
        """注销断开的 WebSocket PTY 进程"""
        primary = self._alias_map.pop(identifier, identifier)
        session = self._sessions.pop(primary, None)
        if session:
            for a in session.aliases:
                self._alias_map.pop(a, None)
            logger.info("Unregistered active PTY terminal session for '%s'", primary)

    def on_stdout_chunk(self, identifier: str, chunk: str) -> None:
        """当 PTY stdout 产生输出数据时由 WebSocket 推送循环调用记录"""
        primary = self._alias_map.get(identifier, identifier)
        session = self._sessions.get(primary)
        if session:
            session.append_chunk(chunk)

    def has_active_terminal(self, identifier: str) -> bool:
        """检查目标机器是否有正在连接的真实 PTY 窗口"""
        primary = self._alias_map.get(identifier, identifier)
        return primary in self._sessions

    def get_recent_buffer(self, identifier: str) -> str | None:
        """获取目标主机活跃终端最近捕获的屏幕回显快照"""
        primary = self._alias_map.get(identifier, identifier)
        session = self._sessions.get(primary)
        if session:
            return session.get_clean_buffer(50)
        return None

    async def send_and_capture(
        self,
        identifier: str,
        command: str,
        wait_seconds: float = 1.6,
        idle_timeout: float | None = None,
        max_timeout: float | None = None,
        cancel_check: Callable[[], Any] | None = None,
    ) -> str | None:
        """
        向活跃终端注入待执行指令并等待捕获真实输出回显：
        - 注入前等待终端回到空闲 Shell 提示符（防止与未完成的输出/半行内容拼接错位）；
        - 注入后先至少等待 wait_seconds，随后优先等待提示符回归以确认回显完整，超时降级返回。
        - 支持 cancel_check 中途打断正在运行的长指令（发送 Ctrl+C 并抛出 CancelledError）。
        """
        primary = self._alias_map.get(identifier, identifier)
        session = self._sessions.get(primary)
        if not session or not session.process:
            return None

        async with session.lock:
            # 0. 注入前：检查终端是否正停在交互提示中
            tail = session.get_clean_buffer(last_n_chunks=10).strip()
            is_at_interactive = _is_interactive_waiting(tail)

            if is_at_interactive:
                if not _is_interactive_response(command):
                    # 终端正停在交互提示中，而下发的是全新的 Shell 命令：先发送 Ctrl+C (\x03) 退出当前挂起进程
                    try:
                        session.process.stdin.write("\x03")
                        await asyncio.sleep(0.3)
                    except Exception:
                        pass
                    await self._wait_for_idle_prompt(session, idle_timeout)
                else:
                    # 确认输入场景：终端已停在交互提示，显式等待该提示就绪
                    await self._wait_for_idle_prompt(session, idle_timeout, allow_interactive=True)
            else:
                # 正常等待终端回到空闲提示符，避免上个命令尚未结束时命令被拼接错位执行
                await self._wait_for_idle_prompt(session, idle_timeout)

            # 记录注入前提示符的字面指纹，注入后必须精确回归同一字符串才视为执行完成
            prompt_fingerprint = _extract_prompt_fingerprint(session.get_clean_buffer(last_n_chunks=10))
            if prompt_fingerprint:
                logger.debug("Terminal '%s' prompt fingerprint: %r", session.identifier, prompt_fingerprint)

            # 1. 开启本次指令的独立捕获容器
            session.start_capturing()
            try:
                # 防挂起参数清洗：防止 top/ping 等前台指令无限制阻塞 PTY
                sanitized_cmd = command.strip()
                if sanitized_cmd in {"^C", "\x03", "<ctrl+c>"}:
                    try:
                        session.process.stdin.write("\x03")
                        await asyncio.sleep(0.3)
                    except Exception as e:
                        logger.error("Failed to write Ctrl+C to terminal stdin: %s", e)
                    await self._wait_for_idle_prompt(session, idle_timeout)
                    return "(已向终端发送 Ctrl+C 中断信号，恢复 Shell 提示符)"

                if re.match(r"^top\b", sanitized_cmd) and "-b" not in sanitized_cmd:
                    sanitized_cmd += " -b -n 1"
                elif re.match(r"^ping\b", sanitized_cmd) and "-c" not in sanitized_cmd:
                    sanitized_cmd += " -c 4"

                # 2. 敲入命令 + 回车（同时会被 WebSocket 协程推送到左侧前端画布展示）
                try:
                    session.process.stdin.write(sanitized_cmd + "\r")
                except Exception as e:
                    logger.error("Failed to write command to terminal stdin: %s", e)
                    return None

                # 3. 等待命令执行完成：先至少等 wait_seconds，随后等待提示符回归（回显完整的可靠信号）
                had_ps2 = await self._wait_for_output_settled(
                    session,
                    wait_seconds,
                    command=sanitized_cmd,
                    hard_max_timeout=max_timeout,
                    cancel_check=cancel_check,
                    prompt_fingerprint=prompt_fingerprint,
                )

                # 4. 提取从下发指令之后产生的所有 stdout 回显
                raw_output = session.stop_capturing()
                clean_output = _clean_terminal_text(raw_output).strip()

                # 如果检测到进入 PS2 续行提示符（>），明确追加错误说明
                if had_ps2 or (clean_output and (PS2_CONTINUATION_RE.search(clean_output[-30:]) or "> ^C" in clean_output)):
                    clean_output += "\n[错误] 命令存在未闭合引号或语法错误（终端处于 '>' 续行等待），已自动发送 Ctrl+C 中断重置终端。"

                # 如果未捕获到特定分段输出，则兜底返回最近的终端缓冲区末尾
                if not clean_output:
                    clean_output = session.get_clean_buffer(last_n_chunks=30).strip()

                return clean_output
            finally:
                if session.is_capturing:
                    session.stop_capturing()

    @staticmethod
    async def _wait_for_idle_prompt(
        session: "ActiveTerminalSession",
        idle_timeout: float | None,
        allow_interactive: bool = False,
    ) -> bool:
        """轮询等待终端缓冲区末尾出现空闲 Shell 提示符；交互输入场景可放宽到交互提示"""
        timeout = IDLE_WAIT_SECONDS if idle_timeout is None else idle_timeout
        deadline = time.monotonic() + timeout
        while True:
            buffer = session.get_clean_buffer(last_n_chunks=10)
            # 要求末行确实是 Shell 提示符，避免普通输出行末尾含 y/n 或 ? 时被误判为空闲
            if _is_ready_to_inject(buffer, allow_interactive):
                return True
            if time.monotonic() >= deadline:
                tail = _last_non_empty_line(buffer) or ""
                logger.warning(
                    "Terminal '%s' not idle within %.1fs (last output: %r); injecting in degraded mode",
                    session.identifier, timeout, tail[-60:],
                )
                return False
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    @staticmethod
    async def _wait_for_output_settled(
        session: "ActiveTerminalSession",
        min_wait_seconds: float,
        timeout: float = SETTLE_MAX_WAIT_SECONDS,
        command: str = "",
        hard_max_timeout: float | None = None,
        cancel_check: Callable[[], Any] | None = None,
        prompt_fingerprint: str | None = None,
    ) -> bool:
        """
        等待命令回显稳定：
        1. 先至少等 min_wait_seconds，期间实时响应取消；
        2. 动态检测：若输出流正在活跃更新（如 docker pull、yum 下载、编译、解包），动态跟踪 session.last_output_time，绝不在活跃下载中下发 Ctrl+C；
        3. cancel_check 主动取消打断：轮询检测取消信号，立即向终端发送 Ctrl+C 并抛出 CancelledError；
        4. 快速捕获 PS2 续行符（>）：立即下发 Ctrl+C 中断恢复，避免在未闭合引号中无效悬挂；
        5. 提示符回归：若已记录注入前提示符指纹，则要求末行与该指纹精确一致；否则降级使用提示符正则，匹配后立即返回。
        6. 超时仅对静默无输出的挂起指令兜底中断；
        7. hard_max_timeout 保证不会超出整场任务的软预算剩余时间。
        返回：是否遭遇并恢复了 PS2 续行等待。
        """
        async def _is_cancelled() -> bool:
            if not cancel_check:
                return False
            try:
                res = cancel_check()
                if asyncio.iscoroutine(res):
                    return bool(await res)
                return bool(res)
            except Exception:
                return False

        # 先以 0.1s 粒度至少等待 min_wait_seconds，期间实时响应取消
        min_deadline = time.monotonic() + min_wait_seconds
        while time.monotonic() < min_deadline:
            if await _is_cancelled():
                logger.info("Terminal '%s' execution cancelled during initial wait, sending Ctrl+C", session.identifier)
                try:
                    session.process.stdin.write("\x03")
                except Exception:
                    pass
                raise asyncio.CancelledError("Command execution cancelled by user")
            await asyncio.sleep(min(0.1, max(0.01, min_deadline - time.monotonic())))

        is_long_running = bool(re.search(
            r"\b(yum|dnf|apt|apt-get|docker\s+(?:pull|build|run)|git\s+clone|wget|curl|pip|npm|pnpm|yarn|cargo|make|tar)\b",
            command,
            re.IGNORECASE,
        ))
        silence_timeout = 60.0 if is_long_running else 15.0
        calculated_timeout = 900.0 if is_long_running else max(timeout, 45.0)
        if hard_max_timeout is not None:
            max_timeout = max(0.5, min(calculated_timeout, hard_max_timeout))
        else:
            max_timeout = calculated_timeout

        overall_deadline = time.monotonic() + max_timeout

        while time.monotonic() < overall_deadline:
            if await _is_cancelled():
                logger.info("Terminal '%s' execution cancelled during output poll, sending Ctrl+C", session.identifier)
                try:
                    session.process.stdin.write("\x03")
                except Exception:
                    pass
                raise asyncio.CancelledError("Command execution cancelled by user")

            raw = session.get_captured_raw()
            cleaned = _clean_terminal_text(raw).strip()

            # 快速检测 Bash 语法续行符 (PS2, >)：立即发送 Ctrl+C 恢复正常提示符，不浪费超时时间
            if cleaned and PS2_CONTINUATION_RE.search(cleaned[-20:]):
                logger.warning("Terminal '%s' entered PS2 continuation prompt '>', recovering with Ctrl+C immediately", session.identifier)
                try:
                    session.process.stdin.write("\x03")
                    await asyncio.sleep(0.3)
                except Exception:
                    pass
                return True

            if cleaned:
                last_line = _last_non_empty_line(cleaned)
                prompt_returned = False
                prompt_changed = False
                if prompt_fingerprint:
                    # 已记录字面指纹时必须精确回归同一提示符，避免输出末尾的 #/$ 造成误判
                    prompt_returned = _prompt_returned(cleaned, prompt_fingerprint)
                    if not prompt_returned and last_line:
                        if SHELL_PROMPT_LINE_RE.match(last_line):
                            new_structure = _prompt_structure(last_line)
                            old_structure = _prompt_structure(prompt_fingerprint)
                            if new_structure is not None and new_structure == old_structure:
                                # 仅工作目录变化的等价提示符（如 cd 后），立即视为回归
                                logger.info(
                                    "Terminal '%s' prompt changed path but retained structure; treating as returned",
                                    session.identifier,
                                )
                                prompt_returned = True
                            else:
                                prompt_changed = True
                        elif _looks_like_unknown_prompt(last_line):
                            # 自定义 PS1 未被已知规则覆盖：进入宽限降级路径，避免空等到硬超时
                            prompt_changed = True
                else:
                    prompt_returned = bool(READY_AT_END_RE.search(cleaned[-30:]))

                # 交互式确认提问也是合法的“可停靠”状态，需要立即返回以允许下一步输入 y/n
                interactive_waiting = _is_interactive_waiting(cleaned)
                if prompt_returned or interactive_waiting:
                    # 提示符已回归，再等一小拍吸收残余输出
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                    return False

                # 指纹失配宽限：提示符可能含时间戳、分支名或切换用户等动态内容，
                # 输出静默超过宽限窗口后降级接受，避免空等到硬超时并误发 Ctrl+C
                if prompt_changed and time.monotonic() - session.last_output_time >= PROMPT_MISMATCH_GRACE_SECONDS:
                    logger.warning(
                        "Terminal '%s' prompt fingerprint mismatch (expected %r, got %r); "
                        "accepting changed prompt after %.1fs quiet",
                        session.identifier,
                        prompt_fingerprint,
                        last_line,
                        PROMPT_MISMATCH_GRACE_SECONDS,
                    )
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                    return False

            # 如果已经超过静默超时（无任何新数据输出），放弃等待
            if time.monotonic() - session.last_output_time > silence_timeout:
                logger.warning(
                    "Terminal '%s' output silent for %.1fs (command: %s), breaking wait",
                    session.identifier, silence_timeout, command[:40],
                )
                break

            await asyncio.sleep(POLL_INTERVAL_SECONDS)

        # 超时放弃等待，并主动下发 Ctrl+C (\x03) 终止可能挂起的前台未完进程，保护终端不卡死
        try:
            session.process.stdin.write("\x03")
        except Exception:
            pass
        return False
