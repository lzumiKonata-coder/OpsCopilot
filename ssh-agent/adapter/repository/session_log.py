"""适配层 - SSH 终端会话日志仓储实现"""

from datetime import datetime, timezone

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from adapter.repository.models import SSHSessionLogPO
from application.ports import ISSHSessionLogRepository
from domain.entities import SSHSessionLog


class SQLAlchemySSHSessionLogRepository(ISSHSessionLogRepository):
    """
    基于 SQLAlchemy 2.0 的 WebSSH 伪终端会话记录仓储
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _to_entity(po: SSHSessionLogPO) -> SSHSessionLog:
        return SSHSessionLog(
            id=po.id,
            session_id=po.session_id,
            connection_id=po.connection_id,
            user_id=po.user_id,
            status=po.status,
            remote_addr=po.remote_addr,
            start_time=po.start_time,
            end_time=po.end_time,
            error_msg=po.error_msg,
        )

    async def create_log(self, log: SSHSessionLog) -> SSHSessionLog:
        po = SSHSessionLogPO(
            session_id=log.session_id,
            connection_id=log.connection_id,
            user_id=log.user_id,
            status=log.status,
            remote_addr=log.remote_addr,
            start_time=log.start_time,
        )
        self.session.add(po)
        await self.session.commit()
        return self._to_entity(po)

    async def close_log(self, session_id: str, error_msg: str | None = None) -> SSHSessionLog | None:
        stmt = select(SSHSessionLogPO).where(SSHSessionLogPO.session_id == session_id)
        result = await self.session.execute(stmt)
        po = result.scalars().first()
        if not po:
            return None
        po.status = 1  # 已关闭
        po.end_time = datetime.now(timezone.utc)
        if error_msg:
            po.error_msg = error_msg[:500]
        await self.session.commit()
        return self._to_entity(po)

    async def get_by_session_id(self, session_id: str) -> SSHSessionLog | None:
        stmt = select(SSHSessionLogPO).where(SSHSessionLogPO.session_id == session_id)
        result = await self.session.execute(stmt)
        po = result.scalars().first()
        return self._to_entity(po) if po else None

    async def list_by_connection(self, connection_id: str, limit: int = 50) -> list[SSHSessionLog]:
        stmt = (
            select(SSHSessionLogPO)
            .where(SSHSessionLogPO.connection_id == connection_id)
            .order_by(desc(SSHSessionLogPO.start_time))
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [self._to_entity(po) for po in result.scalars().all()]
