"""应用层 - SSH 终端会话日志用例 (ssh_session_log)"""

from typing import Optional

from application.ports import ISSHSessionLogRepository
from domain.entities import SSHSessionLog


class RecordSSHSessionLogUseCase:
    """用例：记录并更新 WebSSH 终端长连接会话日志"""

    def __init__(self, session_log_repo: ISSHSessionLogRepository):
        self.session_log_repo = session_log_repo

    async def open_session(
        self,
        connection_id: str,
        remote_addr: Optional[str] = None,
        user_id: str = "default",
    ) -> SSHSessionLog:
        log = SSHSessionLog(
            connection_id=connection_id,
            user_id=user_id,
            remote_addr=remote_addr,
            status=0,
        )
        return await self.session_log_repo.create_log(log)

    async def close_session(self, session_id: str, error_msg: Optional[str] = None) -> Optional[SSHSessionLog]:
        return await self.session_log_repo.close_log(session_id, error_msg=error_msg)
