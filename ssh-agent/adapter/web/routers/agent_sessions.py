"""
Web 适配层 - OpsAgent 会话管理、检查点与分支树路由
涉及 chat_session / chat_message / chat_milestone 及时间旅行能力。
"""


from fastapi import APIRouter, Depends, HTTPException, Query

from adapter.web.factory import (
    get_create_agent_session_use_case,
    get_delete_agent_session_use_case,
    get_get_agent_session_use_case,
    get_list_agent_sessions_use_case,
)
from adapter.web.schemas import (
    AgentSessionDetailResponse,
    AgentSessionSummaryResponse,
    ChatMessageDTO,
    ChatMilestoneDTO,
    CreateAgentSessionRequest,
)
from application.usecases.agent_session import (
    CreateAgentSessionUseCase,
    DeleteAgentSessionUseCase,
    GetAgentSessionUseCase,
    ListAgentSessionsUseCase,
)

router = APIRouter()


@router.get("/api/v1/hosts/{identifier}/agent/sessions", response_model=list[AgentSessionSummaryResponse], tags=["OpsAgent Sessions"])
async def list_agent_sessions(
    identifier: str,
    limit: int = Query(50, ge=1, le=100),
    use_case: ListAgentSessionsUseCase = Depends(get_list_agent_sessions_use_case),
):
    """查询指定受管主机的历史对话会话列表 (chat_session 表)"""
    sessions = await use_case.execute(identifier, limit=limit)
    return [
        AgentSessionSummaryResponse(
            session_id=s.id,
            id=s.id,
            host_id=s.agent_id,
            agent_id=s.agent_id,
            parent_session_id=s.parent_session_id,
            branch_step=s.branch_step,
            is_branch=bool(s.parent_session_id),
            title=s.title,
            message_count=s.message_count,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s in sessions
    ]


@router.post("/api/v1/hosts/{identifier}/agent/sessions", response_model=AgentSessionSummaryResponse, tags=["OpsAgent Sessions"])
async def create_agent_session(
    identifier: str,
    req: CreateAgentSessionRequest = CreateAgentSessionRequest(),
    use_case: CreateAgentSessionUseCase = Depends(get_create_agent_session_use_case),
):
    """为指定受管主机新建一个空会话 (chat_session 表)"""
    session = await use_case.execute(identifier, title=req.title or "新会话", user_id=req.user_id)
    return AgentSessionSummaryResponse(
        session_id=session.id,
        id=session.id,
        host_id=session.agent_id,
        agent_id=session.agent_id,
        parent_session_id=session.parent_session_id,
        branch_step=session.branch_step,
        is_branch=bool(session.parent_session_id),
        title=session.title,
        message_count=session.message_count,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.get("/api/v1/agent/sessions/{session_id}", response_model=AgentSessionDetailResponse, tags=["OpsAgent Sessions"])
async def get_agent_session_detail(
    session_id: str,
    use_case: GetAgentSessionUseCase = Depends(get_get_agent_session_use_case),
):
    """获取单个会话的完整历史消息与里程碑详情 (chat_message + chat_milestone 表)"""
    session = await use_case.execute(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Agent session '{session_id}' not found.")
    return AgentSessionDetailResponse(
        session_id=session.id,
        id=session.id,
        host_id=session.agent_id,
        agent_id=session.agent_id,
        parent_session_id=session.parent_session_id,
        branch_step=session.branch_step,
        is_branch=bool(session.parent_session_id),
        title=session.title,
        message_count=session.message_count,
        messages=[
            ChatMessageDTO(
                id=m.id,
                role=m.role,
                content=m.content,
                tool_name=m.tool_name,
                priority=m.priority,
                token_count=m.token_count,
                created_at=m.created_at,
            )
            for m in session.messages
        ],
        milestones=[
            ChatMilestoneDTO(
                id=ms.id,
                session_id=ms.session_id,
                type=ms.type,
                content=ms.content,
                created_at=ms.created_at,
            )
            for ms in session.milestones
        ],
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.delete("/api/v1/agent/sessions/{session_id}", tags=["OpsAgent Sessions"])
async def delete_agent_session(
    session_id: str,
    use_case: DeleteAgentSessionUseCase = Depends(get_delete_agent_session_use_case),
):
    """删除指定的对话会话及其下全部消息与里程碑"""
    success = await use_case.execute(session_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Agent session '{session_id}' not found.")
    return {"deleted": True, "session_id": session_id}




