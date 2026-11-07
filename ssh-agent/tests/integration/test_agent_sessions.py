"""
SmartOps OpsAgent 会话持久化、多轮上下文与异常落库测试
"""

import uuid

import pytest

from adapter.repository.db_engine import get_session_factory
from adapter.repository import SQLAlchemyChatRepository, SQLAlchemySSHConnectionRepository
from application.ports import ILLMClient
from application.usecases.agent_session import (
    CreateAgentSessionUseCase,
    DeleteAgentSessionUseCase,
    GetAgentSessionUseCase,
    ListAgentSessionsUseCase,
)
from application.usecases.chat import ChatWithOpsAgentUseCase
from domain.entities import AuthType, SSHConnection
from tests.fakes.helpers import MockLLMClient


@pytest.mark.asyncio
async def test_chat_use_case_strips_redundant_greeting_for_ops_task():
    """测试 8: 验证当用户发出运维需求（如安装 docker）时，即便模型输出带有冗余问候与客套前缀，也会被彻底过滤剥离"""
    async with get_session_factory()() as session:
        host_repo = SQLAlchemySSHConnectionRepository(session)
        test_host = SSHConnection(
            host_id="agent-host-docker",
            name="prod-web-01",
            ip="192.168.132.100",
            port=22,
            username="root",
            auth_type=AuthType.PASSWORD,
            password="root_password",
        )
        await host_repo.save(test_host)

        # 模拟真实大模型在用户输入“安装docker”时，错误地同时输出了自我介绍与客套前缀的极端场景
        noisy_chunks = [
            "你好，我是 SmartOps AI，可以帮你管理云端服务器。\n\n",
            "当前已登录的机器：\n• **prod-web-01** (192.168.132.100, root, 端口: 22)\n\n",
            "你可以让我做这些事：\n• 安装配置软件 (Nginx、Docker、MySQL 等)\n\n",
            "直接告诉我你想做什么就行。\n\n",
            "关于安装 Docker，我可以帮你完成这个任务。以下是安装 Docker 的步骤：\n\n",
            "1. 根因分析\n",
            "目标主机尚未安装 Docker 引擎与容器运行时。\n\n",
            "### 2. 建议指令\n",
            "```bash\napt-get update && apt-get install -y docker.io\n```\n",
        ]

        mock_llm = MockLLMClient(chunks=noisy_chunks)
        use_case = ChatWithOpsAgentUseCase(host_repo=host_repo, llm_client=mock_llm)

        chunks = []
        async for chunk in use_case.execute(
            identifier="agent-host-docker",
            user_prompt="安装docker",
        ):
            chunks.append(chunk)

        result_text = "".join(chunks)

        # 核心断言：问候语、机器信息、能力清单、客套前言必须全部被干净剥离！
        assert "你好，我是 SmartOps AI" not in result_text
        assert "当前已登录的机器" not in result_text
        assert "你可以让我做这些事" not in result_text
        assert "直接告诉我你想做什么就行" not in result_text
        assert "关于安装 Docker，我可以帮你完成这个任务" not in result_text

        # 核心断言：输出第一行必须直接从根因分析切入，且命令完备
        assert result_text.startswith("### 1. 根因分析")
        assert "目标主机尚未安装 Docker" in result_text
        assert "```bash" in result_text
        assert "apt-get update" in result_text


@pytest.mark.asyncio
async def test_agent_session_repository_and_use_cases():
    """测试 9: 验证会话持久化仓储与会话用例的生命周期管理 (CRUD)"""
    async with get_session_factory()() as db_session:
        host_repo = SQLAlchemySSHConnectionRepository(db_session)
        session_repo = SQLAlchemyChatRepository(db_session)

        unique_host_id = f"session-host-{uuid.uuid4().hex[:8]}"
        test_host = SSHConnection(
            host_id=unique_host_id,
            name="db-master",
            ip="10.0.1.20",
            port=22,
            username="postgres",
            auth_type=AuthType.PASSWORD,
            password="pwd",
        )
        await host_repo.save(test_host)

        create_uc = CreateAgentSessionUseCase(session_repo=session_repo, host_repo=host_repo)
        list_uc = ListAgentSessionsUseCase(session_repo=session_repo, host_repo=host_repo)
        get_uc = GetAgentSessionUseCase(session_repo=session_repo)
        del_uc = DeleteAgentSessionUseCase(session_repo=session_repo)

        # 1. 创建新会话
        session1 = await create_uc.execute(unique_host_id, title="新会话")
        assert session1.id is not None
        assert session1.title == "新会话"
        assert session1.agent_id == unique_host_id
        assert len(session1.messages) == 0

        # 2. 追加消息并持久化
        session1.append_message("user", "查看数据库活跃连接")
        session1.append_message("assistant", "```bash\nselect count(*) from pg_stat_activity;\n```")
        # 验证标题根据首轮提问自动更新
        assert session1.title == "查看数据库活跃连接"
        await session_repo.save(session1)

        # 3. 按 ID 查询详情
        fetched = await get_uc.execute(session1.id)
        assert fetched is not None
        assert fetched.id == session1.id
        assert fetched.title == "查看数据库活跃连接"
        assert len(fetched.messages) == 2
        assert fetched.messages[0].role == "user"
        assert fetched.messages[0].content == "查看数据库活跃连接"
        assert fetched.messages[1].role == "assistant"

        # 4. 创建第二个会话并查询列表
        session2 = await create_uc.execute(unique_host_id, title="清理临时表")
        sessions_list = await list_uc.execute(unique_host_id)
        assert len(sessions_list) == 2
        # 按活跃时间倒序，最后更新的在最前
        assert sessions_list[0].id == session2.id

        # 5. 删除会话
        del_result = await del_uc.execute(session1.id)
        assert del_result is True

        # 再次获取已不存在
        deleted_fetch = await get_uc.execute(session1.id)
        assert deleted_fetch is None
        remaining = await list_uc.execute(unique_host_id)
        assert len(remaining) == 1
        assert remaining[0].id == session2.id


