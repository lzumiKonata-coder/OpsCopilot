"""
Web 适配层 - 交互式 WebSSH 伪终端路由 (ssh_session_log 全链路追踪)
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status

from adapter.composition_root import container
from adapter.web.factory import (
    get_host_repository,
    get_record_ssh_session_log_use_case,
    get_terminal_gateway,
)
from application.ports import ISSHConnectionRepository, ISSHTerminalGateway
from application.usecases.ssh_session import RecordSSHSessionLogUseCase
from domain.entities import SSHSessionLog

logger = logging.getLogger("smartops.web")

router = APIRouter()


@router.websocket("/ws/hosts/{identifier}/terminal")
async def websocket_terminal_endpoint(
    websocket: WebSocket,
    identifier: str,
    cols: int = Query(100, ge=20, le=300),
    rows: int = Query(30, ge=10, le=150),
    host_repo: ISSHConnectionRepository = Depends(get_host_repository),
    terminal_gateway: ISSHTerminalGateway = Depends(get_terminal_gateway),
    session_log_use_case: RecordSSHSessionLogUseCase = Depends(get_record_ssh_session_log_use_case),
):
    """
    WebSocket 交互式终端通道 (全链路记录 ssh_session_log)
    """
    await websocket.accept()

    host = await host_repo.get_by_identifier(identifier)
    if not host:
        await websocket.send_text(f"\r\n\x1b[31;1m[SmartOps Error] Target host '{identifier}' not found in repository.\x1b[0m\r\n")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 记录 SSH 终端会话开启 (ssh_session_log)
    client_ip = websocket.client.host if websocket.client else "unknown"
    session_log_entry: SSHSessionLog | None = None
    try:
        session_log_entry = await session_log_use_case.open_session(
            connection_id=host.connection_id,
            remote_addr=client_ip,
            user_id=host.user_id,
        )
    except Exception as log_err:
        logger.warning("Failed to record ssh_session_log start: %s", log_err)

    try:
        conn, process = await terminal_gateway.open_terminal(host, cols=cols, rows=rows)
    except Exception as exc:
        logger.error("Failed to establish PTY shell session for %s: %s", host.connection_name, str(exc))
        if session_log_entry:
            await session_log_use_case.close_session(session_log_entry.session_id, error_msg=str(exc))
        await websocket.send_text(f"\r\n\x1b[31;1m[SmartOps SSH Connect Failed] {str(exc)}\x1b[0m\r\n")
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    # 注册到活跃终端会话管理器，支持后端 ReActUseCase 联动在左侧屏幕敲字与感知
    container.terminal_session_manager.register(
        identifier=identifier,
        process=process,
        aliases=[host.connection_id, host.connection_name, host.host],
    )

    # 并发协程 1: SSH PTY stdout -> 推送给前端浏览器并同步给终端会话管理器
    async def ssh_to_ws():
        try:
            while not process.stdout.at_eof():
                data = await process.stdout.read(1024)
                if not data:
                    break
                container.terminal_session_manager.on_stdout_chunk(identifier, data)
                await websocket.send_text(data)
        except Exception:
            pass

    # 并发协程 2: 浏览器 WebSocket 键盘输入/窗口尺寸 -> 写入 SSH PTY stdin
    async def ws_to_ssh():
        try:
            while True:
                msg = await websocket.receive_text()
                try:
                    payload = json.loads(msg)
                    if isinstance(payload, dict):
                        if payload.get("type") == "input":
                            process.stdin.write(payload.get("data", ""))
                        elif payload.get("type") == "resize":
                            new_cols = payload.get("cols", cols)
                            new_rows = payload.get("rows", rows)
                            try:
                                process.change_terminal_size(width=new_cols, height=new_rows)
                            except Exception:
                                pass
                        continue
                except (json.JSONDecodeError, TypeError):
                    pass

                process.stdin.write(msg)
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception:
            pass

    try:
        done, pending = await asyncio.wait(
            [asyncio.create_task(ssh_to_ws()), asyncio.create_task(ws_to_ssh())],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    finally:
        # 从活跃终端会话管理器注销
        container.terminal_session_manager.unregister(identifier)

        # 关闭终端会话审计记录 (ssh_session_log status=1)
        if session_log_entry:
            try:
                await session_log_use_case.close_session(session_log_entry.session_id)
            except Exception as close_log_err:
                logger.warning("Failed to close ssh_session_log: %s", close_log_err)

        try:
            process.terminate()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
