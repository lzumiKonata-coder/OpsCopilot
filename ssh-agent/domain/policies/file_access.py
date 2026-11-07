"""领域层 - 文件通道静态安全策略"""

import posixpath
import re

from domain.entities import AuditDecision
from domain.rules import RiskLevel

FORBIDDEN_FILE_PATHS = [
    re.compile(r"^/etc/(?:shadow|gshadow|sudoers)(?:$|/)"),
    re.compile(r"^/etc/sudoers\.d(?:$|/)"),
    re.compile(r"^/etc/ld\.so\.preload(?:$|/)"),
    re.compile(r"^/(?:boot|dev|proc|sys)(?:$|/)"),
    re.compile(r"^/(?:bin|sbin|usr/bin|usr/sbin)(?:$|/)"),
    re.compile(r"^/$"),
]

HIGH_RISK_FILE_PATHS = [
    re.compile(r"^/etc/(?:passwd|group|fstab|hosts|crontab|resolv\.conf)(?:$|/)"),
    re.compile(r"^/etc/cron(?:\.d|\.daily|\.hourly|\.weekly|\.monthly)?(?:$|/)"),
    re.compile(r"^/var/spool/cron(?:$|/)"),
    re.compile(r"^/etc/security(?:$|/)"),
    re.compile(r"^/etc/systemd/system(?:$|/)"),
    re.compile(r"^/usr/lib/systemd/system(?:$|/)"),
    re.compile(r"^/etc/ssh(?:$|/)"),
    re.compile(r"^/etc/modprobe(?:\.d)?(?:$|/)"),
    re.compile(r"^/etc/rc\.(?:local|d)(?:$|/)"),
    re.compile(r"(?:^|/)\.ssh(?:$|/)"),
    re.compile(r"(?:^|/)(?:\.bashrc|\.bash_profile|\.profile|\.bash_login|\.bash_logout|\.zshrc|\.zprofile|\.cshrc|\.tcshrc)(?:$|/)"),
    re.compile(r"^/etc/profile(?:\.d)?(?:$|/)"),
    re.compile(r"^/etc/bash\.bashrc(?:$|/)"),
    re.compile(r"^/etc/ld\.so\.conf(?:\.d)?(?:$|/)"),
    re.compile(r"^/etc/pam\.d(?:$|/)"),
]

SAFE_WRITE_APP_PATHS = [
    re.compile(r"^/etc/(?:nginx|apache2|httpd|caddy|redis|mysql|php|supervisor|logrotate\.d)(?:$|/)"),
    re.compile(r"^/home/[^/]+/(?:app|project|workspace|data|dist|build|html)(?:$|/)"),
    re.compile(r"^/(?:var/www|tmp|opt)(?:$|/)"),
]

SENSITIVE_READ_FILE_PATHS = [
    re.compile(r"^/etc/(?:shadow|gshadow|sudoers|master\.passwd)(?:$|/)"),
    re.compile(r"^/etc/sudoers\.d(?:$|/)"),
    re.compile(r"^/etc/ssh/ssh_host_.*_key(?:$|\.)"),
    re.compile(r"(?:^|/)ssh_host_.*_key(?:$|\.)"),
    re.compile(r"(?:^|/)\.ssh/(?:id_rsa|id_ed25519|id_ecdsa|id_dsa)(?:$|\.)"),
    re.compile(r"(?:^|/)(?:id_rsa|id_ed25519|id_ecdsa|id_dsa)(?:$|\.)"),
    re.compile(r"^/(?:root|\.ssh|etc/ssh)/.*(?:\.pem|\.key)$"),
]


