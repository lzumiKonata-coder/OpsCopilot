"""应用层 - 记忆系统用例与长期记忆读写辅助 (core_memory / long_term_memory)"""

import logging

from application.ports import IMemoryRepository
from domain.entities import CoreMemory, LongTermMemory

logger = logging.getLogger("smartops.application.usecases.memory")


async def _load_long_term_memory_text(
    repo: IMemoryRepository | None,
    user_id: str,
    host_id: str | None = None,
    limit: int = 10,
) -> str:
    """读取用户长期记忆并格式化为提示词片段；过滤宿主机隔离；异常时降级为空，不影响主流程。"""
    if not repo:
        return ""
    try:
        memories = await repo.get_long_term_memories(user_id=user_id or "default", limit=limit * 2)
    except Exception as exc:
        logger.warning("Failed to retrieve long-term memories: %s", exc)
        return ""
    if not memories:
        return ""
    filtered = []
    for m in memories:
        if host_id:
            if m.memory_key.startswith(f"{host_id}:"):
                filtered.append(m)
            elif ":" in m.memory_key:
                # 包含其他主机前缀的长期记忆，予以隔离剔除
                continue
            else:
                filtered.append(m)
        else:
            filtered.append(m)
        if len(filtered) >= limit:
            break
    if not filtered:
        return ""
    lines = [f"- [{m.memory_type}] {m.memory_key}: {m.content}" for m in filtered]
    return "\n\n# 6. 长期记忆 (Long-term Memory)\n" + "\n".join(lines)


async def _save_long_term_memory(
    repo: IMemoryRepository | None,
    user_id: str,
    memory_type: str,
    memory_key: str,
    content: str,
    keywords: str = "",
    confidence: float = 0.6,
    session_id: str | None = None,
    source_role: str = "assistant",
) -> None:
    """按 (user_id, memory_type, memory_key) 幂等落库长期记忆；失败仅告警，绝不阻断主流程。"""
    if not repo or not content.strip():
        return
    try:
        await repo.save_long_term_memory(
            LongTermMemory(
                user_id=user_id or "default",
                session_id=session_id,
                memory_type=memory_type,
                memory_key=memory_key,
                content=content.strip()[:1000],
                keywords=keywords or None,
                source_role=source_role,
                confidence=confidence,
            )
        )
    except Exception as exc:
        logger.warning("Failed to save long-term memory (%s/%s): %s", memory_type, memory_key, exc)


class ManageMemoryUseCase:
    """用例：管理核心记忆与长期记忆"""

    def __init__(self, memory_repo: IMemoryRepository):
        self.memory_repo = memory_repo

    async def add_core_memory(
        self,
        title: str,
        content: str,
        category: str = "Rule",
        scope: str = "user",
        keywords: str | None = None,
        priority: int = 3,
        user_id: str = "default",
    ) -> CoreMemory:
        mem = CoreMemory(
            user_id=user_id,
            scope=scope,  # type: ignore
            category=category,  # type: ignore
            title=title,
            keywords=keywords,
            content=content,
            priority=priority,
        )
        return await self.memory_repo.save_core_memory(mem)

    async def list_core_memories(
        self,
        user_id: str = "default",
        category: str | None = None,
        limit: int = 20,
    ) -> list[CoreMemory]:
        return await self.memory_repo.list_core_memories(user_id=user_id, category=category, limit=limit)

    async def list_long_term_memories(
        self,
        user_id: str = "default",
        memory_type: str | None = None,
        limit: int = 50,
    ) -> list[LongTermMemory]:
        return await self.memory_repo.get_long_term_memories(
            user_id=user_id, memory_type=memory_type, limit=limit
        )
