"""
领域层 - 业务规则与常量定义 (Domain Rules)
严格仅依赖 Python 标准库，严禁引入 SQLAlchemy、FastAPI、asyncssh 等任何外部框架。
"""

from enum import Enum, IntEnum
from typing import Set


class RiskLevel(str, Enum):
    """命令安全风险等级枚举"""
    SAFE = "SAFE"            # 安全命令：只读操作或普通无害操作（如 ls, uname, df 等）
    HIGH_RISK = "HIGH_RISK"  # 高危命令：可能变更系统状态或影响可用性（如 reboot, kill -9, iptables 等）
    FORBIDDEN = "FORBIDDEN"  # 绝对禁令命令：破坏性/灾难性命令，必须坚决拦截（如 rm -rf /, mkfs, fork 炸弹等）


class AuthType(IntEnum):
    """主机 SSH 认证方式枚举 (1-密码, 2-私钥)"""
    PASSWORD = 1
    PRIVATE_KEY = 2

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            val_upper = value.upper()
            if val_upper in ("PASSWORD", "1"):
                return cls.PASSWORD
            if val_upper in ("PRIVATE_KEY", "2", "KEY"):
                return cls.PRIVATE_KEY
        return None

    def __str__(self) -> str:
        return self.name

    def __eq__(self, other):
        if isinstance(other, str):
            return self.name == other.upper() or str(self.value) == other
        return super().__eq__(other)


# ==================== 领域核心业务规则与约束常量 ====================

DEFAULT_TIMEOUT_SECONDS: int = 30
MAX_TIMEOUT_SECONDS: int = 300
DEFAULT_MAX_BYTES: int = 8192  # 默认 8KB 输出截断保护

# 绝对禁止的破坏性根目录/系统路径列表（用于 rm 等破坏性命令的级联检查）
FORBIDDEN_DESTRUCTIVE_PATHS: Set[str] = {
    "/",
    "/*",
    "/etc",
    "/etc/*",
    "/boot",
    "/boot/*",
    "/usr",
    "/usr/*",
    "/var",
    "/var/*",
    "/bin",
    "/bin/*",
    "/sbin",
    "/sbin/*",
    "~",
    "~/*",
    "$HOME",
}

# 绝对禁止执行的裸命令与设备破坏命令
FORBIDDEN_COMMAND_NAMES: Set[str] = {
    "mkfs",
    "mkfs.ext4",
    "mkfs.xfs",
    "fdisk",
    "parted",
    "gdisk",
}

# 高危操作命令名（需要严密审查或默认阻断）
HIGH_RISK_COMMAND_NAMES: Set[str] = {
    "reboot",
    "shutdown",
    "poweroff",
    "init",
    "halt",
    "kill",
    "pkill",
    "killall",
    "iptables",
    "ufw",
    "firewalld",
    "firewall-cmd",
    "passwd",
    "visudo",
    "usermod",
    "chmod",
    "chown",
    "systemctl",
    "service",
    "rm",
}

# 敏感文件读取黑名单（命中视作 HIGH_RISK）
SENSITIVE_FILES: Set[str] = {
    "/etc/shadow",
    "/etc/sudoers",
    "/etc/master.passwd",
    "/etc/gshadow",
    "~/.ssh/id_rsa",
    "~/.ssh/id_ed25519",
    "~/.ssh/id_ecdsa",
    "~/.ssh/id_dsa",
    "/root/.ssh/id_rsa",
    "/root/.ssh/id_ed25519",
    "/root/.ssh/id_ecdsa",
    "/root/.ssh/id_dsa",
    ".ssh/id_rsa",
    ".ssh/id_ed25519",
    ".ssh/id_ecdsa",
    ".ssh/id_dsa",
    ".ssh/authorized_keys",
}


# ==================== 领域层异常基类 ====================

class DomainError(Exception):
    """领域层根异常"""
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class EntityNotFoundError(DomainError):
    """实体不存在异常"""
    pass


class InvalidHostCredentialError(DomainError):
    """主机凭据非法异常"""
    pass


class SecurityInterceptionError(DomainError):
    """安全审查拦截异常"""
    def __init__(self, message: str, risk_level: RiskLevel, hit_rules: list[str]):
        super().__init__(message)
        self.risk_level = risk_level
        self.hit_rules = hit_rules
