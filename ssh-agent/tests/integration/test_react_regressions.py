"""
智能运维 ReAct 安全回归、终端时序与多模态测试
"""

import time

import pytest

from adapter.repository.db_engine import get_session_factory
from adapter.repository import SQLAlchemyChatRepository, SQLAlchemySSHConnectionRepository
from adapter.ssh.terminal_manager import InMemorySSHTerminalSessionManager
from application.ports import ILLMClient
from application.react.engine import LangGraphReActEngine
from application.react.observation import detect_command_error
from application.react.text_utils import parse_react_response
from application.usecases.react import ReActUseCase
from domain.entities import AuthType, SSHConnection
from tests.fakes.react_helpers import (
    CrashingAuditor,
    FakePtyProcess,
    ForbiddenAuditor,
    MockLLM,
    PermissionDeniedSSHGateway,
    RecordingMockLLM,
    RecordingSSHGateway,
    RootPromptSSHGateway,
    _create_react_host,
)

@pytest.mark.asyncio
async def test_react_auditor_crash_blocks_command():
    """P0 回归: 审查器自身异常时必须 fail-secure 保守阻断，绝不放行指令到 PTY"""
    async with get_session_factory()() as session:
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)
        saved_host = await _create_react_host(host_repo, "audit-crash-host", "192.168.1.110")

        gateway = RecordingSSHGateway()
        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=MockLLM(responses=["Thought: 检查磁盘。\nAction: df -h\nStep_Title: 检查磁盘"]),
            ssh_gateway=gateway,
            session_repo=chat_repo,
            auditor=CrashingAuditor(),
        )

        events = []
        async for ev in uc.execute(saved_host.connection_id, "排查磁盘使用情况", max_steps=5):
            events.append(ev)

        # 铁律断言：审查器崩溃时命令绝不允许被下发执行
        assert gateway.executed == []
        blocked_actions = [e for e in events if e.get("type") == "action" and e.get("blocked")]
        assert len(blocked_actions) == 1
        blocked_obs = [e for e in events if e.get("type") == "observation" and "安全阻断" in e.get("output", "")]
        assert blocked_obs, "审查器异常时必须产出安全阻断观察事件"
        finish = next(e for e in events if e.get("type") == "finish")
        assert finish["success"] is False


@pytest.mark.asyncio
async def test_react_auditor_block_decision_stops_loop():
    """P0 回归: 审查器判定 FORBIDDEN 时阻断指令、不下发终端并终止循环"""
    async with get_session_factory()() as session:
        host_repo = SQLAlchemySSHConnectionRepository(session)
        saved_host = await _create_react_host(host_repo, "audit-block-host", "192.168.1.112")

        gateway = RecordingSSHGateway()
        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=MockLLM(responses=["Thought: 检查磁盘。\nAction: df -h\nStep_Title: 检查磁盘"]),
            ssh_gateway=gateway,
            auditor=ForbiddenAuditor(),
        )

        events = []
        async for ev in uc.execute(saved_host.connection_id, "排查磁盘使用情况", max_steps=5):
            events.append(ev)

        assert gateway.executed == []
        blocked_obs = [e for e in events if e.get("type") == "observation" and "测试规则拦截" in e.get("output", "")]
        assert blocked_obs
        finish = next(e for e in events if e.get("type") == "finish")
        assert finish["success"] is False


@pytest.mark.asyncio
async def test_react_action_multiline_text_not_injected():
    """P0 回归: Action 后跟随的解释性多行文本绝不能被注入 PTY 当作多条命令执行"""
    async with get_session_factory()() as session:
        host_repo = SQLAlchemySSHConnectionRepository(session)
        saved_host = await _create_react_host(host_repo, "multiline-host", "192.168.1.111")

        gateway = RecordingSSHGateway()
        llm = MockLLM(responses=[
            "Thought: 先查看磁盘占用。\nAction: df -h\n接下来我们检查内存使用情况\nStep_Title: 检查磁盘",
            "Thought: 已获取磁盘信息。\nFinal_Answer: 磁盘排查完成，无异常。",
        ])
        uc = ReActUseCase(host_repo=host_repo, llm_client=llm, ssh_gateway=gateway)

        async for _ in uc.execute(saved_host.connection_id, "排查磁盘使用情况", max_steps=5):
            pass

        # 仅第一条真实命令被执行，解释性文本不得进入终端
        assert gateway.executed == ["df -h"]


