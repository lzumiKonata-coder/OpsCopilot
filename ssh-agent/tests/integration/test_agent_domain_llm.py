"""
SmartOps OpsAgent 领域实体与 LLM 适配层单元测试
"""

import pytest

from adapter.agent.openai_client import OpenAICompatibleLLMClient
from adapter.repository.db_engine import get_session_factory
from adapter.repository import SQLAlchemySSHConnectionRepository
from application.prompts import SYSTEM_PROMPT_TEMPLATE
from application.usecases.chat import ChatWithOpsAgentUseCase
from domain.entities import AuthType, ChatMessage, ChatSession, SSHConnection
from domain.rules import EntityNotFoundError
from tests.fakes.helpers import MockLLMClient


@pytest.mark.asyncio
async def test_domain_chat_message_and_session():
    """测试 1: 验证 ChatMessage 值对象与 ChatSession 实体行为"""
    msg1 = ChatMessage(role="user", content="你好")
    assert msg1.role == "user"
    assert msg1.content == "你好"
    assert msg1.created_at is not None

    # 验证不可变性 (frozen=True)
    with pytest.raises(Exception):
        msg1.content = "修改"  # type: ignore

    session = ChatSession(session_id="test-session-1", host_id="test-host-1")
    assert session.id == "test-session-1"
    assert len(session.messages) == 0

    session.append_message("user", "查看磁盘")
    session.append_message("assistant", "请执行 df -h")
    assert len(session.messages) == 2

    # 验证历史记录截断
    for i in range(15):
        session.append_message("user", f"消息 {i}")

    recent = session.get_recent_history(limit=5)
    assert len(recent) == 5
    assert recent[-1].content == "消息 14"


@pytest.mark.asyncio
async def test_use_case_prompt_rendering():
    """测试 2: 验证 ChatWithOpsAgentUseCase 系统提示词动态上下文绑定与渲染"""
    async with get_session_factory()() as session:
        host_repo = SQLAlchemySSHConnectionRepository(session)
        test_host = SSHConnection(
            host_id="agent-host-001",
            name="k8s-node-01",
            ip="192.168.10.50",
            port=22,
            username="root",
            auth_type=AuthType.PASSWORD,
            password="root_password",
        )
        await host_repo.save(test_host)

        mock_llm = MockLLMClient()
        use_case = ChatWithOpsAgentUseCase(host_repo=host_repo, llm_client=mock_llm)

        chunks = []
        terminal_buf = "No space left on device\nwrite failed\n"
        async for chunk in use_case.execute(
            identifier="agent-host-001",
            user_prompt="系统报 write failed",
            terminal_buffer=terminal_buf,
        ):
            chunks.append(chunk)

        assert len(chunks) > 0
        assert "".join(chunks).startswith("### 1. 根因分析")

        # 验证提示词注入参数
        system_msg = mock_llm.received_messages[0]["content"]
        assert "192.168.10.50" in system_msg
        assert "k8s-node-01" in system_msg
        assert "root" in system_msg
        assert "True" in system_msg  # is_root == True
        assert "No space left on device" in system_msg

        user_msg = mock_llm.received_messages[1]["content"]
        assert user_msg == "系统报 write failed"

        # 验证检索不存在主机抛出 EntityNotFoundError
        with pytest.raises(EntityNotFoundError):
            async for _ in use_case.execute("non-existent-host", "hello"):
                pass


@pytest.mark.asyncio
async def test_llm_client_fallback_mode():
    """测试 3: 验证未配置 API Key 时 OpenAICompatibleLLMClient 优雅降级"""
    client = OpenAICompatibleLLMClient(api_key="")
    chunks = []
    async for chunk in client.stream_chat([{"role": "user", "content": "ping"}]):
        chunks.append(chunk)

    full_resp = "".join(chunks)
    assert "未配置 LLM_API_KEY" in full_resp
    assert "### 1. 根因分析" in full_resp
    assert "```bash" in full_resp
    assert "uptime" in full_resp


