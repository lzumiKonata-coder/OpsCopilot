"""
测试：终端交互式提示（如 [y/n]、是否删除...？等）的检测、研判与 ReAct 状态机响应机制
验证：
1. detect_interactive_prompt 准确识别中英文各类交互提问
2. analyze_observation 对交互等待和 y/n 确认输入做针对性反思
3. PTY 会话管理器对处于交互状态的终端免 25s 等待，并支持 y/n 穿透输入与非交互指令自动 Ctrl+C 抢权
4. ReAct 状态机完整闭环：从 rm test.txt 触发询问 -> 智能体感知并输入 y -> 成功落地
"""

from typing import List
import pytest

from adapter.repository.database import get_db_session
from adapter.repository import (
    SQLAlchemyChatRepository,
    SQLAlchemySSHConnectionRepository,
)
from adapter.ssh.terminal_manager import InMemorySSHTerminalSessionManager
from application.ports import ILLMClient, ISSHGateway
from application.react.engine import LangGraphReActEngine
from application.react.observation import detect_interactive_prompt
from application.react.text_utils import parse_react_response
from application.usecases.react import ReActUseCase
from domain.entities import AuthType, Command, ExecutionResult, SSHConnection


class MockLLM(ILLMClient):
    def __init__(self, responses: List[str]):
        self.responses = responses
        self.call_count = 0
        self.received_messages: List[list] = []

    async def chat(self, messages: list, temperature: float = 0.2) -> str:
        self.received_messages.append(messages)
        idx = min(self.call_count, len(self.responses) - 1)
        resp = self.responses[idx]
        self.call_count += 1
        return resp

    async def stream_chat(self, messages: list):
        resp = await self.chat(messages)
        yield resp


class InteractiveFakeGateway(ISSHGateway):
    def __init__(self):
        self.executed: List[str] = []

    async def execute(self, host: SSHConnection, command: Command) -> ExecutionResult:
        cmd_str = command.raw_command
        self.executed.append(cmd_str)
        if cmd_str == "rm test.txt":
            return ExecutionResult(stdout="rm: 是否删除普通空文件 'test.txt'?", exit_status=0)
        elif cmd_str == "y":
            return ExecutionResult(stdout="", exit_status=0)
        return ExecutionResult(stdout="Executed: " + cmd_str, exit_status=0)

    async def test_connection(self, host: SSHConnection) -> bool:
        return True


class FakeInteractivePtyProcess:
    def __init__(self, manager: InMemorySSHTerminalSessionManager, identifier: str):
        self.manager = manager
        self.identifier = identifier
        self.written: List[str] = []
        self.stdin = self

    def write(self, data: str):
        self.written.append(data)
        stripped = data.strip()
        if stripped == "rm test.txt":
            # 模拟 rm 提示是否删除普通空文件
            chunk = "rm test.txt\r\nrm: 是否删除普通空文件 'test.txt'? "
            self.manager.on_stdout_chunk(self.identifier, chunk)
        elif stripped == "y":
            # 模拟输入 y 后删除成功并回归 Shell 提示符
            chunk = "y\r\n[root@hadoop100 ~]# "
            self.manager.on_stdout_chunk(self.identifier, chunk)
        elif stripped == "\x03":
            # 模拟 Ctrl+C 打断并回归 Shell 提示符
            chunk = "^C\r\n[root@hadoop100 ~]# "
            self.manager.on_stdout_chunk(self.identifier, chunk)
        elif stripped.startswith("rm -f"):
            chunk = f"{stripped}\r\n[root@hadoop100 ~]# "
            self.manager.on_stdout_chunk(self.identifier, chunk)