@pytest.mark.asyncio
async def test_parse_react_action_prefers_first_non_comment_line():
    """P0 回归: Action 解析跳过注释行，提取首个真实命令行"""
    parsed = parse_react_response(
        "Thought: 查看磁盘。\n"
        "Action: ```bash\n# 查看磁盘占用\ndf -h\n```\nStep_Title: 检查磁盘"
    )
    assert parsed["action"] == "df -h"


@pytest.mark.asyncio
async def test_analyze_observation_factual_no_false_match():
    """P0 回归: 回显研判基于命令程序名精确匹配，不编造健康结论"""
    # 修复前 'ss' 子串误匹配 cat /etc/passwd，返回错误的网络研判
    analysis = await LangGraphReActEngine.analyze_observation("cat /etc/passwd", "root:x:0:0:root:/root:/bin/bash")
    assert "网络" not in analysis

    # 修复前 free 命令无论回显如何都宣称"内存处于可控水位"
    analysis_mem = await LangGraphReActEngine.analyze_observation("free -m", "Mem: 15814 15000 800 0 0 400")
    assert "可控水位" not in analysis_mem
    assert "需结合回显" in analysis_mem

    # 带绝对路径与 sudo 前缀的命令名精确提取
    analysis_abs = await LangGraphReActEngine.analyze_observation("sudo /usr/sbin/ss -tlnp", "LISTEN 0 128 *:22 *:*")
    assert "网络连接采集" in analysis_abs

    # 空回显不得宣称成功
    analysis_empty = await LangGraphReActEngine.analyze_observation("systemctl start docker", "")
    assert "不能据此判定成功" in analysis_empty

@pytest.mark.asyncio
async def test_react_root_context_dynamic_correction():
    """P0 回归: 回显出现 ]# root 提示符后，后续 System Prompt 必须修正 is_root，不再诱导 sudo"""
    async with get_session_factory()() as session:
        host_repo = SQLAlchemySSHConnectionRepository(session)
        host = SSHConnection(
            name="root-detect-host",
            host="192.168.1.113",
            port=22,
            username="ops",  # 非 root 用户名，初始研判应为 False
            auth_type=AuthType.PASSWORD,
            password="pwd",
        )
        saved_host = await host_repo.save(host)

        llm = RecordingMockLLM(responses=[
            "Thought: 探测当前用户身份。\nAction: whoami\nStep_Title: 确认身份",
            "Thought: 已确认身份。\nFinal_Answer: 身份确认完成。",
        ])
        gateway = RootPromptSSHGateway()
        uc = ReActUseCase(host_repo=host_repo, llm_client=llm, ssh_gateway=gateway)

        async for _ in uc.execute(saved_host.connection_id, "确认当前用户身份", max_steps=5):
            pass

        assert len(llm.captured) == 2
        assert "root权限: False" in llm.captured[0][0]["content"]
        # 第一步回显携带 [root@hadoop100 ~]# 提示符，第二步 Prompt 必须修正为 root
        assert "root权限: True" in llm.captured[1][0]["content"]


@pytest.mark.asyncio
async def test_react_failure_advice_matches_error_type():
    """P0 回归: 失败兜底结论按报错类型分发建议（权限错误不再误导去查网络/软件源）"""
    async with get_session_factory()() as session:
        host_repo = SQLAlchemySSHConnectionRepository(session)
        host = SSHConnection(
            name="perm-host",
            host="192.168.1.114",
            port=22,
            username="ops",
            auth_type=AuthType.PASSWORD,
            password="pwd",
        )
        saved_host = await host_repo.save(host)

        llm = MockLLM(responses=[
            "Thought: 读取配置。\nAction: sudo cat /etc/shadow\nStep_Title: 读取配置",
            "Thought: 重试一次。\nAction: sudo cat /etc/shadow\nStep_Title: 重试",
        ])
        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=llm,
            ssh_gateway=PermissionDeniedSSHGateway(),
        )

        events = []
        async for ev in uc.execute(saved_host.connection_id, "查看 shadow 配置", max_steps=2):
            events.append(ev)

        finish = next(e for e in events if e.get("type") == "finish")
        assert finish["success"] is False
        assert "sudoers" in finish["conclusion"]
        assert "软件源" not in finish["conclusion"]


