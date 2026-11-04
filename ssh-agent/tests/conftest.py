"""测试环境全局配置：临时数据库、集成测试标记与全局夹具"""

import atexit
import os
import shutil
import tempfile

import pytest
import pytest_asyncio

_TEST_DB_DIR = tempfile.mkdtemp(prefix="smartops_test_")
_TEST_DB_PATH = os.path.join(_TEST_DB_DIR, "test_smartops.db").replace("\\", "/")
os.environ["SMARTOPS_DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB_PATH}"
atexit.register(shutil.rmtree, _TEST_DB_DIR, ignore_errors=True)


def pytest_collection_modifyitems(config, items):
    """按目录自动标注集成测试，无需逐个文件添加 marker"""
    for item in items:
        if item.nodeid.startswith("tests/integration/"):
            item.add_marker(pytest.mark.integration)


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    """全量测试统一初始化与释放测试数据库，避免各测试文件重复定义夹具"""
    from adapter.repository.database import close_db, init_db

    await init_db()
    yield
    await close_db()