def test_detect_interactive_prompt_patterns():
    """验证：准确识别中英文各类交互式提示符，非交互式输出返回 None"""
    # 典型 Linux 交互式询问
    assert detect_interactive_prompt("rm: 是否删除普通空文件 'test.txt'?") is not None
    assert detect_interactive_prompt("rm: remove regular empty file 'test.txt'? ") is not None
    assert detect_interactive_prompt("Do you want to continue? [Y/n]") == "[Y/n]"
    assert detect_interactive_prompt("Is this ok [y/d/N]: ") == "Is this ok [y/d/N]:"
    assert detect_interactive_prompt("Overwrite foo.txt? (y/n)") == "(y/n)"
    assert detect_interactive_prompt("是否继续执行？[y/n]") is not None

    # 普通输出不得误判
    assert detect_interactive_prompt("[root@hadoop100 ~]# ") is None
    assert detect_interactive_prompt("Filesystem Size Used Avail Use% Mounted on") is None
    assert detect_interactive_prompt("Active: active (running)") is None
    assert detect_interactive_prompt("bash: rm: command not found") is None


@pytest.mark.asyncio
async def test_analyze_observation_with_interactive_prompt():
    """验证：研判回显能明确指出正在等待交互式输入并指导下发 y/n"""
    # 1. 遇到交互询问，提示模型下发 y
    analysis_prompt = await LangGraphReActEngine.analyze_observation(
        "rm test.txt",
        "rm: 是否删除普通空文件 'test.txt'?"
    )
    assert "等待交互式确认输入" in analysis_prompt
    assert "'y'" in analysis_prompt

    # 2. 下发 y 后，研判确认已完成
    analysis_y = await LangGraphReActEngine.analyze_observation("y", "")
    assert "已向终端发送交互确认" in analysis_y


@pytest.mark.asyncio
async def test_terminal_manager_interactive_prompt_and_y_input():
    """验证：PTY 会话管理器遇到 rm 提示符时无需等待 25 秒，且后续输入 y 能够顺利完成交互"""
    mgr = InMemorySSHTerminalSessionManager()
    fake = FakeInteractivePtyProcess(mgr, "conn-interact")
    mgr.register("conn-interact", process=fake)
    mgr.on_stdout_chunk("conn-interact", "[root@hadoop100 ~]# ")

    # 1. 执行 rm test.txt -> 应该迅速捕获到交互提示，且不会发送 \x03 打断
    obs1 = await mgr.send_and_capture("conn-interact", "rm test.txt", wait_seconds=0.2)
    assert "是否删除普通空文件" in obs1
    assert fake.written == ["rm test.txt\r"]  # 绝对没有发送 \x03

    # 2. 下一步直接输入 y -> 应该顺利写入 stdin，不发 Ctrl+C，并收到最后的 Shell 提示符
    obs2 = await mgr.send_and_capture("conn-interact", "y", wait_seconds=0.2)
    assert fake.written == ["rm test.txt\r", "y\r"]
    assert "root@hadoop100" in obs2


@pytest.mark.asyncio
async def test_terminal_manager_non_interactive_command_clears_stuck_prompt():
    """验证：如果终端停在 rm 交互提示符中，但下发的不是 y/n 而是新命令时，自动发送 Ctrl+C 抢权避免错位"""
    mgr = InMemorySSHTerminalSessionManager()
    fake = FakeInteractivePtyProcess(mgr, "conn-clear")
    mgr.register("conn-clear", process=fake)
    mgr.on_stdout_chunk("conn-clear", "[root@hadoop100 ~]# ")

    # 1. 注入 rm test.txt 挂起在提示符
    await mgr.send_and_capture("conn-clear", "rm test.txt", wait_seconds=0.2)
    assert "是否删除普通空文件" in mgr.get_recent_buffer("conn-clear")

    # 2. 下发的不是 y/n，而是全新命令 rm -f test.txt
    await mgr.send_and_capture("conn-clear", "rm -f test.txt", wait_seconds=0.2)

    # 验证：先写入了 \x03 取消悬挂，再写入了 rm -f test.txt\r
    assert "\x03" in fake.written
    assert "rm -f test.txt\r" in fake.written


