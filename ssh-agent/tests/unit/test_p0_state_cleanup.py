"""P0 状态清理与载荷限制测试：
1. 会话删除清理检查点与会话树；
2. 图片 base64 不随检查点累积；
3. 单张图片 data URL 体积上限校验。
"""

from typing import List, Optional

import pytest
from pydantic import ValidationError

from adapter.web.schemas import MAX_IMAGE_DATA_URL_LENGTH, ReActRequest
from application.react.engine import LangGraphReActEngine
from application.react.state import ReActGraphState
from application.ports import ILLMClient
from application.runtime import SessionTreeManager
from application.usecases.agent_session import DeleteAgentSessionUseCase
from domain.entities import AuthType, SSHConnection


class _StubSessionRepo:
    def __init__(self, deleted: bool = True):
        self.deleted = deleted
        self.deleted_ids: List[str] = []

    async def delete(self, session_id: str) -> bool:
        self.deleted_ids.append(session_id)
        return self.deleted


class _StubCheckpointer:
    def __init__(self):
        self.deleted_threads: List[str] = []

    def delete_thread(self, thread_id: str) -> None:
        self.deleted_threads.append(thread_id)


class _MockLLM(ILLMClient):
    def __init__(self, responses: Optional[List[str]] = None):
        self.call_count = 0
        self.responses = responses or ["Thought: 查看磁盘\nAction: df -h\nStep_Title: 查看磁盘空间"]

    async def chat(self, messages: list, temperature: float = 0.2, tools=None, on_token=None) -> str:
        idx = min(self.call_count, len(self.responses) - 1)
        resp = self.responses[idx]
        self.call_count += 1
        return resp

    async def stream_chat(self, messages: list):
        yield await self.chat(messages)


@pytest.mark.asyncio
async def test_delete_session_clears_checkpoint_and_tree_state():
    tree = SessionTreeManager()
    tree.register_session("root-session", title="根会话")
    tree.register_branch("root-session", "child-session", 2)

    repo = _StubSessionRepo(deleted=True)
    checkpointer = _StubCheckpointer()
    use_case = DeleteAgentSessionUseCase(
        session_repo=repo,
        checkpointer=checkpointer,
        session_tree_manager=tree,
    )

    assert await use_case.execute("child-session") is True
    assert checkpointer.deleted_threads == ["child-session"]
    assert tree.get_branches("root-session") == []


@pytest.mark.asyncio
async def test_delete_session_keeps_runtime_state_when_db_delete_fails():
    repo = _StubSessionRepo(deleted=False)
    checkpointer = _StubCheckpointer()
    use_case = DeleteAgentSessionUseCase(session_repo=repo, checkpointer=checkpointer)

    assert await use_case.execute("missing-session") is False
    assert checkpointer.deleted_threads == []


@pytest.mark.asyncio
async def test_think_node_clears_images_after_multimodal_round():
    """图片 base64 仅参与首轮多模态消息，后续检查点不得重复存储"""
    engine = LangGraphReActEngine(llm_client=_MockLLM())
    state: ReActGraphState = {
        "host": SSHConnection(
            name="h", host="1.1.1.1", port=22, username="root", auth_type=AuthType.PASSWORD
        ),
        "user_prompt": "描述一下这张图片",
        "images": ["data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="],
        "session_id": "test-image-memory",
        "step_idx": 1,
        "executed_steps": [],
        "react_history": [],
        "max_steps": None,
    }

    result = await engine._node_think(state)
    assert result.get("images") == []


def test_react_request_accepts_normal_image_data_url():
    data_url = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
    req = ReActRequest(prompt="描述图片", images=[data_url])
    assert req.images == [data_url]


def test_react_request_rejects_oversized_image_data_url():
    oversized = "data:image/png;base64," + "A" * MAX_IMAGE_DATA_URL_LENGTH
    with pytest.raises(ValidationError):
        ReActRequest(prompt="描述图片", images=[oversized])


def test_react_request_rejects_non_data_url_image():
    with pytest.raises(ValidationError):
        ReActRequest(prompt="描述图片", images=["https://example.com/demo.png"])
