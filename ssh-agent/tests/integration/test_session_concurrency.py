"""P0 并发防护测试：会话运行互斥锁与共享文件写锁"""

import pytest
from httpx import ASGITransport, AsyncClient

from adapter.bootstrap import container
from adapter.web.main import app
from application.react.engine import LangGraphReActEngine
from application.runtime import RemoteFileMutationQueue, SessionConcurrencyGuard


def test_session_guard_rejects_duplicate_and_releases():
    guard = SessionConcurrencyGuard()
    assert guard.try_acquire("session-a") is True
    assert guard.try_acquire("session-a") is False
    guard.release("session-a")
    assert guard.try_acquire("session-a") is True
    guard.release("session-a")


def test_engine_uses_injected_shared_file_mutation_queue():
    class StubLLM:
        model = "stub-model"

    shared_queue = RemoteFileMutationQueue()
    engine = LangGraphReActEngine(llm_client=StubLLM(), file_mutation_queue=shared_queue)
    assert engine.file_mutation_queue is shared_queue


@pytest.mark.asyncio
async def test_react_endpoint_rejects_when_session_already_running():
    """同一 session_id 已被占用时，新请求必须返回 409，而不是并行跑同一检查点"""
    session_key = "hadoop100:concurrency-test-session"
    assert container.session_run_guard.try_acquire(session_key) is True
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/hosts/hadoop100/agent/react",
                json={"prompt": "排查磁盘使用情况", "session_id": "concurrency-test-session"},
            )
            assert resp.status_code == 409
            assert "正在运行" in resp.json()["detail"]
    finally:
        container.session_run_guard.release(session_key)
