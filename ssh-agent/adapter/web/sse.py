"""
Web 适配层 - SSE 流式响应管道
将 ReAct 推演、普通对话与断线接续的流式编排从路由函数中下沉，路由只做协议入口转换。
"""

import asyncio
import json
import logging

from fastapi import HTTPException, Request, status
from fastapi.responses import StreamingResponse

from adapter.composition_root import container
from adapter.web.schemas import AgentChatRequest, ReActRequest
from application.usecases.chat import ChatWithOpsAgentUseCase
from application.usecases.react import ReActUseCase

logger = logging.getLogger("smartops.web")


async def build_agent_chat_response(
    identifier: str,
    req: AgentChatRequest,
    use_case: ChatWithOpsAgentUseCase,
) -> StreamingResponse:
    """普通 OpsAgent 对话 SSE 流式响应"""
    terminal_buf = req.terminal_buffer
    if not terminal_buf or len(terminal_buf.strip()) < 10:
        active_buf = container.terminal_session_manager.get_recent_buffer(identifier)
        if active_buf:
            terminal_buf = active_buf

    async def sse_stream():
        try:
            async for chunk in use_case.execute(
                identifier,
                req.prompt,
                terminal_buf,
                task_id=req.task_id,
                permission_mode=req.permission_mode,
                session_id=req.session_id,
            ):
                yield f"data: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(sse_stream(), media_type="text/event-stream")