@pytest.mark.asyncio
async def test_react_loop_handles_interactive_y_input():
    """验证：完整 ReAct 闭环在遇到 rm 交互询问时，智能体感知并输入 y，成功达成任务"""
    async for session in get_db_session():
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)
        host = SSHConnection(
            name="interact-host",
            host="192.168.1.150",
            port=22,
            username="root",
            auth_type=AuthType.PASSWORD,
            password="pwd",
        )
        saved_host = await host_repo.save(host)

        gateway = InteractiveFakeGateway()
        # 模拟模型：第一步下发 rm test.txt，第二步看到回显后输入 y，第三步完成
        llm = MockLLM(responses=[
            "Thought: 探测并删除旧测试文件。\nAction: rm test.txt\nStep_Title: 删除测试文件",
            "Thought: 终端回显提示是否删除空文件，直接输入 y 确认删除。\nAction: y\nStep_Title: 确认删除",
            "Thought: 文件已成功删除，排障完毕。\nFinal_Answer: 测试文件 test.txt 已成功删除，流程执行完毕。",
        ])

        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=llm,
            ssh_gateway=gateway,
            session_repo=chat_repo,
        )

        events = []
        async for ev in uc.execute(saved_host.connection_id, "清理 test.txt 文件", max_steps=5):
            events.append(ev)

        # 验证步骤
        actions = [e for e in events if e.get("type") == "action"]
        assert len(actions) == 2
        assert actions[0]["command"] == "rm test.txt"
        assert actions[1]["command"] == "y"

        # 验证物理网关执行顺序
        assert gateway.executed == ["rm test.txt", "y"]

        # 验证观察与反思
        reflections = [e for e in events if e.get("type") == "reflection"]
        assert "等待交互式确认输入" in reflections[0]["analysis"]
        assert "已向终端发送交互确认 'y'" in reflections[1]["analysis"]

        # 验证最终完成
        finish_events = [e for e in events if e.get("type") == "finish"]
        assert len(finish_events) == 1
        assert finish_events[0]["success"] is True



def test_parse_react_response_extracts_command_from_four_part_report():
    """验证：当模型输出四段式报告而非严格 Thought/Action 时，能准确从中提取待执行命令 y 而非误判为结束"""
    raw_reply = """### 2. 根因分析
终端正在等待交互式确认输入，需要确认删除 `test.txt` 文件。

### 2. 建议指令
```bash
# 确认删除 test.txt 文件
y
```

### 3. 效果验证
```bash
# 验证 test.txt 文件是否已被删除
ls | grep test.txt
```"""

    parsed = parse_react_response(raw_reply)
    assert parsed["action"] == "y"
    assert parsed["title"] == "确认删除 test.txt 文件"
    assert "等待交互式确认输入" in parsed["thought"]
    assert parsed["final_answer"] is None  # 存在有效 Action，绝不应判定为 final_answer


@pytest.mark.asyncio
async def test_react_loop_handles_markdown_four_part_report_with_y():
    """验证：真实 ReAct 循环中，模型第 2 步回复四段式包含 y 的代码块时，能准确执行 y 而非提前结束"""
    async for session in get_db_session():
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)
        host = SSHConnection(
            name="four-part-host",
            host="192.168.1.151",
            port=22,
            username="root",
            auth_type=AuthType.PASSWORD,
            password="pwd",
        )
        saved_host = await host_repo.save(host)

        gateway = InteractiveFakeGateway()
        # 模拟模型：第 1 步下发 rm test.txt，第 2 步返回四段式带 y 代码块，第 3 步结束
        four_part_reply = """### 2. 根因分析 终端正在等待交互式确认输入，需要确认删除 `test.txt` 文件。
### 2. 建议指令
```bash
# 确认删除 test.txt 文件
y
```
### 3. 效果验证
```bash
# 验证 test.txt 文件是否已被删除
ls | grep test.txt
```"""

        llm = MockLLM(responses=[
            """Thought: 删除目标测试文件。\nAction: rm test.txt\nStep_Title: 删除文件""",
            four_part_reply,
            """Thought: 文件已确认删除，排障完毕。\nFinal_Answer: 文件已成功清理。""",
        ])

        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=llm,
            ssh_gateway=gateway,
            session_repo=chat_repo,
        )

        events = []
        async for ev in uc.execute(saved_host.connection_id, "删除 test.txt", max_steps=5):
            events.append(ev)

        # 核心验证：y 必须被作为 action 真实下发执行！
        assert "rm test.txt" in gateway.executed
        assert "y" in gateway.executed
        assert gateway.executed == ["rm test.txt", "y"]

        # 核心验证：不会在第 2 步就停止
        actions = [e for e in events if e.get("type") == "action"]
        assert len(actions) == 2


