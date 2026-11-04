"""适配层 - 应用依赖容器：集中持有共享基础设施单例"""

from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from adapter.agent.openai_client import OpenAICompatibleLLMClient
from adapter.repository.database import close_db, init_db
from adapter.runtime import RunEventBrokerRegistry
from adapter.security.auditor import BashlexCommandAuditor
from adapter.ssh.asyncssh_gateway import AsyncSSHGateway
from adapter.ssh.terminal_manager import InMemorySSHTerminalSessionManager
from adapter.tools import McpManager, build_default_registry
from application.runtime import (
    RemoteFileMutationQueue,
    SessionConcurrencyGuard,
    SessionTreeManager,
    TaskCancellationManager,
)
from application.ports import (
    ICommandAuditor,
    ILLMClient,
    ISSHGateway,
    ISSHTerminalSessionManager,
)
from application.react.engine import LangGraphReActEngine
from application.react.managers import (
    IApprovalManager,
    ISteeringManager,
    InMemoryApprovalManager,
    InMemorySteeringManager,
)
from application.tool_registry import ToolRegistry


@dataclass
class AppContainer:
    """应用运行期依赖容器（单个对象替代散落的模块级单例）"""

    checkpointer: Any = field(default_factory=MemorySaver)
    session_tree_manager: SessionTreeManager = field(default_factory=SessionTreeManager)
    auditor: ICommandAuditor = field(default_factory=BashlexCommandAuditor)
    ssh_gateway: ISSHGateway = field(default_factory=AsyncSSHGateway)
    llm_client: ILLMClient = field(default_factory=OpenAICompatibleLLMClient)
    terminal_session_manager: ISSHTerminalSessionManager = field(
        default_factory=InMemorySSHTerminalSessionManager
    )
    approval_manager: IApprovalManager = field(default_factory=InMemoryApprovalManager)
    steering_manager: ISteeringManager = field(default_factory=InMemorySteeringManager)
    tool_registry: ToolRegistry = field(default_factory=build_default_registry)
    mcp_manager: McpManager = field(default_factory=McpManager)
    file_mutation_queue: RemoteFileMutationQueue = field(default_factory=RemoteFileMutationQueue)
    session_run_guard: SessionConcurrencyGuard = field(default_factory=SessionConcurrencyGuard)
    task_cancellation_manager: TaskCancellationManager = field(default_factory=TaskCancellationManager)
    run_event_broker_registry: RunEventBrokerRegistry = field(default_factory=RunEventBrokerRegistry)
    react_engine: LangGraphReActEngine = field(init=False)

    def __post_init__(self) -> None:
        # 复用已编译状态图：所有请求共享同一引擎实例
        self.react_engine = LangGraphReActEngine(
            llm_client=self.llm_client,
            terminal_manager=self.terminal_session_manager,
            terminal_gateway=self.ssh_gateway,
            ssh_gateway=self.ssh_gateway,
            auditor=self.auditor,
            approval_manager=self.approval_manager,
            steering_manager=self.steering_manager,
            tool_registry=self.tool_registry,
            file_mutation_queue=self.file_mutation_queue,
            checkpointer=self.checkpointer,
            session_tree_manager=self.session_tree_manager,
        )

    async def startup(self) -> int:
        """初始化数据库表结构并连接 MCP Server"""
        await init_db()
        return await self.mcp_manager.connect_all(self.tool_registry)

    async def shutdown(self) -> None:
        """释放 MCP 连接与数据库连接池"""
        await self.mcp_manager.close()
        await close_db()
