"""
智能运维 ReActUseCase 自动化集成测试（核心闭环与意图分流）
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from adapter.repository.db_engine import get_session_factory
from adapter.repository import (
    SQLAlchemyChatRepository,
    SQLAlchemySSHConnectionRepository,
)
from adapter.ssh.terminal_manager import InMemorySSHTerminalSessionManager
from adapter.web.factory import get_llm_client
from adapter.web.main import app
from application.react.observation import detect_command_error
from application.usecases.react import ReActUseCase
from domain.entities import AuthType, SSHConnection
from tests.fakes.react_helpers import MockAdaptiveSSHGateway, MockLLM
from utils.shell import clean_extracted_command, detect_os_family

@pytest.mark.asyncio
async def test_react_use_case_greeting_and_non_ops():
    """测试 1: 问候与非运维需求直接简短回答，不触发任何终端指令与 ReAct 状态机"""
    async with get_session_factory()() as session:
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)

        host = SSHConnection(
            name="react-host-1",
            host="192.168.1.101",
            port=22,
            username="root",
            auth_type=AuthType.PASSWORD,
            password="pwd",
        )
        saved_host = await host_repo.save(host)

        mock_llm = MockLLM()
        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=mock_llm,
            session_repo=chat_repo,
        )

        # 1. 纯日常打招呼
        events = []
        async for ev in uc.execute(saved_host.connection_id, "你好呀"):
            events.append(ev)

        assert len(events) == 1
        assert events[0]["type"] == "greeting"
        assert "SmartOps" in events[0]["content"]

        # 2. 验证非运维闲聊
        events_non_ops = []
        async for ev in uc.execute(saved_host.connection_id, "帮我写一首诗"):
            events_non_ops.append(ev)

        assert len(events_non_ops) == 1
        assert events_non_ops[0]["type"] == "greeting"


@pytest.mark.asyncio
async def test_react_use_case_autonomous_loop():
    """测试 2: 后端自主 ReAct 闭环循环 (Thought -> Action -> Observation -> Reflection -> Finish)"""
    async with get_session_factory()() as session:
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)

        host = SSHConnection(
            name="react-host-2",
            host="192.168.1.102",
            port=22,
            username="root",
            auth_type=AuthType.PASSWORD,
            password="pwd",
        )
        saved_host = await host_repo.save(host)

        mock_llm = MockLLM()
        terminal_mgr = InMemorySSHTerminalSessionManager()

        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=mock_llm,
            terminal_manager=terminal_mgr,
            session_repo=chat_repo,
        )

        session_id = f"react_sess_{uuid.uuid4().hex[:8]}"
        events = []
        async for ev in uc.execute(
            identifier=saved_host.connection_id,
            user_prompt="排查服务器磁盘使用情况",
            session_id=session_id,
            max_steps=3,
        ):
            events.append(ev)

        event_types = [e["type"] for e in events]
        assert "start" in event_types
        assert "thought" in event_types
        assert "action" in event_types
        assert "observation" in event_types
        assert "reflection" in event_types
        assert "finish" in event_types

        # 验证会话与里程碑落库
        saved_session = await chat_repo.get_by_id(session_id)
        assert saved_session is not None
        assert len(saved_session.messages) >= 2  # user + assistant trace
        assert len(saved_session.milestones) >= 1  # DIAGNOSIS


@pytest.mark.integration
@pytest.mark.asyncio
async def test_web_api_react_stream_endpoint():
    """测试 3: Web API /api/v1/hosts/{identifier}/agent/react SSE 流式闭环端点"""
    async with get_session_factory()() as session:
        host_repo = SQLAlchemySSHConnectionRepository(session)
        host = SSHConnection(
            name="react-web-host",
            host="192.168.1.103",
            port=22,
            username="root",
            auth_type=AuthType.PASSWORD,
            password="pwd",
        )
        saved_host = await host_repo.save(host)
        host_id = saved_host.connection_id

    app.dependency_overrides[get_llm_client] = lambda: MockLLM()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        req_payload = {
            "prompt": "检查目标机内存健康度",
            "terminal_buffer": "Mem: 16G total, 8G used, 8G free",
            "max_steps": 2,
            "pre_planned_steps": [
                {"title": "检查内存", "command": "free -m"}
            ]
        }
        res = await client.post(f"/api/v1/hosts/{host_id}/agent/react", json=req_payload)
        assert res.status_code == 200
        assert "text/event-stream" in res.headers["content-type"]

        body_text = res.text
        assert "data: " in body_text
        assert "[DONE]" in body_text
        assert "thought" in body_text
        assert "reflection" in body_text
        assert "finish" in body_text

    app.dependency_overrides.clear()


def test_detect_os_and_command_error():
    """测试 4: 操作系统提示符智能识别与物理报错检测"""
    # 1. 验证 RHEL/CentOS 提示符特征
    rhel_prompt = "[root@hadoop100 ~]# "
    os_info_rhel = detect_os_family(rhel_prompt)
    assert "CentOS" in os_info_rhel or "RPM" in os_info_rhel

    # 2. 验证 Ubuntu/Debian 提示符特征
    ubuntu_prompt = "root@ubuntu:~# "
    os_info_ubuntu = detect_os_family(ubuntu_prompt)
    assert "Ubuntu" in os_info_ubuntu or "DEB" in os_info_ubuntu

    # 3. 验证典型报错识别
    err_apt = detect_command_error("sudo apt-get update", "sudo: apt-get: 找不到命令\r\n")
    assert err_apt is not None
    assert "command not found" in err_apt or "找不到命令" in err_apt

    err_unit = detect_command_error("systemctl start docker", "Failed to start docker.service: Unit not found.")
    assert err_unit is not None
    assert "Unit not found" in err_unit

    err_docker = detect_command_error("docker --version", "bash: docker: 未找到命令...")
    assert err_docker is not None

    # 4. 验证正常回显无误报
    normal_obs = detect_command_error("free -m", "              total        used        free\r\nMem:          15814        1256       14558")
    assert normal_obs is None

    # 5. 验证模型 Action 命令清洗剥离反引号与中文
    raw_action_1 = "`yum makecache` 或 `dnf makecache`，这两个命令都可以创建一个本地包缓存"
    assert clean_extracted_command(raw_action_1) == "yum makecache"

    raw_action_2 = "`yum install -y yum-utils`"
    assert clean_extracted_command(raw_action_2) == "yum install -y yum-utils"

    raw_action_3 = "yum install -y yum`\n\n【格式 A：需要执行下一步探测或修复】："
    assert clean_extracted_command(raw_action_3) == "yum install -y yum"

    # 6. 验证主机名启发式识别
    assert "CentOS" in detect_os_family("", host_name="hadoop100")

@pytest.mark.asyncio
async def test_react_use_case_self_healing_from_apt_to_yum():
    """测试 5: ReAct 闭环自愈能力 — 遇 apt-get 报错自动废弃后续预定步骤并自愈切换为 yum"""
    async with get_session_factory()() as session:
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)

        host = SSHConnection(
            name="hadoop100",
            host="192.168.132.100",
            port=22,
            username="root",
            auth_type=AuthType.PASSWORD,
            password="pwd",
        )
        saved_host = await host_repo.save(host)

        # 模拟物理机回显映射：apt 失败，yum 成功，docker --version 返回版本
        command_map = {
            "sudo apt-get update": "sudo: apt-get: 找不到命令",
            "yum install -y docker": "Loaded plugins: fastestmirror\r\nResolving Dependencies\r\nComplete!",
            "systemctl start docker": "",
            "docker --version": "Docker version 20.10.21, build baeda1f",
        }
        mock_gateway = MockAdaptiveSSHGateway(command_map)

        # 模拟自愈模型的思考过程 (步骤 2 自愈切换为 yum)
        healing_llm = MockLLM(responses=[
            "Thought: 前一步执行 apt-get 报错找不到命令，终端提示符 [root@hadoop100 ~]# 显示目标主机为 CentOS/RHEL 发行版，应改用 yum 安装 docker。\nAction: yum install -y docker\nStep_Title: 使用 yum 安装 Docker 软件包",
            "Thought: Docker 已安装完成，立即启动 docker 守护进程。\nAction: systemctl start docker\nStep_Title: 启动 Docker 服务",
            "Thought: 服务已启动，验证当前 Docker 安装版本。\nAction: docker --version\nStep_Title: 验证 Docker 版本",
            "Thought: 终端回显 Docker version 20.10.21，验证成功。\nFinal_Answer: Docker 软件包已成功通过 yum 完成安装与启动，版本为 20.10.21。",
        ])

        uc = ReActUseCase(
            host_repo=host_repo,
            llm_client=healing_llm,
            ssh_gateway=mock_gateway,
            session_repo=chat_repo,
        )

        # 传入初始的错误方案（包含 apt-get）
        pre_planned_steps = [
            {"title": "更新包列表", "command": "sudo apt-get update"},
            {"title": "安装Docker (无效预定)", "command": "sudo apt-get install -y docker.io"},
            {"title": "启动服务 (无效预定)", "command": "sudo systemctl start docker"},
        ]

        events = []
        async for ev in uc.execute(
            identifier=saved_host.connection_id,
            user_prompt="在目标主机安装并配置 Docker 环境",
            terminal_buffer="[root@hadoop100 ~]# ",
            max_steps=10,
            pre_planned_steps=pre_planned_steps,
        ):
            events.append(ev)

        # 验证步骤流转
        actions = [e.get("command") for e in events if e.get("type") == "action"]
        assert "sudo apt-get update" in actions
        # 核心断言：剩余预定命令 sudo apt-get install -y docker.io 必须被废弃，绝对不允许盲目继续执行！
        assert "sudo apt-get install -y docker.io" not in actions
        # 核心断言：步骤 2 必须自愈为 yum install -y docker
        assert "yum install -y docker" in actions
        assert "docker --version" in actions

        # 验证 finish 事件包含成功与准确结论
        finish_event = next(e for e in events if e.get("type") == "finish")
        assert finish_event["success"] is True
        assert "20.10.21" in finish_event["conclusion"] or "成功" in finish_event["conclusion"]