"""
适配层 - 数据库连接与会话生命周期管理 (Database Engine & Session Management)
基于 SQLAlchemy 2.0 异步引擎，支持 aiosqlite / asyncpg / aiomysql 无缝切换。
引擎与会话工厂均惰性创建，避免在模块导入时固化运行期配置。
"""

from typing import AsyncGenerator, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config.settings import load_settings

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def get_engine() -> AsyncEngine:
    """惰性创建异步数据库引擎，确保运行期配置变化可生效"""
    global _engine
    if _engine is None:
        settings = load_settings()
        database_url = settings.database_url
        _engine = create_async_engine(
            database_url,
            echo=settings.db_echo,
            pool_pre_ping=True,
            # SQLite 独有配置（针对多进程多连接并发增加超时时间与线程检查放宽）
            connect_args={"check_same_thread": False, "timeout": 30} if "sqlite" in database_url else {},
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """惰性创建异步会话工厂"""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
    return _session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖注入使用的异步数据库 Session 生成器"""
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """初始化数据库表结构（在服务启动生命周期中调用）并启用 WAL 模式提高并发性能"""
    from adapter.repository.models import Base

    engine = get_engine()
    database_url = str(engine.url)
    async with engine.begin() as conn:
        if "sqlite" in database_url:
            await conn.execute(text("PRAGMA journal_mode=WAL;"))
            await conn.execute(text("PRAGMA busy_timeout=30000;"))
        await conn.run_sync(Base.metadata.create_all)

        # 兼容 SQLite、PostgreSQL 与 MySQL 存量表迁移 passphrase 列
        dialect_name = engine.dialect.name
        if dialect_name == "sqlite":
            res = await conn.execute(text("PRAGMA table_info(ssh_connection);"))
            cols = [row[1] for row in res.fetchall()]
            if cols and "passphrase" not in cols:
                await conn.execute(text("ALTER TABLE ssh_connection ADD COLUMN passphrase VARCHAR(512);"))
        elif dialect_name in ("postgresql", "postgres"):
            await conn.execute(text("ALTER TABLE ssh_connection ADD COLUMN IF NOT EXISTS passphrase VARCHAR(512);"))
        elif dialect_name == "mysql":
            res = await conn.execute(text(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME = 'ssh_connection' AND COLUMN_NAME = 'passphrase' "
                "AND TABLE_SCHEMA = DATABASE();"
            ))
            if not res.fetchall():
                await conn.execute(text("ALTER TABLE ssh_connection ADD COLUMN passphrase VARCHAR(512) NULL;"))


async def close_db() -> None:
    """平滑释放连接池资源；重置惰性单例以便后续按新配置重新初始化"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
