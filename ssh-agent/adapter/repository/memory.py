"""适配层 - 核心记忆与长期记忆仓储实现"""

from datetime import datetime, timezone
from decimal import Decimal
import re

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from adapter.repository.models import CoreMemoryPO, LongTermMemoryPO
from application.ports import IMemoryRepository
from domain.entities import CoreMemory, LongTermMemory


class SQLAlchemyMemoryRepository(IMemoryRepository):
    """
    基于 SQLAlchemy 2.0 的核心记忆与长期记忆仓储实现
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _core_to_entity(po: CoreMemoryPO) -> CoreMemory:
        return CoreMemory(
            id=po.id,
            user_id=po.user_id,
            scope=po.scope,  # type: ignore
            category=po.category,  # type: ignore
            title=po.title,
            keywords=po.keywords,
            content=po.content or "",
            priority=po.priority,
            source_session_id=po.source_session_id,
            use_count=po.use_count,
            created_at=po.created_at,
            last_used_at=po.last_used_at,
        )

    @staticmethod
    def _long_to_entity(po: LongTermMemoryPO) -> LongTermMemory:
        return LongTermMemory(
            id=po.id,
            user_id=po.user_id,
            session_id=po.session_id,
            memory_type=po.memory_type,
            memory_key=po.memory_key,
            content=po.content,
            keywords=po.keywords,
            source_role=po.source_role,
            confidence=float(po.confidence),
            hit_count=po.hit_count,
            created_at=po.created_at,
            updated_at=po.updated_at,
        )

    async def save_core_memory(self, memory: CoreMemory) -> CoreMemory:
        if memory.id is not None:
            po = await self.session.get(CoreMemoryPO, memory.id)
            if po:
                po.title = memory.title
                po.content = memory.content
                po.keywords = memory.keywords
                po.priority = memory.priority
                po.category = memory.category
                po.scope = memory.scope
                po.use_count = memory.use_count
                po.last_used_at = datetime.now(timezone.utc)
                await self.session.commit()
                return self._core_to_entity(po)

        po = CoreMemoryPO(
            user_id=memory.user_id,
            scope=memory.scope,
            category=memory.category,
            title=memory.title,
            keywords=memory.keywords,
            content=memory.content,
            priority=memory.priority,
            source_session_id=memory.source_session_id,
            use_count=memory.use_count,
            created_at=memory.created_at,
            last_used_at=memory.last_used_at,
        )
        self.session.add(po)
        await self.session.commit()
        return self._core_to_entity(po)

    async def list_core_memories(
        self,
        user_id: str = "default",
        category: str | None = None,
        limit: int = 20,
    ) -> list[CoreMemory]:
        stmt = select(CoreMemoryPO).where(CoreMemoryPO.user_id == user_id)
        if category:
            stmt = stmt.where(CoreMemoryPO.category == category)
        stmt = stmt.order_by(desc(CoreMemoryPO.priority), desc(CoreMemoryPO.last_used_at)).limit(limit)
        result = await self.session.execute(stmt)
        return [self._core_to_entity(po) for po in result.scalars().all()]

    @staticmethod
    def _tokenize_query(text: str) -> list[str]:
        """查询词元切分：英文/数字按词，中文按 2 字滑窗。"""
        lowered = (text or "").lower()
        tokens = set(re.findall(r"[a-z0-9_\-]{2,}", lowered))
        for segment in re.findall(r"[\u4e00-\u9fff]+", lowered):
            for i in range(len(segment) - 1):
                tokens.add(segment[i : i + 2])
        return list(tokens)

    @staticmethod
    def _score_core_memory(po: CoreMemoryPO, tokens: list[str]) -> float:
        """相关性打分：关键词命中最重，标题次之，正文最轻。"""
        keywords = (po.keywords or "").lower()
        title = (po.title or "").lower()
        content = (po.content or "").lower()
        score = 0.0
        for token in tokens:
            if token in keywords:
                score += 3.0
            elif token in title:
                score += 2.0
            elif token in content:
                score += 1.0
        return score

    async def find_relevant_core_memories(
        self,
        user_id: str,
        query_text: str,
        limit: int = 5,
    ) -> list[CoreMemory]:
        # 扩大候选池后再按相关性打分，避免仅凭优先级取前 N 条
        candidate_limit = max(limit * 8, 50)
        stmt = (
            select(CoreMemoryPO)
            .where(CoreMemoryPO.user_id == user_id)
            .order_by(desc(CoreMemoryPO.priority), desc(CoreMemoryPO.last_used_at))
            .limit(candidate_limit)
        )
        result = await self.session.execute(stmt)
        candidates = result.scalars().all()

        tokens = self._tokenize_query(query_text)
        scored = [
            (self._score_core_memory(po, tokens) * (1 + po.priority / 5.0), po)
            for po in candidates
        ]
        hits = sorted([item for item in scored if item[0] > 0], key=lambda x: x[0], reverse=True)

        # 无实质命中时回退为优先级排序，且不计入使用次数
        if not hits:
            return [self._core_to_entity(po) for po in candidates[:limit]]

        matched_pos = [po for _, po in hits[:limit]]
        now = datetime.now(timezone.utc)
        for po in matched_pos:
            po.use_count += 1
            po.last_used_at = now

        await self.session.commit()
        return [self._core_to_entity(po) for po in matched_pos]

    async def save_long_term_memory(self, memory: LongTermMemory) -> LongTermMemory:
        stmt = select(LongTermMemoryPO).where(
            LongTermMemoryPO.user_id == memory.user_id,
            LongTermMemoryPO.memory_type == memory.memory_type,
            LongTermMemoryPO.memory_key == memory.memory_key,
        )
        result = await self.session.execute(stmt)
        po = result.scalars().first()

        if po:
            if (po.content or "") == memory.content:
                # 同一事实被反复确认：置信度平滑逼近上限
                old_confidence = float(po.confidence)
                new_confidence = min(0.99, old_confidence + (1.0 - old_confidence) * 0.15)
            else:
                # 内容发生实质变化，视为旧结论被修正，回退到本次提供的初始置信度
                new_confidence = memory.confidence
            po.content = memory.content
            po.keywords = memory.keywords
            if memory.source_role:
                po.source_role = memory.source_role
            po.confidence = Decimal(str(round(new_confidence, 4)))
            po.hit_count += 1
            po.updated_at = datetime.now(timezone.utc)
        else:
            po = LongTermMemoryPO(
                user_id=memory.user_id,
                session_id=memory.session_id,
                memory_type=memory.memory_type,
                memory_key=memory.memory_key,
                content=memory.content,
                keywords=memory.keywords,
                source_role=memory.source_role,
                confidence=Decimal(str(memory.confidence)),
                hit_count=1,
            )
            self.session.add(po)

        await self.session.commit()
        return self._long_to_entity(po)

    async def get_long_term_memories(
        self,
        user_id: str,
        memory_type: str | None = None,
        limit: int = 50,
    ) -> list[LongTermMemory]:
        stmt = select(LongTermMemoryPO).where(LongTermMemoryPO.user_id == user_id)
        if memory_type:
            stmt = stmt.where(LongTermMemoryPO.memory_type == memory_type)
        stmt = stmt.order_by(
            desc(LongTermMemoryPO.confidence), desc(LongTermMemoryPO.updated_at)
        ).limit(limit)
        result = await self.session.execute(stmt)
        return [self._long_to_entity(po) for po in result.scalars().all()]
