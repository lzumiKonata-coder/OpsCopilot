"""适配层 - 对话会话、消息流与关键里程碑仓储实现"""

from datetime import datetime, timezone

from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from adapter.repository.models import ChatMessagePO, ChatMilestonePO, ChatSessionPO
from application.ports import IChatSessionRepository
from domain.entities import (
    ChatMessage,
    ChatMilestone,
    ChatSession,
    extract_session_title,
    is_default_session_title,
)


class SQLAlchemyChatRepository(IChatSessionRepository):
    """
    基于规范化表的对齐仓储实现 (chat_session + chat_message + chat_milestone)
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, chat_session: ChatSession) -> ChatSession:
        # 1. 保存/更新 chat_session 表
        stmt = select(ChatSessionPO).where(ChatSessionPO.id == chat_session.id)
        result = await self.session.execute(stmt)
        po = result.scalars().first()

        if po is None:
            po = ChatSessionPO(
                id=chat_session.id,
                agent_id=chat_session.agent_id,
                parent_session_id=chat_session.parent_session_id,
                branch_step=chat_session.branch_step,
                user_id=chat_session.user_id,
                title=chat_session.title,
                message_count=len(chat_session.messages),
                created_at=chat_session.created_at,
                updated_at=chat_session.updated_at,
            )
            self.session.add(po)
        else:
            po.title = chat_session.title
            po.parent_session_id = chat_session.parent_session_id
            po.branch_step = chat_session.branch_step
            po.message_count = len(chat_session.messages)
            po.updated_at = chat_session.updated_at

        await self.session.commit()

        # 2. 同步消息
        if chat_session.messages:
            existing_count_stmt = select(ChatMessagePO).where(ChatMessagePO.session_id == chat_session.id)
            existing_res = await self.session.execute(existing_count_stmt)
            existing_msgs = existing_res.scalars().all()
            existing_len = len(existing_msgs)

            # 追加新产生的消息
            if len(chat_session.messages) > existing_len:
                for msg in chat_session.messages[existing_len:]:
                    msg_po = ChatMessagePO(
                        session_id=chat_session.id,
                        role=msg.role,
                        content=msg.content,
                        tool_name=msg.tool_name,
                        tool_call_id=msg.tool_call_id,
                        priority=msg.priority,
                        token_count=msg.token_count,
                        created_at=msg.created_at,
                    )
                    self.session.add(msg_po)
                await self.session.commit()

        # 3. 同步关键里程碑
        if chat_session.milestones:
            existing_ms_stmt = select(ChatMilestonePO).where(ChatMilestonePO.session_id == chat_session.id)
            existing_ms_res = await self.session.execute(existing_ms_stmt)
            existing_milestones = existing_ms_res.scalars().all()
            existing_ms_len = len(existing_milestones)

            if len(chat_session.milestones) > existing_ms_len:
                for ms in chat_session.milestones[existing_ms_len:]:
                    ms_po = ChatMilestonePO(
                        session_id=chat_session.id,
                        type=ms.type,
                        content=ms.content,
                        created_at=ms.created_at,
                    )
                    self.session.add(ms_po)
                await self.session.commit()

        return await self.get_by_id(chat_session.id) or chat_session

    async def get_by_id(self, session_id: str) -> ChatSession | None:
        stmt = select(ChatSessionPO).where(ChatSessionPO.id == session_id)
        result = await self.session.execute(stmt)
        po = result.scalars().first()
        if not po:
            return None

        # 加载所有对话消息
        msg_stmt = (
            select(ChatMessagePO)
            .where(ChatMessagePO.session_id == session_id)
            .order_by(ChatMessagePO.created_at.asc(), ChatMessagePO.id.asc())
        )
        msg_res = await self.session.execute(msg_stmt)
        messages = [
            ChatMessage(
                id=m.id,
                session_id=m.session_id,
                role=m.role,  # type: ignore
                content=m.content or "",
                tool_name=m.tool_name,
                tool_call_id=m.tool_call_id,
                priority=m.priority,  # type: ignore
                token_count=m.token_count,
                created_at=m.created_at,
            )
            for m in msg_res.scalars().all()
        ]

        # 加载里程碑
        ms_stmt = (
            select(ChatMilestonePO)
            .where(ChatMilestonePO.session_id == session_id)
            .order_by(ChatMilestonePO.created_at.asc(), ChatMilestonePO.id.asc())
        )
        ms_res = await self.session.execute(ms_stmt)
        milestones = [
            ChatMilestone(
                id=ms.id,
                session_id=ms.session_id,
                type=ms.type,
                content=ms.content or "",
                created_at=ms.created_at,
            )
            for ms in ms_res.scalars().all()
        ]

        return ChatSession(
            id=po.id,
            agent_id=po.agent_id,
            parent_session_id=po.parent_session_id,
            branch_step=po.branch_step,
            user_id=po.user_id,
            title=po.title or "新会话",
            message_count=po.message_count,
            created_at=po.created_at,
            updated_at=po.updated_at,
            messages=messages,
            milestones=milestones,
        )

    async def list_by_host(self, host_id: str, limit: int = 50) -> list[ChatSession]:
        stmt = (
            select(ChatSessionPO)
            .where(ChatSessionPO.agent_id == host_id)
            .order_by(desc(ChatSessionPO.updated_at))
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        pos = result.scalars().all()

        sessions = []
        for po in pos:
            # 简版加载用于侧边栏抽屉展示
            sessions.append(
                ChatSession(
                    id=po.id,
                    agent_id=po.agent_id,
                    parent_session_id=po.parent_session_id,
                    branch_step=po.branch_step,
                    user_id=po.user_id,
                    title=po.title or "新会话",
                    message_count=po.message_count,
                    created_at=po.created_at,
                    updated_at=po.updated_at,
                )
            )
        return sessions

    async def delete(self, session_id: str) -> bool:
        # 1. 删除消息
        await self.session.execute(
            delete(ChatMessagePO).where(ChatMessagePO.session_id == session_id)
        )
        # 2. 删除里程碑
        await self.session.execute(
            delete(ChatMilestonePO).where(ChatMilestonePO.session_id == session_id)
        )
        # 3. 删除会话主记录
        stmt = delete(ChatSessionPO).where(ChatSessionPO.id == session_id)
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount > 0

    async def append_message(self, message: ChatMessage) -> ChatMessage:
        if not message.session_id:
            raise ValueError("ChatMessage must have a session_id to be appended.")

        po = ChatMessagePO(
            session_id=message.session_id,
            role=message.role,
            content=message.content,
            tool_name=message.tool_name,
            tool_call_id=message.tool_call_id,
            priority=message.priority,
            token_count=message.token_count,
            created_at=message.created_at,
        )
        self.session.add(po)

        # 更新主会话消息计数与活跃时间
        stmt = select(ChatSessionPO).where(ChatSessionPO.id == message.session_id)
        result = await self.session.execute(stmt)
        sess_po = result.scalars().first()
        if sess_po:
            sess_po.message_count += 1
            sess_po.updated_at = datetime.now(timezone.utc)
            if is_default_session_title(sess_po.title) and message.role == "user":
                extracted = extract_session_title(message.content)
                if extracted and extracted != "新会话":
                    sess_po.title = extracted

        await self.session.commit()
        return ChatMessage(
            id=po.id,
            session_id=po.session_id,
            role=po.role,  # type: ignore
            content=po.content or "",
            tool_name=po.tool_name,
            tool_call_id=po.tool_call_id,
            priority=po.priority,  # type: ignore
            token_count=po.token_count,
            created_at=po.created_at,
        )

    async def add_milestone(self, milestone: ChatMilestone) -> ChatMilestone:
        po = ChatMilestonePO(
            session_id=milestone.session_id,
            type=milestone.type,
            content=milestone.content,
            created_at=milestone.created_at,
        )
        self.session.add(po)
        await self.session.commit()
        return ChatMilestone(
            id=po.id,
            session_id=po.session_id,
            type=po.type,
            content=po.content or "",
            created_at=po.created_at,
        )