@pytest.mark.asyncio
async def test_react_loop_auto_completes_y_when_terminal_waiting_and_model_omits_action():
    """验证：若终端停在交互提示符，但模型仅输出纯文本未生成任何命令时，底层兜底自动发送 y 推进，不挂起终端"""
    async for session in get_db_session():
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)
        host = SSHConnection(
            name="auto-y-host",
            host="192.168.1.152",
            port=22,
            username="root",
            auth_type=AuthType.PASSWORD,
            password="pwd",
        )
        saved_host = await host_repo.save(host)

        gateway = InteractiveFakeGateway()
        # 第 2 步模型未输出任何 Action，仅输出了纯文本总结
        llm = MockLLM(responses=[
            """Thought: 删除目标文件。\nAction: rm test.txt\nStep_Title: 删除文件""",
            """Thought: 终端需要确认删除。\nFinal_Answer: 目标正在等待输入确认，建议用户在终端手动输入 y。""",
            """Thought: 确认清理完毕。\nFinal_Answer: 清理完成。""",
        ])

        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=llm,
            ssh_gateway=gateway,
            session_repo=chat_repo,
        )

        events = []
        async for ev in uc.execute(saved_host.connection_id, "删除 test.txt", max_steps=5):
            events.append(ev)

        # 核心验证：系统感知到终端处于 rm 交互等待，即便模型未输出 action，也自动下发 y 确认，不让终端死锁
        assert "rm test.txt" in gateway.executed
        assert "y" in gateway.executed



@pytest.mark.asyncio
async def test_analyze_observation_grep_empty_output_indicates_success():
    """验证：grep/find 过滤命令为空回显时，智能判定为已无匹配项（达成清理），不误报'不能据此判定成功'"""
    res = await LangGraphReActEngine.analyze_observation("ls | grep test.txt", "")
    assert "未检索到匹配结果" in res
    assert "任务达成" in res
    assert "不能据此判定成功" not in res

    # 非过滤类命令（如 systemctl start）空回显仍需保留严谨告警
    res_sys = await LangGraphReActEngine.analyze_observation("systemctl start nginx", "")
    assert "不能据此判定成功" in res_sys


def test_detect_command_error_inspection_no_such_file():
    """验证：ls/stat 查看类命令回显文件不存在时，不误判为指令崩溃，支持删除验证"""
    from application.react.observation import detect_command_error
    err = detect_command_error("ls test.txt", "ls: cannot access 'test.txt': No such file or directory")
    assert err is None


