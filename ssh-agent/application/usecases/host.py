"""应用层 - 主机资产用例 (ssh_connection / ssh_connection_config)"""


from application.ports import ISSHConnectionRepository
from domain.entities import SSHConnection


class RegisterSSHConnectionUseCase:
    """用例：注册或更新主机资产与连接配置"""

    def __init__(self, host_repo: ISSHConnectionRepository):
        self.host_repo = host_repo

    async def execute(self, host: SSHConnection) -> SSHConnection:
        host.validate_credentials()
        return await self.host_repo.save(host)


class GetSSHConnectionUseCase:
    """用例：获取单个主机资产详情"""

    def __init__(self, host_repo: ISSHConnectionRepository):
        self.host_repo = host_repo

    async def execute(self, connection_id: str) -> SSHConnection | None:
        return await self.host_repo.get_by_id(connection_id)


class ListSSHConnectionsUseCase:
    """用例：分页获取主机资产列表"""

    def __init__(self, host_repo: ISSHConnectionRepository):
        self.host_repo = host_repo

    async def execute(self, skip: int = 0, limit: int = 100) -> list[SSHConnection]:
        return await self.host_repo.list_all(skip=skip, limit=limit)


class DeleteSSHConnectionUseCase:
    """用例：删除主机资产 (软删除 deleted=1)"""

    def __init__(self, host_repo: ISSHConnectionRepository):
        self.host_repo = host_repo

    async def execute(self, connection_id: str) -> bool:
        return await self.host_repo.delete(connection_id)
