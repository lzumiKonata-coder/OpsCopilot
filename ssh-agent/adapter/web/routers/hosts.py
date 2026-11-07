"""
Web 适配层 - 主机资产管理路由 (ssh_connection / ssh_connection_config)
"""


from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from adapter.web.factory import get_host_repository, get_ssh_gateway
from adapter.web.schemas import HostResponse, RegisterHostRequest
from application.ports import ISSHConnectionRepository, ISSHGateway
from application.usecases.host import RegisterSSHConnectionUseCase
from domain.entities import SSHConnection, SSHConnectionConfig
from domain.rules import AuthType

router = APIRouter()


@router.post("/api/v1/hosts", response_model=HostResponse, status_code=status.HTTP_201_CREATED, tags=["Host Management"])
async def register_host(
    request: RegisterHostRequest,
    host_repo: ISSHConnectionRepository = Depends(get_host_repository),
):
    """
    1. 注册目标主机资产 (支持 ssh_connection 与高级配置表)
    """
    use_case = RegisterSSHConnectionUseCase(host_repo)

    clean_name = request.connection_name or request.name or "host"
    clean_host = request.host or request.ip or "127.0.0.1"

    clean_password = request.password if request.auth_type == AuthType.PASSWORD else None
    clean_private_key = request.private_key if request.auth_type == AuthType.PRIVATE_KEY else None
    clean_passphrase = request.passphrase if request.auth_type == AuthType.PRIVATE_KEY else None

    if request.auth_type == AuthType.PASSWORD and request.private_key == "string":
        clean_private_key = None
        clean_passphrase = None

    connection = SSHConnection(
        connection_name=clean_name,
        host=clean_host,
        port=request.port,
        username=request.username,
        auth_type=request.auth_type,
        password=clean_password,
        private_key=clean_private_key,
        passphrase=clean_passphrase,
        user_id=request.user_id,
        config=SSHConnectionConfig(
            connect_timeout=request.connection_timeout,
            keepalive_interval=request.keepalive_interval,
            strict_host_key_check=request.strict_host_key_check,
            known_hosts=request.known_hosts,
        ),
    )
    saved_host = await use_case.execute(connection)
    return HostResponse(
        host_id=saved_host.connection_id,
        connection_id=saved_host.connection_id,
        name=saved_host.connection_name,
        connection_name=saved_host.connection_name,
        ip=saved_host.host,
        host=saved_host.host,
        port=saved_host.port,
        username=saved_host.username,
        auth_type=saved_host.auth_type,
        connection_timeout=saved_host.config.connect_timeout,
        strict_host_key_check=saved_host.config.strict_host_key_check,
        known_hosts=saved_host.config.known_hosts,
        status=saved_host.status,
        user_id=saved_host.user_id,
    )


@router.get("/api/v1/hosts", response_model=list[HostResponse], tags=["Host Management"])
async def list_hosts(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    host_repo: ISSHConnectionRepository = Depends(get_host_repository),
):
    """获取所有已登记的主机资产列表"""
    hosts = await host_repo.list_all(skip=skip, limit=limit)
    return [
        HostResponse(
            host_id=h.connection_id,
            connection_id=h.connection_id,
            name=h.connection_name,
            connection_name=h.connection_name,
            ip=h.host,
            host=h.host,
            port=h.port,
            username=h.username,
            auth_type=h.auth_type,
            connection_timeout=h.config.connect_timeout,
            status=h.status,
            user_id=h.user_id,
        )
        for h in hosts
    ]


@router.get("/api/v1/hosts/{identifier}", response_model=HostResponse, tags=["Host Management"])
async def get_host(
    identifier: str,
    host_repo: ISSHConnectionRepository = Depends(get_host_repository),
):
    """
    获取单个主机资产详情
    """
    host = await host_repo.get_by_identifier(identifier)
    if not host:
        raise HTTPException(status_code=404, detail=f"Host '{identifier}' not found.")
    return HostResponse(
        host_id=host.connection_id,
        connection_id=host.connection_id,
        name=host.connection_name,
        connection_name=host.connection_name,
        ip=host.host,
        host=host.host,
        port=host.port,
        username=host.username,
        auth_type=host.auth_type,
        connection_timeout=host.config.connect_timeout,
        status=host.status,
        user_id=host.user_id,
    )


@router.delete("/api/v1/hosts/{identifier}", tags=["Host Management"])
async def delete_host(
    identifier: str,
    host_repo: ISSHConnectionRepository = Depends(get_host_repository),
):
    """软删除指定主机 (deleted=1)"""
    host = await host_repo.get_by_identifier(identifier)
    if not host:
        raise HTTPException(status_code=404, detail=f"Host '{identifier}' not found.")
    await host_repo.delete(host.connection_id)
    return {"deleted": True, "connection_id": host.connection_id}


@router.post("/api/v1/hosts/{identifier}/test", tags=["Host Management"])
async def test_host_connection(
    identifier: str,
    host_repo: ISSHConnectionRepository = Depends(get_host_repository),
    ssh_gateway: ISSHGateway = Depends(get_ssh_gateway),
):
    """
    测试目标主机 SSH 连通性与凭据正确性
    """
    host = await host_repo.get_by_identifier(identifier)
    if not host:
        raise HTTPException(status_code=404, detail=f"Host '{identifier}' not found.")

    is_connected = await ssh_gateway.test_connection(host)
    if not is_connected:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "connected": False,
                "host_id": host.connection_id,
                "target": f"{host.username}@{host.host}:{host.port}",
                "message": "Failed to connect to remote host. Please check network, IP, port, or SSH credentials.",
            },
        )
    return {
        "connected": True,
        "host_id": host.connection_id,
        "target": f"{host.username}@{host.host}:{host.port}",
        "message": "SSH connection and authentication test succeeded.",
    }