@pytest.mark.asyncio
async def test_react_loop_anti_infinite_verification_loop():
    """验证：若智能体在删除后连续重复执行相同查看验证指令（如多次 ls | grep），状态机自动收敛并成功结束"""
    async for session in get_db_session():
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)
        host = SSHConnection(
            name="loop-host",
            host="192.168.1.153",
            port=22,
            username="root",
            auth_type=AuthType.PASSWORD,
            password="pwd",
        )
        saved_host = await host_repo.save(host)

        gateway = InteractiveFakeGateway()
        # 模拟模型：第 1 步删除，第 2 步发 y，随后不断重复执行 ls | grep test.txt
        llm = MockLLM(responses=[
            "Thought: 删除目标文件。\nAction: rm test.txt\nStep_Title: 删除文件",
            "Thought: 确认删除。\nAction: y\nStep_Title: 确认执行",
            "Thought: 验证文件是否已删除。\nAction: ls | grep test.txt\nStep_Title: 验证删除1",
            "Thought: 再次验证文件。\nAction: ls | grep test.txt\nStep_Title: 验证删除2",
            "Thought: 再次验证文件。\nAction: ls | grep test.txt\nStep_Title: 验证删除3",
        ])

        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=llm,
            ssh_gateway=gateway,
            session_repo=chat_repo,
        )

        events = []
        async for ev in uc.execute(saved_host.connection_id, "删除 test.txt", max_steps=10):
            events.append(ev)

        # 核心验证：在重复验证 2 次后自动收敛触发 finish，绝对不会死循环跑满 10 步
        actions = [e for e in events if e.get("type") == "action"]
        assert len(actions) <= 4

        finish_events = [e for e in events if e.get("type") == "finish"]
        assert len(finish_events) == 1
        assert finish_events[0]["success"] is True


def test_clean_extracted_command_preserves_chinese_commas_inside_quotes():
    """验证：clean_extracted_command 绝不会截断引号内部包含中文逗号或括号的参数字符串"""
    from utils.shell import clean_extracted_command

    # 1. 引号内包含中文逗号、括号与空格
    cmd = 'if pgrep -x yum >/dev/null 2>&1; then echo "yum进程仍在运行，请等待"; else echo "ok"; fi'
    res = clean_extracted_command(cmd)
    assert res == cmd
    assert res.endswith('fi')

    # 2. 单引号内包含逗号与中文
    cmd_single = "echo '未安装软件包，准备下一步安装'"
    assert clean_extracted_command(cmd_single) == cmd_single

    # 3. 引号外部附带的中文说明正确被剥离
    cmd_with_comment = 'systemctl restart docker，重启服务以生效'
    assert clean_extracted_command(cmd_with_comment) == 'systemctl restart docker'


def test_detect_interactive_prompt_ignores_ps2_continuation():
    """验证：detect_interactive_prompt 不会将 Bash 的 PS2 (>) 误判为交互式确认提示"""
    assert detect_interactive_prompt('>') is None
    assert detect_interactive_prompt('>\n') is None
    assert detect_interactive_prompt('dquote>') is None


@pytest.mark.asyncio
async def test_terminal_manager_recovers_immediately_from_ps2_continuation():
    """验证：PTY 会话管理器遇到 Bash 续行等待 (>) 时立即发送 Ctrl+C 中断恢复，不等待超时"""
    mgr = InMemorySSHTerminalSessionManager()

    class FakePs2PtyProcess:
        def __init__(self, m, ident):
            self.m = m
            self.ident = ident
            self.written = []
            self.stdin = self

        def write(self, data):
            self.written.append(data)
            if data.strip() == '\x03':
                self.m.on_stdout_chunk(self.ident, '^C\r\n[root@hadoop100 ~]# ')
            else:
                self.m.on_stdout_chunk(self.ident, f'{data}\r\n> ')

    fake_ps2 = FakePs2PtyProcess(mgr, 'conn-ps2-test')
    mgr.register('conn-ps2-test', process=fake_ps2)
    mgr.on_stdout_chunk('conn-ps2-test', '[root@hadoop100 ~]# ')

    obs = await mgr.send_and_capture('conn-ps2-test', 'echo "unclosed quote', wait_seconds=0.1)
    assert '\x03' in fake_ps2.written
    assert '[错误]' in obs
    assert '续行' in obs


