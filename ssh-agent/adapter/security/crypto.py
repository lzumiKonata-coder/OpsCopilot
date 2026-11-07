"""
安全加密工具 (Credential Encryption & Decryption)
为存储于持久化数据库 (SQLite / MySQL / PostgreSQL) 中的 SSH 密码、私钥与 Passphrase 提供强加密保护。
采用 Fernet (AES-128-CBC + HMAC-SHA256) 算法，密钥优先由环境变量 SMARTOPS_SECRET_KEY 注入。
若未设置环境变量，则自动在本地持久化生成随机独立密钥文件 .smartops_secret_key，杜绝全局共享硬编码默认密钥。
"""

import base64
import hashlib
import logging
import os
import secrets

from cryptography.fernet import Fernet

from config.settings import load_settings

logger = logging.getLogger("smartops.security.crypto")


def _resolve_secret_key() -> str:
    """
    解析并获取加密主密钥：
    1. 优先读取环境变量 SMARTOPS_SECRET_KEY
    2. 若未配置环境变量，则读取或创建本地持久化密钥文件 .smartops_secret_key
    保障每个部署实例拥有独一无二且重启持久的加密密钥，杜绝共享硬编码默认密钥。
    """
    settings = load_settings()
    env_secret = settings.secret_key
    if env_secret and env_secret.strip():
        return env_secret.strip()

    key_file = settings.secret_key_file
    if os.path.exists(key_file):
        try:
            with open(key_file, "r", encoding="utf-8") as f:
                key = f.read().strip()
                if key:
                    return key
        except Exception as e:
            logger.warning("Failed to read persisted key from %s: %s", key_file, e)

    # 自动生成随机 32 字节安全密钥并持久化
    new_key = secrets.token_urlsafe(32)
    try:
        with open(key_file, "w", encoding="utf-8") as f:
            f.write(new_key)
        try:
            os.chmod(key_file, 0o600)
        except Exception:
            pass
        logger.warning(
            "SMARTOPS_SECRET_KEY not set in environment. Auto-generated instance secret key to '%s'. "
            "For production clusters, configure SMARTOPS_SECRET_KEY in your environment.",
            key_file,
        )
    except Exception as e:
        logger.error("Failed to persist generated secret key to %s: %s", key_file, e)

    return new_key


def _get_fernet_instance() -> Fernet:
    secret = _resolve_secret_key()
    key_bytes = hashlib.sha256(secret.encode("utf-8")).digest()
    url_safe_key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(url_safe_key)


_fernet: Fernet | None = None


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = _get_fernet_instance()
    return _fernet


def encrypt_credential(plain_text: str | None) -> str | None:
    """
    对敏感凭据（密码/私钥/Passphrase）执行 AES 对称加密。
    若加密发生异常，严格抛出 RuntimeError 中断保存，杜绝明文静默落库且被标记为 encrypted=1。
    """
    if plain_text is None:
        return None
    if not plain_text.strip():
        return plain_text
    try:
        fernet = get_fernet()
        token = fernet.encrypt(plain_text.encode("utf-8"))
        return token.decode("utf-8")
    except Exception as e:
        logger.critical("Failed to encrypt credential securely: %s", e)
        raise RuntimeError(f"Credential encryption failure: {e}") from e


def decrypt_credential(cipher_text: str | None) -> str | None:
    """对已加密的敏感凭据进行解密，若数据为存量明文则自动兼容返回"""
    if not cipher_text or not cipher_text.strip():
        return cipher_text
    try:
        fernet = get_fernet()
        decrypted = fernet.decrypt(cipher_text.encode("utf-8"))
        return decrypted.decode("utf-8")
    except Exception:
        # 兼容存量历史明文或格式不符凭据
        return cipher_text
