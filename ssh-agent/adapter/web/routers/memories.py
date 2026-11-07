"""
Web 适配层 - 核心记忆与长期记忆路由 (core_memory / long_term_memory)
"""


from fastapi import APIRouter, Depends, Query, status

from adapter.web.factory import get_memory_repository
from adapter.web.schemas import (
    CoreMemoryResponse,
    CreateCoreMemoryRequest,
    LongTermMemoryResponse,
)
from application.ports import IMemoryRepository
from application.usecases.memory import ManageMemoryUseCase

router = APIRouter()


@router.get("/api/v1/memories/long-term", response_model=list[LongTermMemoryResponse], tags=["Memory Management"])
async def list_long_term_memories(
    user_id: str = Query("default"),
    memory_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    memory_repo: IMemoryRepository = Depends(get_memory_repository),
):
    """查询长期记忆库 (long_term_memory 表)"""
    use_case = ManageMemoryUseCase(memory_repo)
    memories = await use_case.list_long_term_memories(
        user_id=user_id, memory_type=memory_type, limit=limit
    )
    return [
        LongTermMemoryResponse(
            id=m.id,
            user_id=m.user_id,
            session_id=m.session_id,
            memory_type=m.memory_type,
            memory_key=m.memory_key,
            content=m.content,
            keywords=m.keywords,
            confidence=m.confidence,
            hit_count=m.hit_count,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )
        for m in memories
    ]


@router.get("/api/v1/memories", response_model=list[CoreMemoryResponse], tags=["Memory Management"])
async def list_core_memories(
    user_id: str = Query("default"),
    category: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    memory_repo: IMemoryRepository = Depends(get_memory_repository),
):
    """查询核心记忆库 (core_memory 表)"""
    use_case = ManageMemoryUseCase(memory_repo)
    memories = await use_case.list_core_memories(user_id=user_id, category=category, limit=limit)
    return [
        CoreMemoryResponse(
            id=m.id,
            user_id=m.user_id,
            scope=m.scope,
            category=m.category,
            title=m.title,
            keywords=m.keywords,
            content=m.content,
            priority=m.priority,
            use_count=m.use_count,
            created_at=m.created_at,
            last_used_at=m.last_used_at,
        )
        for m in memories
    ]


@router.post("/api/v1/memories", response_model=CoreMemoryResponse, status_code=status.HTTP_201_CREATED, tags=["Memory Management"])
async def create_core_memory(
    req: CreateCoreMemoryRequest,
    memory_repo: IMemoryRepository = Depends(get_memory_repository),
):
    """新增核心记忆（用户偏好、业务规则、纠错规则等）"""
    use_case = ManageMemoryUseCase(memory_repo)
    m = await use_case.add_core_memory(
        title=req.title,
        content=req.content,
        category=req.category or "Rule",
        scope=req.scope or "user",
        keywords=req.keywords,
        priority=req.priority or 3,
        user_id=req.user_id or "default",
    )
    return CoreMemoryResponse(
        id=m.id,
        user_id=m.user_id,
        scope=m.scope,
        category=m.category,
        title=m.title,
        keywords=m.keywords,
        content=m.content,
        priority=m.priority,
        use_count=m.use_count,
        created_at=m.created_at,
        last_used_at=m.last_used_at,
    )