async def build_react_stream_response(
    identifier: str,
    req: ReActRequest,
    request: Request,
    use_case: ReActUseCase,
) -> StreamingResponse:
    """ReAct 闭环排障 SSE 流式响应（含会话互斥锁、断线续跑与事件总线）"""
    terminal_buf = req.terminal_buffer
    if not terminal_buf or len(terminal_buf.strip()) < 10:
        active_buf = container.terminal_session_manager.get_recent_buffer(identifier)
        if active_buf:
            terminal_buf = active_buf

    # 1. 任务唯一标识与并发互斥锁：防止同一会话被并发触发导致指令竞态冲突
    task_key = f"{identifier}:{req.session_id}" if req.session_id else identifier
    if not container.session_run_guard.try_acquire(task_key):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该会话已有排障任务正在运行，请等待完成或先取消后再试。",
        )

    # 2. 初始化任务取消信号量：清理上一轮残留的取消标记，确保本次推演正常起跑
    task_cancel_event = container.task_cancellation_manager.get_or_create_cancel_event(task_key)
    task_cancel_event.clear()

    # 3. 复合型任务取消判定器：供 ReAct 引擎在每一步推演（思考/执行/反思）前后周期性回调
    async def combined_cancel_check() -> bool:
        # (1) 业务级主动取消：用户点击前端“中止”按钮，或主机发起了全局中止广播
        if task_cancel_event.is_set() or container.task_cancellation_manager.is_cancelled(identifier):
            return True
        # (2) 传输层断开取消（按需触发）：仅在显式开启 cancel_on_disconnect 时，检测客户端离线即终止；
        #     默认保持后台持续推演，用户重新连接后可通过事件总线接续查看排障结果
        if req.cancel_on_disconnect:
            try:
                if await request.is_disconnected():
                    return True
            except Exception:
                pass
        return False

    async def sse_react_stream():
        """
        SSE 响应流生成器：采用【生产者-消费者 + 传输层/任务层解耦】架构。
        - 生产者 (_run_task 作为独立后台协程)：负责执行 ReAct 推演状态图，不因 HTTP 暂时断连而中断。
        - 消费者 (当前生成器循环)：从队列消费事件并编码为 SSE 规范格式推流给前端。
        - 事件总线 (broker)：全量缓存推演事件，支持前端断网重连、页面刷新后的历史回放与无缝接续。
        """
        has_emitted_error = False
        task_emitted_error = False
        event_queue: asyncio.Queue = asyncio.Queue()
        guard_released = False

        def _release_guard_once() -> None:
            """安全幂等地释放会话级互斥锁，确保仅释放一次"""
            nonlocal guard_released
            if not guard_released:
                guard_released = True
                container.session_run_guard.release(task_key)

        async def _run_task():
            """后台工作协程 (生产者)：驱动 ReAct 引擎并双发事件至【事件总线】与【内存队列】"""
            nonlocal task_emitted_error
            # 注册运行期事件总线：浏览器刷新/断线后可重新接续并按序号回放历史事件
            broker = container.run_event_broker_registry.create(task_key)
            try:
                async for event in use_case.execute(
                    identifier=identifier,
                    user_prompt=req.prompt,
                    images=req.images,
                    terminal_buffer=terminal_buf,
                    session_id=req.session_id,
                    max_steps=req.max_steps,
                    max_run_seconds=req.max_run_seconds,
                    max_llm_calls=req.max_llm_calls,
                    max_tokens=req.max_tokens,
                    pre_planned_steps=req.pre_planned_steps,
                    cancel_check=combined_cancel_check,
                    permission_mode=req.permission_mode,
                    approved_commands=req.approved_commands,
                    branch_from_session_id=req.branch_from_session_id,
                    branch_step_idx=req.branch_step_idx,
                    branch_title=req.branch_title,
                ):
                    if isinstance(event, dict) and event.get("type") == "error":
                        task_emitted_error = True
                    # 双发策略：1. 入总线持久化供断线续跑；2. 入内存队列实时推流
                    broker.publish(event)
                    await event_queue.put(event)
            except Exception as exc:
                logger.exception("ReAct execution failed in background task: %s", exc)
                if not task_emitted_error:
                    error_event = {"type": "error", "error": str(exc)}
                    broker.publish(error_event)
                    await event_queue.put(error_event)
            finally:
                broker.finish()
                # 放入 None 作为队列哨兵值，通知消费者推流循环已结束
                await event_queue.put(None)
                container.task_cancellation_manager.unregister(task_key)
                # 后台任务真正结束后才释放会话锁；若客户端断开但任务在后台继续，不提前放锁
                _release_guard_once()

        # 启动独立后台工作协程，解耦 HTTP 连接生命周期与后台业务生命周期
        background_task = asyncio.create_task(_run_task())

        try:
            while True:
                # 检查网络连接状态：
                if req.cancel_on_disconnect:
                    # 模式 A：严格随网络连接生命周期终止任务
                    try:
                        if await request.is_disconnected():
                            logger.info("Client disconnected with cancel_on_disconnect=True, cancelling task for %s", identifier)
                            container.task_cancellation_manager.cancel_task(task_key)
                            break
                    except Exception:
                        pass
                else:
                    # 模式 B (默认)：断网仅退出 SSE 推流，后台推演任务继续独立运行 (支持断线续跑)
                    try:
                        if await request.is_disconnected():
                            logger.info("Client disconnected, background task continues for %s", identifier)
                            break
                    except Exception:
                        pass

                # 从事件队列非阻塞/带超时获取新事件（0.5s 超时以便定期轮询网络连接状态与取消信号）
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if await combined_cancel_check():
                        break
                    continue

                # 收到 None 哨兵值表示后台任务已全部完成
                if event is None:
                    break

                if isinstance(event, dict) and event.get("type") == "error":
                    has_emitted_error = True
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            # 发送 SSE 标准结束标记
            yield "data: [DONE]\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            # 客户端主动断开连接（如切换页面或关闭浏览器）
            logger.info("SSE ReAct stream ended due to disconnection/cancellation for %s", identifier)
            if req.cancel_on_disconnect:
                container.task_cancellation_manager.cancel_task(task_key)
                background_task.cancel()
        except Exception as exc:
            logger.exception("ReAct SSE streaming failed: %s", exc)
            if not has_emitted_error:
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)}, ensure_ascii=False)}\n\n"
        finally:
            if req.cancel_on_disconnect and not background_task.done():
                background_task.cancel()
            # 仅当后台任务真正完成时释放会话互斥锁；后台仍在续跑时不提前释放
            if background_task.done():
                _release_guard_once()

    return StreamingResponse(sse_react_stream(), media_type="text/event-stream")


def build_attach_stream_response(
    identifier: str,
    session_id: str | None,
    after: int,
) -> StreamingResponse:
    """接续服务端后台运行的排障任务：回放缓冲事件并持续推送新事件"""
    task_key = f"{identifier}:{session_id}" if session_id else identifier
    broker = container.run_event_broker_registry.get(task_key)
    if broker is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="没有可接续的排障任务，可能已执行结束。",
        )

    async def sse_attach_stream():
        try:
            async for event in broker.subscribe(after=after):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            logger.info("SSE attach stream ended for %s", task_key)
            return
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse_attach_stream(), media_type="text/event-stream")