def audit_file_content_security(content: str) -> AuditDecision | None:
    """审计文件通道写入/替换的内容载荷，阻断反弹 Shell 与后门公钥注入"""
    if not content:
        return None
    # 1. 致命反弹 Shell 载荷拦截
    if re.search(r"(/dev/tcp/\d|nc(?:\.traditional)?\s+-[el]|mkfifo\s+/tmp/|/bin/(?:ba)?sh\s+-i|python[23]?\s+-c\s+.*socket)", content):
        return AuditDecision(
            risk_level=RiskLevel.FORBIDDEN,
            hit_rules=["FORBIDDEN_REVERSE_SHELL_PAYLOAD"],
            reason="写入内容中检测到高危反弹 Shell / 后门载荷，平台硬性阻断",
        )
    # 2. SSH 公钥持久化注入提权
    if re.search(r"(ssh-rsa\s+AAAA|ssh-ed25519\s+AAAA|ecdsa-sha2-nistp)", content):
        return AuditDecision(
            risk_level=RiskLevel.HIGH_RISK,
            hit_rules=["HIGH_RISK_SSH_KEY_INJECTION"],
            reason="写入内容包含 SSH 授权公钥，存在持久化免密提权风险，需人机审批",
        )
    return None


def audit_file_path_security(path: str, is_write: bool = True) -> AuditDecision:
    """
    静态文件通道安全策略
    保护核心敏感路径，杜绝通过 write_remote_file / edit_remote_file 篡改提权与系统核心配置；
    并在只读读取时拦截私钥与密码本等敏感文件泄漏。
    """
    if not path or not path.strip():
        return AuditDecision(risk_level=RiskLevel.FORBIDDEN, reason="文件路径为空")

    cleaned = path.strip().replace("\\", "/")
    # 处理波浪号 ~ 展开为虚拟统一前缀
    if cleaned.startswith("~"):
        cleaned = "/root" + cleaned[1:] if cleaned.startswith("~/") or cleaned == "~" else "/" + cleaned
    elif not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    norm_path = posixpath.normpath(cleaned)

    if is_write:
        for pat in FORBIDDEN_FILE_PATHS:
            if pat.search(norm_path) or pat.search(path):
                return AuditDecision(
                    risk_level=RiskLevel.FORBIDDEN,
                    hit_rules=["FORBIDDEN_SYSTEM_PATH"],
                    reason=f"平台安全策略坚决禁止修改系统核心敏感路径: {norm_path}"
                )

        for pat in HIGH_RISK_FILE_PATHS:
            if pat.search(norm_path) or pat.search(path):
                return AuditDecision(
                    risk_level=RiskLevel.HIGH_RISK,
                    hit_rules=["HIGH_RISK_SYSTEM_CONFIG"],
                    reason=f"修改关键系统服务配置、后门持久化或鉴权路径需人机审批: {norm_path}"
                )

        # /etc/**, /root/**, /home/** 默认归入高危，仅显式列出的业务应用路径放行
        if any(norm_path.startswith(prefix) for prefix in ("/etc/", "/root/", "/home/", "/var/spool/")):
            is_safe_app = any(pat.search(norm_path) for pat in SAFE_WRITE_APP_PATHS)
            is_dotfile = bool(re.search(r"(?:^|/)\.(?:bash|profile|zsh|ssh|cron|local|config)", norm_path))
            if is_safe_app and not is_dotfile:
                return AuditDecision(risk_level=RiskLevel.SAFE, reason="业务配置或应用目录修改放行")
            return AuditDecision(
                risk_level=RiskLevel.HIGH_RISK,
                hit_rules=["HIGH_RISK_SYSTEM_PATH"],
                reason=f"修改系统与用户关键目录文件需人机审批: {norm_path}"
            )
    else:
        # 读模式：严禁未审批读取敏感凭据与私钥
        for pat in SENSITIVE_READ_FILE_PATHS:
            if pat.search(norm_path) or pat.search(path):
                return AuditDecision(
                    risk_level=RiskLevel.HIGH_RISK,
                    hit_rules=["SENSITIVE_CREDENTIAL_READ"],
                    reason=f"读取敏感凭据与特权私钥文件需人机审批: {norm_path}"
                )

    return AuditDecision(risk_level=RiskLevel.SAFE, reason="普通文件操作")