def test_terminal_clean_buffer_strips_overlap_and_control_chars():
    """P0 回归: 终端清洗剥除 ANSI/控制字符，并按回车覆盖语义还原屏幕最终内容"""
    mgr = InMemorySSHTerminalSessionManager()
    mgr.register("conn-clean", process=None)
    mgr.on_stdout_chunk("conn-clean", "sudo ls /usr/\x1b[0K\rroot@hadoop100:~# ")
    mgr.on_stdout_chunk("conn-clean", "\x07df -h\nFilesystem  Size  Used Avail\r\n")

    session = mgr._sessions["conn-clean"]
    buf = session.get_clean_buffer()
    assert "\x1b" not in buf
    assert "\x07" not in buf
    assert "\r" not in buf
    # 覆盖语义：\r 后写入的 root 提示符覆盖行首旧内容，不再出现叠字残影
    assert "root@hadoop100:~#" in buf
    assert "Filesystem" in buf


@pytest.mark.asyncio
async def test_terminal_send_and_capture_waits_idle_and_settles():
    """P0 回归: 注入前等待空闲提示符，注入后等待提示符回归并返回清洗后的完整回显"""
    mgr = InMemorySSHTerminalSessionManager()
    fake = FakePtyProcess(
        mgr,
        "conn-e2e",
        lambda cmd: f"{cmd.strip()}\r\nFilesystem  Size\r\n[root@hadoop100 ~]# ",
    )
    mgr.register("conn-e2e", process=fake)
    mgr.on_stdout_chunk("conn-e2e", "[root@hadoop100 ~]# ")  # 预置空闲提示符

    captured = await mgr.send_and_capture("conn-e2e", "df -h", wait_seconds=0.3)

    assert fake.written == ["df -h\r"]
    assert "Filesystem" in captured
    assert "root@hadoop100" in captured
    assert "\r" not in captured


@pytest.mark.asyncio
async def test_terminal_send_and_capture_degrades_when_never_idle():
    """P0 回归: 终端始终未回到提示符时，超过等待上限后仍降级注入（不永久阻塞）"""
    mgr = InMemorySSHTerminalSessionManager()
    fake = FakePtyProcess(
        mgr,
        "conn-busy",
        lambda cmd: f"{cmd.strip()}\r\n[root@hadoop100 ~]# ",
    )
    mgr.register("conn-busy", process=fake)
    mgr.on_stdout_chunk("conn-busy", "yum install -y docker ... running\n")  # 无提示符

    start = time.monotonic()
    captured = await mgr.send_and_capture("conn-busy", "uptime", wait_seconds=0.2, idle_timeout=0.8)
    elapsed = time.monotonic() - start

    assert fake.written == ["uptime\r"]  # 超时后降级注入仍发生
    assert elapsed >= 0.8
    assert "root@hadoop100" in captured


# ==================== LangGraph 重构与缺陷修复增强回归测试 ====================


def test_inspection_command_not_false_alarm():
    """验证：journalctl / grep 等排障日志查看命令回显包含 failed/error 时不误判为指令崩溃"""
    # 1. 普通变更/启动命令出现 failed to start 属于真故障
    err_start = detect_command_error("systemctl start nginx", "Job for nginx.service failed to start.")
    assert err_start is not None
    assert "服务启动或命令执行失败" in err_start

    # 2. 查看日志命令输出中包含 failed to start 属于正常排查线索，不得误判为指令崩溃
    err_log = detect_command_error("journalctl -u nginx --no-pager", "Oct 31 15:00:00 server nginx[123]: failed to start worker process")
    assert err_log is None

    err_grep = detect_command_error("grep 'failed' /var/log/messages", "failed to start service")
    assert err_grep is None

    # 3. 查看日志命令本身确实找不到（例如 grep 不存在）仍需准确捕获
    err_not_found = detect_command_error("journalctl -u nginx", "bash: journalctl: command not found")
    assert err_not_found is not None