@pytest.mark.asyncio
async def test_send_and_capture_with_many_prior_chunks():
    """验证：当终端历史 buffer 累积超过 200 个 chunk 时，新命令回显捕获与提示符识别不受任何影响"""
    mgr = InMemorySSHTerminalSessionManager()

    class FakeEchoPty:
        def __init__(self, m, ident):
            self.m = m
            self.ident = ident
            self.stdin = self

        def write(self, data):
            # 模拟执行 docker images mysql 快速返回
            self.m.on_stdout_chunk(self.ident, f"{data}\r\nREPOSITORY   TAG   IMAGE ID\r\nmysql        latest abc123\r\n[root@hadoop100 ~]# ")

    fake = FakeEchoPty(mgr, 'conn-many-chunks')
    mgr.register('conn-many-chunks', process=fake)

    # 先灌入 300 个历史 chunk
    for i in range(300):
        mgr.on_stdout_chunk('conn-many-chunks', f'history line {i}\r\n')

    mgr.on_stdout_chunk('conn-many-chunks', '[root@hadoop100 ~]# ')

    # 执行新命令
    obs = await mgr.send_and_capture('conn-many-chunks', 'docker images mysql', wait_seconds=0.1)
    assert 'REPOSITORY' in obs
    assert 'mysql' in obs
    assert 'abc123' in obs


def test_detect_command_error_inspection_not_polluted_by_grep_or_log():
    """验证：grep / 日志查看回显包含报错文本时不被误判为指令崩溃，而工具自身抛错仍能被准确识别"""
    from application.react.observation import detect_command_error

    # 1. grep 检索目标包含 "command not found"，回显匹配行不应误判为错误
    grep_cmd = "grep -rn 'command not found' /var/log/messages"
    grep_obs = "/var/log/messages:120:bash: legacy_tool: command not found"
    assert detect_command_error(grep_cmd, grep_obs) is None

    # 2. cat 日志包含 "Permission denied" 业务内容，不应误判为 cat 指令自身崩溃
    cat_cmd = "cat /var/log/secure"
    cat_obs = "Apr 10 12:00:00 srv sshd[123]: Failed password: Permission denied for user guest"
    assert detect_command_error(cat_cmd, cat_obs) is None

    # 3. 查看工具自身真正权限不足报错 (cat: /etc/shadow: Permission denied) 仍应准确识别
    cat_err_obs = "cat: /etc/shadow: Permission denied"
    err = detect_command_error("cat /etc/shadow", cat_err_obs)
    assert err is not None
    assert "Permission denied" in err

    # 4. 真实执行未安装命令，即便不在查看白名单，也准确报错
    real_cmd = "my_custom_tool --version"
    real_obs = "-bash: my_custom_tool: command not found"
    err2 = detect_command_error(real_cmd, real_obs)
    assert err2 is not None
    assert "command not found" in err2


@pytest.mark.asyncio
async def test_react_step_exhaustion_does_not_falsely_claim_success():
    """验证：ReAct 步数耗尽且模型未输出 Final_Answer 时，严禁虚报成功，必须明确提示未闭环"""
    from application.react.engine import LangGraphReActEngine

    class DummyClient:
        pass

    engine = LangGraphReActEngine(llm_client=DummyClient())

    # 构造步数耗尽（step_idx=6 > max_steps=5），无报错但未输出 Final_Answer 的状态
    state = {
        "step_idx": 6,
        "max_steps": 5,
        "final_conclusion": None,
        "final_answer": None,
        "has_encountered_error": False,
        "last_error_detail": None,
        "planned_steps_queue": [],
        "executed_steps": [
            {"step": 1, "title": "步骤1", "command": "uptime", "observation": "load: 0.1", "analysis": "正常", "is_error": False},
            {"step": 2, "title": "步骤2", "command": "free -m", "observation": "mem ok", "analysis": "正常", "is_error": False},
        ],
    }

    result = await engine._node_finish(state)
    assert result["is_success"] is False
    assert "顺利执行完成" not in result["final_conclusion"]
    assert "目标操作已成功落地" not in result["final_conclusion"]
    assert "未闭环" in result["final_conclusion"]
    assert "已达最大步数限制" in result["final_conclusion"]