@pytest.mark.asyncio
async def test_agent_session_multi_turn_context_and_auto_title():
    """测试 10: 验证多轮对话历史上下文注入 (Multi-turn Context Retention) 与自动命名"""
    async with get_session_factory()() as db_session:
        host_repo = SQLAlchemySSHConnectionRepository(db_session)
        session_repo = SQLAlchemyChatRepository(db_session)

        unique_host_id = f"mt-host-{uuid.uuid4().hex[:8]}"
        test_host = SSHConnection(
            host_id=unique_host_id,
            name="app-server-01",
            ip="172.16.0.10",
            port=22,
            username="root",
            auth_type=AuthType.PASSWORD,
            password="pwd",
        )
        await host_repo.save(test_host)

        # 第一轮对话：用户提问卡顿
        mock_llm = MockLLMClient(chunks=["### 1. 根因分析\n系统负载偏高\n\n### 2. 建议指令\n```bash\nuptime\n```"])
        use_case = ChatWithOpsAgentUseCase(
            host_repo=host_repo,
            llm_client=mock_llm,
            session_repo=session_repo,
        )

        session_id = f"test-mt-sess-{uuid.uuid4().hex[:8]}"
        chunks_turn1 = []
        async for c in use_case.execute(
            identifier=unique_host_id,
            user_prompt="服务器负载好像很高，请帮我排查",
            session_id=session_id,
        ):
            chunks_turn1.append(c)

        # 验证第一轮持久化落库
        s1 = await session_repo.get_by_id(session_id)
        assert s1 is not None
        assert s1.title == "服务器负载好像很高，请帮我排查"
        assert len(s1.messages) == 2
        assert s1.messages[0].role == "user"
        assert s1.messages[1].role == "assistant"

        # 第二轮对话：针对第一轮结果继续提问
        mock_llm_turn2 = MockLLMClient(chunks=["### 1. 根因分析\n内存使用率正常\n\n### 2. 建议指令\n```bash\nfree -m\n```"])
        use_case_turn2 = ChatWithOpsAgentUseCase(
            host_repo=host_repo,
            llm_client=mock_llm_turn2,
            session_repo=session_repo,
        )

        chunks_turn2 = []
        async for c in use_case_turn2.execute(
            identifier=unique_host_id,
            user_prompt="继续看看内存情况",
            session_id=session_id,
        ):
            chunks_turn2.append(c)

        # 核心断言：第二轮发送给 LLM 的 messages 必须完整包含第一轮的历史消息！
        received = mock_llm_turn2.received_messages
        assert len(received) >= 4  # system + user1 + assistant1 + user2
        assert received[0]["role"] == "system"
        assert received[1]["role"] == "user"
        assert received[1]["content"] == "服务器负载好像很高，请帮我排查"
        assert received[2]["role"] == "assistant"
        assert "系统负载偏高" in received[2]["content"]
        assert received[3]["role"] == "user"
        assert received[3]["content"] == "继续看看内存情况"

        # 验证第二轮结束后会话中有 4 条消息
        s2 = await session_repo.get_by_id(session_id)
        assert s2 is not None
        assert len(s2.messages) == 4
        assert s2.messages[2].content == "继续看看内存情况"
        assert "内存使用率正常" in s2.messages[3].content


@pytest.mark.asyncio
async def test_chat_use_case_persists_user_message_on_zero_output_exception():
    """测试 13: 验证大模型在输出首 token 前抛出异常时，用户的提问仍正确落库持久化，上下文不丢失"""
    class CrashingLLMClient(ILLMClient):
        async def stream_chat(self, messages, temperature=0.2):
            # 模拟在首个 token 返回前直接抛出连接异常
            raise RuntimeError("LLM Service Unavailable (503 / Timeout)")
            yield ""

    async with get_session_factory()() as session:
        host_repo = SQLAlchemySSHConnectionRepository(session)
        chat_repo = SQLAlchemyChatRepository(session)

        test_host = SSHConnection(
            host_id="crash-test-host-001",
            name="crash-test-node",
            ip="192.168.10.99",
            port=22,
            username="root",
            auth_type=AuthType.PASSWORD,
            password="pwd",
        )
        await host_repo.save(test_host)

        use_case = ChatWithOpsAgentUseCase(
            host_repo=host_repo,
            llm_client=CrashingLLMClient(),
            session_repo=chat_repo,
        )

        sid = f"crash-sess-{uuid.uuid4().hex[:6]}"
        prompt = "帮我排查内核崩溃日志"

        # 执行用例，预期内部抛出 RuntimeError
        with pytest.raises(RuntimeError):
            async for _ in use_case.execute(identifier="crash-test-host-001", user_prompt=prompt, session_id=sid):
                pass

        # 核心断言：即使首 token 失败，会话与用户提问必须已持久化在数据库中！
        saved_sess = await chat_repo.get_by_id(sid)
        assert saved_sess is not None
        assert len(saved_sess.messages) >= 2
        # 第一条为用户提问
        assert saved_sess.messages[0].role == "user"
        assert saved_sess.messages[0].content == prompt
        # 第二条为系统级异常告知，对话链路闭合
        assert saved_sess.messages[1].role == "assistant"
        assert "系统异常" in saved_sess.messages[1].content or "未返回任何数据" in saved_sess.messages[1].content
