"""
Web 适配层 - 依赖注入工厂
集中从 AppContainer 提供驱动单例与 FastAPI 依赖提供者，供各路由模块复用。
"""

from application.ports.repository import IChatSessionRepository, IMemoryRepository, ISSHConnectionRepository

from application.ports.llm import ILLMClient

from application.ports.ssh import ISSHGateway, ISSHTerminalGateway, ISSHTerminalSessionManager

from application.ports.audit import ICommandAuditor

from application.tool_registry import ToolRegistry

from application.runtime.file_mutation_queue import RemoteFileMutationQueue

from fastapi import Depends

from adapter.composition_root import container
from adapter.repository import (
    SQLAlchemyChatRepository,
    SQLAlchemyMemoryRepository,
    SQLAlchemySSHConnectionRepository,
    SQLAlchemySSHSessionLogRepository,
)
from adapter.repository.db_engine import get_db_session
from application.ports import (
    IChatSessionRepository,
    ICommandAuditor,
    ILLMClient,
    IMemoryRepository,
    ISSHConnectionRepository,
    ISSHGateway,
    ISSHSessionLogRepository,
    ISSHTerminalGateway,
    ISSHTerminalSessionManager,
)
from application.react.managers import (
    IApprovalManager,
    ISteeringManager,
)
from application.usecases.agent_session import (
    CreateAgentSessionUseCase,
    DeleteAgentSessionUseCase,
    GetAgentSessionUseCase,
    ListAgentSessionsUseCase,
)
from application.usecases.chat import ChatWithOpsAgentUseCase
from application.usecases.react import ReActUseCase
from application.usecases.ssh_session import RecordSSHSessionLogUseCase


def get_approval_manager() -> IApprovalManager:
    return container.approval_manager


def get_steering_manager() -> ISteeringManager:
    return container.steering_manager


# 依赖提供者
def get_auditor() -> ICommandAuditor:
    return container.auditor


def get_ssh_gateway() -> ISSHGateway:
    return container.ssh_gateway


def get_terminal_gateway() -> ISSHTerminalGateway:
    return container.ssh_gateway


def get_terminal_session_manager() -> ISSHTerminalSessionManager:
    return container.terminal_session_manager


def get_llm_client() -> ILLMClient:
    return container.llm_client


def get_host_repository(session=Depends(get_db_session)) -> ISSHConnectionRepository:
    return SQLAlchemySSHConnectionRepository(session)


def get_ssh_session_log_repository(session=Depends(get_db_session)) -> ISSHSessionLogRepository:
    return SQLAlchemySSHSessionLogRepository(session)


def get_record_ssh_session_log_use_case(
    session_log_repo: ISSHSessionLogRepository = Depends(get_ssh_session_log_repository),
) -> RecordSSHSessionLogUseCase:
    return RecordSSHSessionLogUseCase(session_log_repo=session_log_repo)


def get_agent_session_repository(session=Depends(get_db_session)) -> IChatSessionRepository:
    return SQLAlchemyChatRepository(session)


def get_memory_repository(session=Depends(get_db_session)) -> IMemoryRepository:
    return SQLAlchemyMemoryRepository(session)


def get_agent_use_case(
    host_repo: ISSHConnectionRepository = Depends(get_host_repository),
    llm_client: ILLMClient = Depends(get_llm_client),
    session_repo: IChatSessionRepository = Depends(get_agent_session_repository),
    memory_repo: IMemoryRepository = Depends(get_memory_repository),
) -> ChatWithOpsAgentUseCase:
    return ChatWithOpsAgentUseCase(
        host_repo=host_repo,
        llm_client=llm_client,
        session_repo=session_repo,
        memory_repo=memory_repo,
    )


def get_react_use_case(
    host_repo: ISSHConnectionRepository = Depends(get_host_repository),
    llm_client: ILLMClient = Depends(get_llm_client),
    terminal_manager: ISSHTerminalSessionManager = Depends(get_terminal_session_manager),
    terminal_gateway: ISSHTerminalGateway = Depends(get_terminal_gateway),
    ssh_gateway: ISSHGateway = Depends(get_ssh_gateway),
    session_repo: IChatSessionRepository = Depends(get_agent_session_repository),
    memory_repo: IMemoryRepository = Depends(get_memory_repository),
    auditor: ICommandAuditor = Depends(get_auditor),
    approval_manager: IApprovalManager = Depends(get_approval_manager),
    steering_manager: ISteeringManager = Depends(get_steering_manager),
) -> ReActUseCase:
    use_case_kwargs = dict[str, ISSHConnectionRepository | ILLMClient | ISSHTerminalSessionManager | ISSHTerminalGateway | ISSHGateway | IChatSessionRepository | IMemoryRepository | ICommandAuditor | IApprovalManager | ISteeringManager | ToolRegistry | RemoteFileMutationQueue](
        host_repo=host_repo,
        llm_client=llm_client,
        terminal_manager=terminal_manager,
        terminal_gateway=terminal_gateway,
        ssh_gateway=ssh_gateway,
        session_repo=session_repo,
        memory_repo=memory_repo,
        auditor=auditor,
        approval_manager=approval_manager,
        steering_manager=steering_manager,
        tool_registry=container.tool_registry,
        file_mutation_queue=container.file_mutation_queue,
    )
    # 默认复用共享引擎（编译图缓存与取消上下文均按请求隔离）；
    # 测试或自定义 LLM 场景下仍支持按当前依赖独立构造。
    if llm_client is container.llm_client:
        use_case_kwargs["engine"] = container.react_engine
    return ReActUseCase(**use_case_kwargs)


def get_create_agent_session_use_case(
    session_repo: IChatSessionRepository = Depends(get_agent_session_repository),
    host_repo: ISSHConnectionRepository = Depends(get_host_repository),
) -> CreateAgentSessionUseCase:
    return CreateAgentSessionUseCase(session_repo=session_repo, host_repo=host_repo)


def get_list_agent_sessions_use_case(
    session_repo: IChatSessionRepository = Depends(get_agent_session_repository),
    host_repo: ISSHConnectionRepository = Depends(get_host_repository),
) -> ListAgentSessionsUseCase:
    return ListAgentSessionsUseCase(session_repo=session_repo, host_repo=host_repo)


def get_get_agent_session_use_case(
    session_repo: IChatSessionRepository = Depends(get_agent_session_repository),
) -> GetAgentSessionUseCase:
    return GetAgentSessionUseCase(session_repo=session_repo)


def get_delete_agent_session_use_case(
    session_repo: IChatSessionRepository = Depends(get_agent_session_repository),
) -> DeleteAgentSessionUseCase:
    return DeleteAgentSessionUseCase(
        session_repo=session_repo,
        checkpointer=container.checkpointer,
        session_tree_manager=container.session_tree_manager,
    )
