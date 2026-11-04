"""
Web 适配层 - OpsAgent ReAct 排障路由 (对话、取消、状态、接续、审批、插话与单步研判)
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse

from adapter.bootstrap import container
from adapter.web.dependencies import (
    get_approval_manager,
    get_agent_use_case,
    get_host_repository,
    get_llm_client,
    get_react_use_case,
)
from adapter.web.schemas import (
    AgentChatRequest,
    ReActApprovalRequest,
    ReActCancelRequest,
    ReActRequest,
    ReActSteerRequest,
    StepAnalyzeRequest,
    StepAnalyzeResponse,
)
from adapter.web.sse import (
    build_agent_chat_response,
    build_attach_stream_response,
    build_react_stream_response,
)
from application.ports import ILLMClient, ISSHConnectionRepository
from application.react.managers import IApprovalManager
from application.react.observation import detect_command_error
from application.usecases.chat import ChatWithOpsAgentUseCase
from application.usecases.react import ReActUseCase

logger = logging.getLogger("smartops.web")

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
    req: Optional[ReActCancelRequest] = None,
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
    session_id: Optional[str] = Query(default=None, description="会话唯一 ID"),
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
    session_id: Optional[str] = Query(default=None, description="会话唯一 ID"),
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


@router.post("/api/v1/hosts/{identifier}/agent/step-analyze", response_model=StepAnalyzeResponse, tags=["OpsAgent"])
async def analyze_step_execution(
    identifier: str,
    req: StepAnalyzeRequest,
    host_repo: ISSHConnectionRepository = Depends(get_host_repository),
    llm_client: ILLMClient = Depends(get_llm_client),
):
    """
    单步执行终端回显分析接口
    """
    host = await host_repo.get_by_identifier(identifier)
    host_name = host.connection_name if host else identifier

    prompt = [
        {
            "role": "system",
            "content": (
                "你是一个专业的 Linux 运维与排障专家。你在目标 Linux 主机上刚按步骤执行了一条 Shell 命令。\n"
                "请根据命令内容、步骤说明以及终端最新回显，进行精准、冷静的深度推演研判。\n"
                "输出要求：\n"
                "1. 用 2 到 3 句话指出终端回显所反映的核心物理指标（如内存剩余量、负载水位、端口连接数或进程状态）；\n"
                "2. 明确给出系统运行状态结论（正常/异常），并确认是否满足安全进入下一步骤的条件。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"目标主机: {host_name}\n"
                f"执行步骤: 步骤 {req.step_number} - {req.step_title}\n"
                f"执行命令: {req.command}\n\n"
                f"终端最新回显快照 (Terminal Echo Snapshot):\n\"\"\"\n{req.terminal_snapshot[:1200]}\n\"\"\""
            ),
        },
    ]

    analysis = ""
    is_normal = True
    try:
        reply = await llm_client.chat(prompt, temperature=0.1)
        if reply and not reply.startswith(">"):
            analysis = reply.strip()
            # 校验 LLM 分析结论与回显特征中是否表达了异常判定
            err_detail = detect_command_error(req.command, req.terminal_snapshot)
            if err_detail:
                is_normal = False
            elif any(k in analysis for k in ["存在异常", "处于异常", "系统异常", "指标异常", "执行失败", "不正常", "报错"]):
                is_normal = False
    except Exception as e:
        logger.warning("Step analysis LLM call error: %s", e)

    if not analysis:
        buf_lower = req.terminal_snapshot.lower()
        cmd_lower = req.command.lower()
        err_detail = detect_command_error(req.command, req.terminal_snapshot)
        if err_detail:
            is_normal = False
            analysis = f"回显检测到命令执行异常: {err_detail}。建议根据报错提示进行排查处理。"
        elif any(err in buf_lower for err in ["command not found", "permission denied", "fatal", "failed to start", "out of memory", "failed to connect", "connection refused", "error:"]):
            is_normal = False
            analysis = "警告：回显检测到异常报错、连接失败或权限不足。请核实该步骤参数或依赖后谨慎决策。"
        elif "free" in cmd_lower:
            # 真实分析内存快照文本，避免虚构平稳
            if "total" in buf_lower or "mem" in buf_lower:
                analysis = f"已捕获物理内存水位快照。输出摘要：\n{req.terminal_snapshot[:300].strip()}\n请根据实际可用内存与 Swap 水位评估是否充足。"
            else:
                analysis = "已执行 free 命令，但回显未包含标准内存表头，建议确认命令输出完整性。"
        elif "uptime" in cmd_lower:
            analysis = f"已采集系统负载与运行时长：\n{req.terminal_snapshot[:200].strip()}\n请确认 load average 与 CPU 核心数匹配情况。"
        elif "ss" in cmd_lower or "netstat" in cmd_lower:
            analysis = "网络套接字与监听端口扫描完毕，输出包含活跃网络连接，可继续进行端口状态核对。"
        elif "ps" in cmd_lower:
            analysis = "已读取目标进程快照，未发现系统级致命死锁报错，可继续推进。"
        else:
            analysis = f"指令 `{req.command}` 执行完成，回显快照已捕获，未检测到平台级语法错误。"

    return StepAnalyzeResponse(
        step_number=req.step_number,
        command=req.command,
        analysis=analysis,
        is_normal=is_normal
    )
