"""
SmartOps OpsAgent Web SSE 与会话 REST 端点测试
"""

import pytest
from httpx import ASGITransport, AsyncClient

from adapter.web.dependencies import get_llm_client
from adapter.web.main import app
from tests.fakes.helpers import MockLLMClient


@pytest.mark.asyncio
async def test_web_agent_chat_sse_stream():
    """测试 4: 验证 Web API SSE 流式端点 (/api/v1/hosts/{identifier}/agent/chat) 交互"""
    mock_llm = MockLLMClient(chunks=["Hello", " ", "World"])
    app.dependency_overrides[get_llm_client] = lambda: mock_llm

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. 注册目标主机
        reg_payload = {
            "name": "agent-test-host",
            "ip": "172.16.0.8",
            "port": 22,
            "username": "admin",
            "auth_type": "PASSWORD",
            "password": "pass",
            "connection_timeout": 5,
        }
        res_host = await client.post("/api/v1/hosts", json=reg_payload)
        assert res_host.status_code == 201
        host_id = res_host.json()["host_id"]

        # 2. 调用 Agent 对话接口 (SSE 流式)
        chat_req = {
            "prompt": "怎么查看网络连接数？",
            "terminal_buffer": "Active Internet connections",
        }
        res_chat = await client.post(f"/api/v1/hosts/{host_id}/agent/chat", json=chat_req)
        assert res_chat.status_code == 200
        assert "text/event-stream" in res_chat.headers["content-type"]

        body_text = res_chat.text
        assert "data: " in body_text
        assert "data: [DONE]" in body_text
        assert "Hello" in body_text
        assert "World" in body_text

        # 3. 验证通过主机名智能定位
        res_by_name = await client.post("/api/v1/hosts/agent-test-host/agent/chat", json=chat_req)
        assert res_by_name.status_code == 200
        assert "text/event-stream" in res_by_name.headers["content-type"]

        # 4. 验证不存在主机返回友好 SSE 错误或者错误事件
        res_err = await client.post("/api/v1/hosts/unknown-host-xxx/agent/chat", json=chat_req)
        assert res_err.status_code == 200
        assert "error" in res_err.text or "not found" in res_err.text

    # 清理依赖注入覆盖
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_agent_session_web_endpoints():
    """测试 11: 验证会话相关的 Web HTTP / REST API 端点闭环"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 注册主机
        reg_payload = {
            "name": "session-web-host",
            "ip": "10.20.30.40",
            "port": 22,
            "username": "admin",
            "auth_type": "PASSWORD",
            "password": "pass",
        }
        res_host = await client.post("/api/v1/hosts", json=reg_payload)
        host_id = res_host.json()["host_id"]

        # 1. 初始列表为空
        res_list0 = await client.get(f"/api/v1/hosts/{host_id}/agent/sessions")
        assert res_list0.status_code == 200
        assert res_list0.json() == []

        # 2. 创建新会话
        res_create = await client.post(f"/api/v1/hosts/{host_id}/agent/sessions", json={"title": "新会话"})
        assert res_create.status_code == 200
        session_data = res_create.json()
        sid = session_data["session_id"]
        assert session_data["title"] == "新会话"
        assert session_data["message_count"] == 0

        # 3. 带着 session_id 进行 chat 对话
        mock_llm = MockLLMClient()
        app.dependency_overrides[get_llm_client] = lambda: mock_llm
        try:
            chat_payload = {
                "prompt": "查询系统运行时间",
                "session_id": sid,
            }
            res_chat = await client.post(f"/api/v1/hosts/{host_id}/agent/chat", json=chat_payload)
            assert res_chat.status_code == 200
            assert "text/event-stream" in res_chat.headers["content-type"]
        finally:
            app.dependency_overrides.pop(get_llm_client, None)

        # 4. 获取会话详情，验证消息已持久化且标题自动重命名
        res_detail = await client.get(f"/api/v1/agent/sessions/{sid}")
        assert res_detail.status_code == 200
        detail = res_detail.json()
        assert detail["session_id"] == sid
        assert detail["title"] == "查询系统运行时间"
        assert len(detail["messages"]) == 2
        assert detail["messages"][0]["role"] == "user"
        assert detail["messages"][0]["content"] == "查询系统运行时间"
        assert detail["messages"][1]["role"] == "assistant"

        # 5. 获取会话列表，验证 message_count 和更新时间
        res_list = await client.get(f"/api/v1/hosts/{host_id}/agent/sessions")
        assert res_list.status_code == 200
        sessions = res_list.json()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == sid
        assert sessions[0]["message_count"] == 2

        # 6. 删除会话
        res_del = await client.delete(f"/api/v1/agent/sessions/{sid}")
        assert res_del.status_code == 200
        assert res_del.json()["deleted"] is True

        # 7. 再次获取详情返回 404
        res_detail404 = await client.get(f"/api/v1/agent/sessions/{sid}")
        assert res_detail404.status_code == 404
