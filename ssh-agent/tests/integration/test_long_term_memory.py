"""长期记忆业务链路回归测试：写入沉淀、提示词注入、查询端点"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from adapter.repository.db_engine import get_session_factory
from adapter.repository import SQLAlchemyMemoryRepository
from adapter.web.main import app
from application.usecases.memory import (
    ManageMemoryUseCase,
    _load_long_term_memory_text,
    _save_long_term_memory,
)
from domain.entities import CoreMemory


@pytest.mark.asyncio
async def test_save_and_load_long_term_memory_roundtrip():
    async with get_session_factory()() as db_session:
        repo = SQLAlchemyMemoryRepository(db_session)
        user_id = f"lt-user-{uuid.uuid4().hex[:8]}"
        memory_key = f"os_env_{uuid.uuid4().hex[:8]}"

        # 1. 首次沉淀主机环境画像
        await _save_long_term_memory(
            repo,
            user_id=user_id,
            memory_type="ServerEnv",
            memory_key=memory_key,
            content="发行版 CentOS 7，登录用户 root",
            keywords="环境,发行版",
        )

        # 2. 同 key 再次沉淀：幂等更新而非新增
        await _save_long_term_memory(
            repo,
            user_id=user_id,
            memory_type="ServerEnv",
            memory_key=memory_key,
            content="发行版 CentOS 7，已升级内核至 5.10",
            keywords="环境,内核",
        )

        # 3. 提示词注入片段包含记忆内容
        text = await _load_long_term_memory_text(repo, user_id)
        assert "长期记忆" in text
        assert "5.10" in text

        # 4. 幂等写入后仅一条记录且命中计数递增
        memories = await ManageMemoryUseCase(repo).list_long_term_memories(user_id=user_id)
        assert len(memories) == 1
        assert memories[0].hit_count == 2


@pytest.mark.asyncio
async def test_load_long_term_memory_degrades_on_repo_error():
    class BrokenRepo:
        async def get_long_term_memories(self, **kwargs):
            raise RuntimeError("db down")

    assert await _load_long_term_memory_text(BrokenRepo(), "u") == ""


@pytest.mark.asyncio
async def test_long_term_memory_api_endpoint():
    async with get_session_factory()() as db_session:
        repo = SQLAlchemyMemoryRepository(db_session)
        user_id = f"api-user-{uuid.uuid4().hex[:8]}"
        await _save_long_term_memory(
            repo,
            user_id=user_id,
            memory_type="Troubleshooting",
            memory_key=f"last_conclusion_{uuid.uuid4().hex[:8]}",
            content="磁盘写满源于 nginx 日志未切割",
            keywords="磁盘,nginx",
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/v1/memories/long-term", params={"user_id": user_id})
            assert res.status_code == 200
            data = res.json()
            assert len(data) >= 1
            assert "nginx" in data[0]["content"]
            assert data[0]["memory_type"] == "Troubleshooting"


@pytest.mark.asyncio
async def test_confidence_boost_on_repeated_confirmation():
    async with get_session_factory()() as db_session:
        repo = SQLAlchemyMemoryRepository(db_session)
        user_id = f"conf-user-{uuid.uuid4().hex[:8]}"
        memory_key = f"os_env_{uuid.uuid4().hex[:8]}"

        await _save_long_term_memory(
            repo, user_id, "ServerEnv", memory_key, "发行版 CentOS 7", confidence=0.6
        )
        first = next(m for m in await repo.get_long_term_memories(user_id) if m.memory_key == memory_key)
        assert float(first.confidence) == pytest.approx(0.6)

        # 同内容再次确认：0.6 + (1-0.6)*0.15 = 0.66
        await _save_long_term_memory(
            repo, user_id, "ServerEnv", memory_key, "发行版 CentOS 7", confidence=0.6
        )
        second = next(m for m in await repo.get_long_term_memories(user_id) if m.memory_key == memory_key)
        assert float(second.confidence) == pytest.approx(0.66, abs=1e-3)

        # 内容被修正：置信度回退到本次提供的初始值
        await _save_long_term_memory(
            repo, user_id, "ServerEnv", memory_key, "发行版 CentOS 8", confidence=0.6
        )
        third = next(m for m in await repo.get_long_term_memories(user_id) if m.memory_key == memory_key)
        assert float(third.confidence) == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_core_memory_relevance_ranking():
    async with get_session_factory()() as db_session:
        repo = SQLAlchemyMemoryRepository(db_session)
        user_id = f"rel-user-{uuid.uuid4().hex[:8]}"

        await repo.save_core_memory(
            CoreMemory(
                user_id=user_id,
                category="Rule",
                title="磁盘扩容规范",
                keywords="磁盘,扩容",
                content="使用 lvm 扩容根分区",
                priority=3,
            )
        )
        await repo.save_core_memory(
            CoreMemory(
                user_id=user_id,
                category="Rule",
                title="网络端口排查",
                keywords="端口,网络",
                content="优先使用 ss -tulpn 查看监听端口",
                priority=3,
            )
        )

        relevant = await repo.find_relevant_core_memories(
            user_id=user_id, query_text="网络端口不通", limit=2
        )
        assert relevant
        assert relevant[0].title == "网络端口排查"

        # 未命中的记忆不应被计入使用次数
        all_memories = await repo.list_core_memories(user_id=user_id, limit=10)
        disk_mem = next(m for m in all_memories if m.title == "磁盘扩容规范")
        assert disk_mem.use_count == 1
