"""适配层 - SSH 连接与配置仓储实现"""

from typing import List, Optional

from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from adapter.repository.models import SSHConnectionConfigPO, SSHConnectionPO
from adapter.security.crypto import decrypt_credential, encrypt_credential
from application.ports import ISSHConnectionRepository
from domain.entities import SSHConnection, SSHConnectionConfig
from domain.rules import AuthType


class SQLAlchemySSHConnectionRepository(ISSHConnectionRepository):
    """
    基于 SQLAlchemy 2.0 的 SSH 主机连接仓储实现
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _to_entity(po: SSHConnectionPO, cfg_po: Optional[SSHConnectionConfigPO] = None) -> SSHConnection:
        config = SSHConnectionConfig()
        if cfg_po:
            config = SSHConnectionConfig(
                connect_timeout=cfg_po.connect_timeout,
                keepalive_interval=cfg_po.keepalive_interval,
                startup_command=cfg_po.startup_command,
                compression=cfg_po.compression,
                strict_host_key_check=cfg_po.strict_host_key_check,
                known_hosts=cfg_po.known_hosts,
            )

        plain_password = decrypt_credential(po.password) if po.encrypted else po.password
        plain_key = decrypt_credential(po.private_key) if po.encrypted else po.private_key
        raw_passphrase = getattr(po, "passphrase", None)
        plain_passphrase = decrypt_credential(raw_passphrase) if po.encrypted else raw_passphrase

        return SSHConnection(
            connection_id=po.connection_id,
            connection_name=po.connection_name,
            host=po.host,
            port=po.port,
            username=po.username,
            auth_type=AuthType(po.auth_type),
            password=plain_password,
            private_key=plain_key,
            passphrase=plain_passphrase,
            encrypted=po.encrypted,
            status=po.status,
            user_id=po.user_id,
            deleted=po.deleted,
            config=config,
            created_at=po.created_at,
            updated_at=po.updated_at,
        )

    async def _get_config_po(self, connection_id: str) -> Optional[SSHConnectionConfigPO]:
        stmt = select(SSHConnectionConfigPO).where(SSHConnectionConfigPO.connection_id == connection_id)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def save(self, host: SSHConnection) -> SSHConnection:
        # 1. 保存/更新 ssh_connection 表
        stmt = select(SSHConnectionPO).where(SSHConnectionPO.connection_id == host.connection_id)
        result = await self.session.execute(stmt)
        po = result.scalars().first()

        auth_val = int(host.auth_type.value) if hasattr(host.auth_type, "value") else int(host.auth_type)
        enc_password = encrypt_credential(host.password)
        enc_private_key = encrypt_credential(host.private_key)
        enc_passphrase = encrypt_credential(host.passphrase)

        if po is None:
            po = SSHConnectionPO(
                connection_id=host.connection_id,
                connection_name=host.connection_name,
                host=host.host,
                port=host.port,
                username=host.username,
                auth_type=auth_val,
                password=enc_password,
                private_key=enc_private_key,
                passphrase=enc_passphrase,
                encrypted=1,
                status=host.status,
                user_id=host.user_id,
                deleted=host.deleted,
            )
            self.session.add(po)
        else:
            po.connection_name = host.connection_name
            po.host = host.host
            po.port = host.port
            po.username = host.username
            po.auth_type = auth_val
            po.password = enc_password
            po.private_key = enc_private_key
            po.passphrase = enc_passphrase
            po.encrypted = 1
            po.status = host.status
            po.user_id = host.user_id
            po.deleted = host.deleted

        # 2. 保存/更新 ssh_connection_config 表
        cfg_po = await self._get_config_po(host.connection_id)
        cfg = host.config or SSHConnectionConfig()
        if cfg_po is None:
            cfg_po = SSHConnectionConfigPO(
                connection_id=host.connection_id,
                connect_timeout=cfg.connect_timeout,
                keepalive_interval=cfg.keepalive_interval,
                startup_command=cfg.startup_command,
                compression=cfg.compression,
                strict_host_key_check=cfg.strict_host_key_check,
                known_hosts=cfg.known_hosts,
            )
            self.session.add(cfg_po)
        else:
            cfg_po.connect_timeout = cfg.connect_timeout
            cfg_po.keepalive_interval = cfg.keepalive_interval
            cfg_po.startup_command = cfg.startup_command
            cfg_po.compression = cfg.compression
            cfg_po.strict_host_key_check = cfg.strict_host_key_check
            cfg_po.known_hosts = cfg.known_hosts

        await self.session.commit()
        await self.session.refresh(po)
        if cfg_po:
            await self.session.refresh(cfg_po)
        return self._to_entity(po, cfg_po)

    async def get_by_id(self, connection_id: str) -> Optional[SSHConnection]:
        stmt = select(SSHConnectionPO).where(
            SSHConnectionPO.connection_id == connection_id,
            SSHConnectionPO.deleted == 0,
        )
        result = await self.session.execute(stmt)
        po = result.scalars().first()
        if not po:
            return None
        cfg_po = await self._get_config_po(connection_id)
        return self._to_entity(po, cfg_po)

    async def get_by_identifier(self, identifier: str) -> Optional[SSHConnection]:
        # 1. 优先按 connection_id (UUID) 精确查找
        host = await self.get_by_id(identifier)
        if host:
            return host

        # 2. 按 connection_name 或 host (IP/域名) 查找未删除的主机
        stmt = (
            select(SSHConnectionPO)
            .where(
                SSHConnectionPO.deleted == 0,
                or_(
                    SSHConnectionPO.connection_name == identifier,
                    SSHConnectionPO.host == identifier,
                ),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        po = result.scalars().first()
        if not po:
            return None
        cfg_po = await self._get_config_po(po.connection_id)
        return self._to_entity(po, cfg_po)

    async def list_all(self, skip: int = 0, limit: int = 100) -> List[SSHConnection]:
        stmt = (
            select(SSHConnectionPO)
            .where(SSHConnectionPO.deleted == 0)
            .order_by(desc(SSHConnectionPO.created_at))
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        pos = result.scalars().all()

        entities = []
        for po in pos:
            cfg_po = await self._get_config_po(po.connection_id)
            entities.append(self._to_entity(po, cfg_po))
        return entities

    async def delete(self, connection_id: str) -> bool:
        # 实施标准软删除
        stmt = select(SSHConnectionPO).where(SSHConnectionPO.connection_id == connection_id)
        result = await self.session.execute(stmt)
        po = result.scalars().first()
        if not po:
            return False
        po.deleted = 1
        await self.session.commit()
        return True
