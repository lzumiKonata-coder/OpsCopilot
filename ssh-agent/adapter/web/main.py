"""
适配层 - Web 驱动入口 (FastAPI Application)
只负责应用装配：生命周期、CORS、全局异常处理与路由挂载。
具体 DTO 见 schemas.py，依赖装配见 dependencies.py，各领域端点见 routers/。
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from adapter.composition_root import shutdown_services, startup_services
from adapter.web.routers import (
    agent_react,
    agent_sessions,
    hosts,
    memories,
    terminal_ws,
)
from domain.rules import (
    DomainError,
    EntityNotFoundError,
    InvalidHostCredentialError,
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("smartops.web")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：委托组合根启动/释放共享基础设施"""
    logger.info("Initializing SmartOps database and table schema...")
    mcp_tool_count = await startup_services()
    logger.info("Database initialization completed successfully.")
    if mcp_tool_count:
        logger.info("MCP tools registered: %d", mcp_tool_count)
    yield
    logger.info("Closing SmartOps shared services gracefully...")
    await shutdown_services()
    logger.info("Shared services closed gracefully.")


app = FastAPI(
    title="SmartOps Remote Execution & Security Engine",
    version="2.0.0",
    description="企业级远程运维核心基座：全通道 PTY、对话消息/里程碑与记忆增强系统",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ====================================================================
# 1. 全局异常处理 (Global Exception Handlers)
# ====================================================================

@app.exception_handler(EntityNotFoundError)
async def entity_not_found_handler(request, exc: EntityNotFoundError):
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"code": "NOT_FOUND", "message": exc.message},
    )


@app.exception_handler(InvalidHostCredentialError)
async def credential_error_handler(request, exc: InvalidHostCredentialError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"code": "INVALID_CREDENTIAL", "message": exc.message},
    )


@app.exception_handler(DomainError)
async def domain_error_handler(request, exc: DomainError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"code": "DOMAIN_ERROR", "message": exc.message},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception):
    logger.exception("Unhandled server exception: %s", str(exc))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"code": "INTERNAL_SERVER_ERROR", "message": str(exc)},
    )


# ====================================================================
# 2. 系统端点与路由挂载 (System Endpoint & Routers)
# ====================================================================

@app.get("/health", tags=["System"])
async def health_check():
    """系统健康检查端点"""
    return {"status": "UP", "timestamp": datetime.now(timezone.utc).isoformat()}


app.include_router(hosts.router)
app.include_router(agent_react.router)
app.include_router(agent_sessions.router)
app.include_router(memories.router)
app.include_router(terminal_ws.router)
