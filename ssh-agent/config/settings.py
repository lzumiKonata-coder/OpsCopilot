"""全局配置中心：集中加载 .env 并解析环境变量，避免各适配器分散读取"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# 默认使用轻量级 aiosqlite 本地持久化，生产环境可由环境变量注入 PostgreSQL 或 MySQL 连接串
# 如：postgresql+asyncpg://user:pass@localhost:5432/smartops
DEFAULT_DB_URL = "sqlite+aiosqlite:///./smartops.db"

_dotenv_loaded = False


def _ensure_dotenv() -> None:
    """确保 .env 仅加载一次；不读取 .env.example，示例文件不作为运行时配置源"""
    global _dotenv_loaded
    if not _dotenv_loaded:
        load_dotenv()
        _dotenv_loaded = True


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in ("true", "1", "yes")


@dataclass(frozen=True)
class Settings:
    """运行期配置快照"""

    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_enable_thinking: bool
    database_url: str
    db_echo: bool
    secret_key: str
    mcp_servers_raw: str
    secret_key_file: str


def load_settings() -> Settings:
    """读取当前环境变量快照；每次调用重新解析，便于测试注入"""
    _ensure_dotenv()
    return Settings(
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/"),
        llm_model=os.getenv("LLM_MODEL", "gpt-4o").strip(),
        llm_enable_thinking=_env_bool("LLM_ENABLE_THINKING"),
        database_url=os.getenv("SMARTOPS_DATABASE_URL", DEFAULT_DB_URL),
        db_echo=_env_bool("SMARTOPS_DB_ECHO"),
        secret_key=(os.getenv("SMARTOPS_SECRET_KEY") or "").strip(),
        mcp_servers_raw=(os.getenv("MCP_SERVERS") or "").strip(),
        secret_key_file=os.path.join(os.getcwd(), ".smartops_secret_key"),
    )
