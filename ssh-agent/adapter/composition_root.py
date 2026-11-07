"""适配层 - 组合根：构造全局依赖容器并暴露基础设施启停入口"""

from adapter.container import AppContainer

container: AppContainer = AppContainer()


async def startup_services() -> int:
    """启动组合根管理的基础设施"""
    return await container.startup()


async def shutdown_services() -> None:
    """释放组合根管理的基础设施"""
    await container.shutdown()


__all__ = [
    "AppContainer",
    "container",
    "shutdown_services",
    "startup_services",
]
