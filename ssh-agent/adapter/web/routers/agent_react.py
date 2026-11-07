"""
Web 适配层 - OpsAgent ReAct 排障路由 (对话、取消、状态、接续、审批与插话)
"""

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse

from adapter.composition_root import container
from adapter.web.factory import (
    get_approval_manager,
    get_agent_use_case,
    get_react_use_case,
)
from adapter.web.schemas import (
    AgentChatRequest,
    ReActApprovalRequest,
    ReActCancelRequest,
    ReActRequest,
    ReActSteerRequest,
)
from adapter.web.sse import (
    build_agent_chat_response,
    build_attach_stream_response,
    build_react_stream_response,
)
from application.react.managers import IApprovalManager
from application.usecases.chat import ChatWithOpsAgentUseCase
from application.usecases.react import ReActUseCase

router = APIRouter()


@router.post("/api/v1/hosts/{identifier}/agent/chat", tags=["OpsAgent"])
async def agent_chat(
    identifier: str,
    req: AgentChatRequest,
    use_case: ChatWithOpsAgentUseCase = Depends(get_agent_use_case),
):
    """
    OpsAgent SSE 流式响应端点
    identifier 支持：host_id、主机名或 IP
    问答多轮历史持久化保存至 chat_session 与 chat_message 中。
    """
    return await build_agent_chat_response(identifier, req, use_case)


@router.post("/api/v1/hosts/{identifier}/agent/react/cancel", tags=["OpsAgent"])
async def cancel_react_agent_task(
    identifier: str,
    req: ReActCancelRequest | None = None,
):
    """
    主动显式取消正在执行的 ReAct 运维任务
    将向正在运行的 PTY 终端注入 Ctrl+C 中断信号，并立即终止状态机推演
    """
    keys = [identifier]
    if req and req.session_id:
        keys.append(f"{identifier}:{req.session_id}")
        keys.append(req.session_id)
    cancelled = False
    for k in keys:
        if container.task_cancellation_manager.cancel_task(k):
            cancelled = True
    return {"status": "ok", "cancelled": cancelled, "task_keys": keys}


@router.get("/api/v1/hosts/{identifier}/agent/react/status", tags=["OpsAgent"])
async def get_react_agent_status(
    identifier: str,
    session_id: str | None = Query(default=None, description="会话唯一 ID"),
):
    """
    查询指定会话是否仍有排障任务在服务端后台运行
    供浏览器刷新/断线后判断是否需要重新接续实时事件流
    """
    task_key = f"{identifier}:{session_id}" if session_id else identifier
    return {
        "status": "ok",
        "task_key": task_key,
        "session_id": session_id,
        "running": container.run_event_broker_registry.is_running(task_key),
    }


@router.get("/api/v1/hosts/{identifier}/agent/react/stream", tags=["OpsAgent"])
async def attach_react_agent_stream(
    identifier: str,
    session_id: str | None = Query(default=None, description="会话唯一 ID"),
    after: int = Query(default=0, ge=0, description="从第几个缓冲事件之后开始回放（0 表示从头回放）"),
):
    """
    接续服务端仍在后台运行的排障任务：回放缓冲事件并持续推送新事件。
    客户端刷新页面或网络短暂中断后可通过该端点恢复实时进度，避免任务被重复发起。
    """
    return build_attach_stream_response(identifier, session_id, after)


@router.post("/api/v1/hosts/{identifier}/agent/react", tags=["OpsAgent"])
async def react_agent_chat(
    identifier: str,
    req: ReActRequest,
    request: Request,
    use_case: ReActUseCase = Depends(get_react_use_case),
):
    """
    自治 ReAct 闭环排障流式端点 (由 LangGraph 状态图驱动)
    实现 Thought -> Audit -> Action (实时注入PTY) -> Observation -> Reflection -> Next Step
    """
    return await build_react_stream_response(identifier, req, request, use_case)


@router.post("/api/v1/hosts/{identifier}/agent/react/approve", tags=["OpsAgent"])
async def submit_react_approval(
    identifier: str,
    req: ReActApprovalRequest,
    approval_manager: IApprovalManager = Depends(get_approval_manager),
):
    """
    提交 ReAct 高危命令人机审批决断
    用户在 Web 终端确认执行或拒绝执行高危命令时触发，唤醒挂起的 LangGraph ReAct 状态机
    """
    success = approval_manager.submit_decision(
        approval_id=req.approval_id,
        approved=req.approved,
        scope=req.scope or "once",
    )
    if not success:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"message": "Approval request not found, already resolved, or timed out."}
        )
    return {"status": "ok", "approval_id": req.approval_id, "approved": req.approved}


@router.post("/api/v1/hosts/{identifier}/agent/react/steer", tags=["OpsAgent"])
async def submit_react_steering(
    identifier: str,
    req: ReActSteerRequest,
    use_case: ReActUseCase = Depends(get_react_use_case),
):
    """
    提交 ReAct 即时插话/方向纠偏指令 (Steering Message)
    允许用户在排障推演中途动态注入纠偏指令，在下一个思考轮次自适应调整排障策略
    """
    success = use_case.push_steering_message(session_id=req.session_id, message=req.content)
    if not success:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Failed to push steering message or steering manager unavailable."},
        )
    return {"status": "ok", "session_id": req.session_id, "content": req.content}