@pytest.mark.asyncio
async def test_chat_use_case_intent_classification_and_modes():
    """测试 6: 验证运维相关性分流机制与意图识别"""
    # 1. 验证 is_greeting 判定
    assert ChatWithOpsAgentUseCase.is_greeting("你好") is True
    assert ChatWithOpsAgentUseCase.is_greeting("您好！") is True
    assert ChatWithOpsAgentUseCase.is_greeting("hi") is True
    assert ChatWithOpsAgentUseCase.is_greeting("hello") is True
    assert ChatWithOpsAgentUseCase.is_greeting("在吗？") is True
    assert ChatWithOpsAgentUseCase.is_greeting("你是谁") is True
    assert ChatWithOpsAgentUseCase.is_greeting("介绍一下自己") is True
    assert ChatWithOpsAgentUseCase.is_greeting("写一首诗") is False
    assert ChatWithOpsAgentUseCase.is_greeting("查看磁盘空间") is False
    assert ChatWithOpsAgentUseCase.is_greeting("systemctl restart nginx") is False

    # 2. 验证 SYSTEM_PROMPT_TEMPLATE 中的三层模式与禁止条款
    assert "意图研判与运维相关性分流法则" in SYSTEM_PROMPT_TEMPLATE
    assert "模式 A：问候与自我介绍" in SYSTEM_PROMPT_TEMPLATE
    assert "模式 B：非运维通用问题或闲聊" in SYSTEM_PROMPT_TEMPLATE
    assert "模式 C：真实运维排障与管理任务" in SYSTEM_PROMPT_TEMPLATE
    assert "严禁输出任何运维排障模板" in SYSTEM_PROMPT_TEMPLATE
    assert "当前已登录的机器" in SYSTEM_PROMPT_TEMPLATE
    assert "你可以让我做这些事" in SYSTEM_PROMPT_TEMPLATE


@pytest.mark.asyncio
async def test_llm_client_fallback_intent_routing():
    """测试 7: 验证离线/演示模式下根据用户意图进行不同内容呈现（非运维/问候简短回答，严禁输出代码块）"""
    client = OpenAICompatibleLLMClient(api_key="")

    # 1. 问候分支：简短礼貌回答，禁止输出长篇清单、根因分析和 bash 代码块
    greeting_chunks = []
    async for chunk in client.stream_chat([
        {"role": "system", "content": "目标主机 IP: 10.0.0.88\n主机展示名称: prod-web\n登录用户名: admin\nSSH 连接端口: 2222"},
        {"role": "user", "content": "你好"}
    ]):
        greeting_chunks.append(chunk)

    greeting_text = "".join(greeting_chunks)
    assert "SmartOps" in greeting_text
    assert len(greeting_text) < 150
    assert "### 1. 根因分析" not in greeting_text
    assert "```bash" not in greeting_text

    # 2. 非运维提问分支：简短回答，不套用运维排障模板
    non_ops_chunks = []
    async for chunk in client.stream_chat([
        {"role": "system", "content": "目标主机 IP: 10.0.0.88\n主机展示名称: prod-web\n登录用户名: admin"},
        {"role": "user", "content": "帮我写一首关于秋天的诗"}
    ]):
        non_ops_chunks.append(chunk)

    non_ops_text = "".join(non_ops_chunks)
    assert "无直接关联" in non_ops_text
    assert len(non_ops_text) < 150
    assert "### 1. 根因分析" not in non_ops_text
    assert "```bash" not in non_ops_text

    # 3. 运维排障分支：输出 4 段式排障建议及 bash 代码块
    ops_chunks = []
    async for chunk in client.stream_chat([
        {"role": "system", "content": "目标主机 IP: 10.0.0.88\n主机展示名称: prod-web\n登录用户名: admin"},
        {"role": "user", "content": "磁盘空间报警了如何排查"}
    ]):
        ops_chunks.append(chunk)

    ops_text = "".join(ops_chunks)
    assert "### 1. 根因分析" in ops_text
    assert "```bash" in ops_text
    assert "df -h" in ops_text
