"""P0 断线/刷新恢复测试：运行期事件缓冲、状态查询与接续端点"""

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient

from adapter.composition_root import container
from adapter.web.main import app

TEST_HOST = "hadoop100"


def test_registry_tracks_running_and_prunes_finished_brokers():
    key = f"{TEST_HOST}:broker-prune"
    broker = container.run_event_broker_registry.create(key)
    assert container.run_event_broker_registry.is_running(key) is True

    broker.finish()
    assert container.run_event_broker_registry.is_running(key) is False

    # 超过保留窗口后自动清理，避免内存长期累积
    broker.finished_at = time.time() - 1000
    assert container.run_event_broker_registry.get(key) is None


@pytest.mark.asyncio
async def test_broker_replays_buffered_events_with_offset():
    key = f"{TEST_HOST}:broker-replay"
    broker = container.run_event_broker_registry.create(key)
    broker.publish({"type": "start", "user_prompt": "排查磁盘"})
    broker.publish({"type": "thought", "step": 1})
    broker.publish({"type": "final_answer", "conclusion": "已定位"})
    broker.finish()

    replayed = [event async for event in broker.subscribe(after=0)]
    assert [e["type"] for e in replayed] == ["start", "thought", "final_answer"]

    tail = [event async for event in broker.subscribe(after=2)]
    assert tail == [{"type": "final_answer", "conclusion": "已定位"}]


@pytest.mark.asyncio
async def test_broker_streams_live_events_to_subscriber():
    key = f"{TEST_HOST}:broker-live"
    broker = container.run_event_broker_registry.create(key)

    async def _consume():
        return [event async for event in broker.subscribe(after=0)]

    consumer = asyncio.create_task(_consume())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    broker.publish({"type": "thought", "step": 1})
    await asyncio.sleep(0)
    broker.publish({"type": "final_answer", "conclusion": "完成"})
    broker.finish()

    events = await asyncio.wait_for(consumer, timeout=2)
    assert [e["type"] for e in events] == ["thought", "final_answer"]


@pytest.mark.asyncio
async def test_status_and_attach_endpoints_support_resume():
    session_id = "broker-api-session"
    key = f"{TEST_HOST}:{session_id}"
    broker = container.run_event_broker_registry.create(key)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        running_resp = await client.get(
            f"/api/v1/hosts/{TEST_HOST}/agent/react/status",
            params={"session_id": session_id},
        )
        assert running_resp.status_code == 200
        assert running_resp.json()["running"] is True

        broker.publish({"type": "start", "user_prompt": "排查磁盘"})
        broker.publish({"type": "final_answer", "conclusion": "已定位"})
        broker.finish()

        finished_resp = await client.get(
            f"/api/v1/hosts/{TEST_HOST}/agent/react/status",
            params={"session_id": session_id},
        )
        assert finished_resp.json()["running"] is False

        attach_resp = await client.get(
            f"/api/v1/hosts/{TEST_HOST}/agent/react/stream",
            params={"session_id": session_id},
        )
        assert attach_resp.status_code == 200
        assert '"type": "start"' in attach_resp.text
        assert '"type": "final_answer"' in attach_resp.text
        assert "[DONE]" in attach_resp.text

        missing_resp = await client.get(
            f"/api/v1/hosts/{TEST_HOST}/agent/react/stream",
            params={"session_id": "no-such-session"},
        )
        assert missing_resp.status_code == 404