def test_parse_react_response_supports_spaces_and_bold_markdown():
    """验证：模型返回加粗 **Thought:** 与带有空格的 Final Answer: 格式解析鲁棒性"""
    raw_reply_1 = (
        "**Thought:** 磁盘水位安全，排查完毕。\n"
        "**Final Answer:** 系统各项指标正常，无需处理。"
    )
    parsed_1 = parse_react_response(raw_reply_1)
    assert parsed_1["final_answer"] == "系统各项指标正常，无需处理。"
    assert "磁盘水位安全" in parsed_1["thought"]

    raw_reply_2 = (
        "Thought: 探测当前负载情况。\n"
        "Action: uptime\n"
        "Step_Title: 查看负载"
    )
    parsed_2 = parse_react_response(raw_reply_2)
    assert parsed_2["action"] == "uptime"
    assert parsed_2["title"] == "查看负载"


@pytest.mark.asyncio
async def test_react_langgraph_cancel_check_aborts_stream():
    """验证：客户端触发取消/断开连接时，LangGraph 状态机立即退出，不再执行后续命令"""
    async with get_session_factory()() as session:
        host_repo = SQLAlchemySSHConnectionRepository(session)
        saved_host = await _create_react_host(host_repo, "cancel-host", "192.168.1.120")

        gateway = RecordingSSHGateway()
        llm = MockLLM(responses=[
            "Thought: 第一步检查。\nAction: df -h\nStep_Title: 检查1",
            "Thought: 第二步检查。\nAction: free -m\nStep_Title: 检查2",
        ])
        uc = ReActUseCase(host_repo=host_repo, llm_client=llm, ssh_gateway=gateway)

        # 模拟执行一步后立即断开
        call_count = 0

        async def fake_cancel():
            nonlocal call_count
            call_count += 1
            return call_count > 1  # 第二步时返回 True 取消

        events = []
        async for ev in uc.execute(saved_host.connection_id, "排查系统", max_steps=5, cancel_check=fake_cancel):
            events.append(ev)

        # 确认未执行全部步骤就被取消终止
        actions = [e for e in events if e.get("type") == "action"]
        assert len(actions) <= 1


@pytest.mark.asyncio
async def test_react_images_are_sent_as_multimodal_message():
    """验证：上传的图片以 OpenAI 兼容的 image_url 内容块传入大模型"""
    async with get_session_factory()() as session:
        host_repo = SQLAlchemySSHConnectionRepository(session)
        saved_host = await _create_react_host(host_repo, "vision-image-host", "192.168.1.131")

        class RecordingLLM(ILLMClient):
            def __init__(self):
                self.messages: list[list] = []

            async def chat(self, messages: list, temperature: float = 0.2) -> str:
                self.messages.append(messages)
                return "Thought: 用户要求描述图片。\nFinal_Answer: 图片中是一位动漫风格的角色。"

            async def stream_chat(self, messages: list):
                yield await self.chat(messages)

        data_url = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
        llm = RecordingLLM()
        uc = ReActUseCase(host_repo=host_repo, llm_client=llm)

        async for _ in uc.execute(
            saved_host.connection_id,
            "描述一下这张图片",
            images=[data_url],
            max_steps=1,
        ):
            pass

        assert llm.messages, "大模型必须被调用"
        user_message = llm.messages[0][-1]
        assert isinstance(user_message["content"], list), "带图片时用户消息必须是多模态内容块"
        image_blocks = [p for p in user_message["content"] if p.get("type") == "image_url"]
        assert image_blocks, "多模态消息中必须包含 image_url 内容块"
        assert image_blocks[0]["image_url"]["url"] == data_url