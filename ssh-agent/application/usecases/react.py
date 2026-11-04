"""应用层 - 自治运维 ReAct 循环用例 (LangGraph 驱动)"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List, Optional, Tuple
import uuid

from application.runtime import RemoteFileMutationQueue
from application.ports import (
    IChatSessionRepository,
    ICommandAuditor,
    ILLMClient,
    IMemoryRepository,
    IReActEngine,
    ISSHConnectionRepository,
    ISSHGateway,
    ISSHTerminalGateway,
    ISSHTerminalSessionManager,
)
from application.react.cancel_context import reset_cancel_check, set_cancel_check
from application.react.engine import LangGraphReActEngine
from application.react.managers import IApprovalManager, ISteeringManager
from application.react.state import ReActGraphState
from application.tool_registry import ToolRegistry
from application.usecases.chat import ChatWithOpsAgentUseCase
from application.usecases.memory import _load_long_term_memory_text, _save_long_term_memory
from domain.entities import ChatSession, SSHConnection
from domain.rules import EntityNotFoundError
from utils.shell import detect_os_family
from utils.text import remove_emojis

logger = logging.getLogger("smartops.application.usecases.react")


@dataclass
class ReActRunOptions:
    """一次 ReAct 排障请求的运行参数快照"""

    identifier: str
    user_prompt: str
    images: Optional[List[str]] = None
    terminal_buffer: Optional[str] = None
    session_id: Optional[str] = None
    max_steps: Optional[int] = None
    max_run_seconds: Optional[float] = None
    max_llm_calls: Optional[int] = None
    max_tokens: Optional[int] = None
    pre_planned_steps: Optional[List[Dict[str, str]]] = None
    cancel_check: Optional[Callable[[], Awaitable[bool]]] = None
    permission_mode: Optional[str] = "request_approval"
    approved_commands: Optional[List[str]] = None
    branch_from_session_id: Optional[str] = None
    branch_step_idx: Optional[int] = None
    branch_title: Optional[str] = None
    steering_message: Optional[str] = None


@dataclass
class ReActExecutionContext:
    """请求执行期派生的上下文快照"""

    host: SSHConnection
    session: Optional[ChatSession]
    options: ReActRunOptions
    os_family: str
    is_root_user: bool
    memory_extra: str
    session_history: List[Dict[str, str]]
    is_branching: bool
    forked_state: Dict[str, Any]


class ReActUseCase:
    """
    智能运维 ReAct 闭环编排用例 (Reasoning + Acting Loop)
    由 LangGraph StateGraph 状态图严格闭环驱动：
    Thought -> Audit -> Action (实时注入 PTY) -> Observation -> Reflection -> Next Step / Finish
    """

    def __init__(
        self,
        host_repo: ISSHConnectionRepository,
        llm_client: ILLMClient,
        terminal_manager: Optional[ISSHTerminalSessionManager] = None,
        terminal_gateway: Optional[ISSHTerminalGateway] = None,
        ssh_gateway: Optional[ISSHGateway] = None,
        session_repo: Optional[IChatSessionRepository] = None,
        memory_repo: Optional[IMemoryRepository] = None,
        auditor: Optional[ICommandAuditor] = None,
        approval_manager: Optional[IApprovalManager] = None,
        steering_manager: Optional[ISteeringManager] = None,
        approval_timeout: float = 120.0,
        context_window_size: Optional[int] = None,
        max_observation_chars: Optional[int] = None,
        tool_registry: Optional[ToolRegistry] = None,
        file_mutation_queue: Optional[RemoteFileMutationQueue] = None,
        engine: Optional[IReActEngine] = None,
    ):
        self.host_repo = host_repo
        self.llm_client = llm_client
        self.terminal_manager = terminal_manager
        self.terminal_gateway = terminal_gateway
        self.ssh_gateway = ssh_gateway
        self.session_repo = session_repo
        self.memory_repo = memory_repo
        self.auditor = auditor
        self.approval_manager = approval_manager
        self.steering_manager = steering_manager
        self.approval_timeout = approval_timeout

        # 初始化 LangGraph ReAct 状态机引擎；生产环境由组合根注入共享实例，复用已编译状态图
        self.engine: IReActEngine = engine if engine is not None else LangGraphReActEngine(
            llm_client=self.llm_client,
            terminal_manager=self.terminal_manager,
            terminal_gateway=self.terminal_gateway,
            ssh_gateway=self.ssh_gateway,
            auditor=self.auditor,
            approval_manager=self.approval_manager,
            steering_manager=self.steering_manager,
            approval_timeout=self.approval_timeout,
            context_window_size=context_window_size,
            max_observation_chars=max_observation_chars,
            tool_registry=tool_registry,
            file_mutation_queue=file_mutation_queue,
        )

    def push_steering_message(self, session_id: str, message: str) -> bool:
        """向指定排障会话推送即时插话/方向纠偏指令"""
        if getattr(self.engine, "steering_manager", None):
            self.engine.steering_manager.push_steering_message(session_id, message)
            return True
        return False

    @staticmethod
    def is_greeting(prompt: str) -> bool:
        return ChatWithOpsAgentUseCase.is_greeting(prompt)

    @staticmethod
    def is_non_ops_chat(prompt: str) -> bool:
        clean = (prompt or "").strip().lower()
        non_ops_keywords = [
            "写一首诗", "作诗", "讲个笑话", "翻译", "今天天气", "吃什么", "数学题",
            "谁最帅", "女朋友", "歌词", "故事", "脑筋急转弯",
        ]
        return any(k in clean for k in non_ops_keywords)

    async def _prepare_session(
        self,
        session_id: Optional[str],
        host: SSHConnection,
    ) -> Optional[ChatSession]:
        """加载或初始化会话 (chat_session)"""
        session: Optional[ChatSession] = None
        if self.session_repo:
            if session_id:
                session = await self.session_repo.get_by_id(session_id)
            if not session:
                session = ChatSession(
                    id=session_id or str(uuid.uuid4()),
                    agent_id=host.connection_id,
                    title="ReAct智能排障",
                    messages=[],
                )
        return session

    async def _load_memory_context(
        self,
        session: Optional[ChatSession],
        host: SSHConnection,
        user_prompt: str,
    ) -> str:
        """加载核心记忆与长期记忆，拼装注入模型的记忆上下文"""
        memory_extra = ""
        if self.memory_repo:
            try:
                memories = await self.memory_repo.find_relevant_core_memories(
                    user_id=session.user_id if session else "default",
                    query_text=user_prompt,
                    limit=5,
                )
                if memories:
                    mem_lines = [f"- [{m.category}] {m.title}: {m.content}" for m in memories]
                    memory_extra = "\n\n【用户记忆与规则约束】：\n" + "\n".join(mem_lines)
            except Exception as e:
                logger.warning("Failed to retrieve core memories: %s", e)

        memory_extra += await _load_long_term_memory_text(
            self.memory_repo,
            session.user_id if session else "default",
            host_id=host.connection_id or host.host,
        )
        return memory_extra

    @staticmethod
    def _collect_session_history(
        session: Optional[ChatSession],
        user_prompt: str,
    ) -> List[Dict[str, str]]:
        """提取会话最近多轮消息，并记录本轮用户输入（跳过前端合成的临时前缀）"""
        session_history: List[Dict[str, str]] = []
        if not session:
            return session_history

        for m in session.get_recent_history(limit=6):
            session_history.append({"role": m.role, "content": m.content})
        is_synthetic_prompt = user_prompt.startswith("目标任务:") or user_prompt.startswith("执行排障方案:")
        if not is_synthetic_prompt:
            session.append_message(role="user", content=user_prompt)
        return session_history

    @staticmethod
    def _sanitize_event(payload: Dict[str, Any]) -> Dict[str, Any]:
        """统一剥离事件文本中的 Emoji"""
        return {
            key: remove_emojis(value) if isinstance(value, str) else value
            for key, value in payload.items()
        }

    async def _run_graph_stream(
        self,
        graph_app: Any,
        initial_state: ReActGraphState,
        run_config: Dict[str, Any],
        cancel_check: Optional[Callable[[], Awaitable[bool]]],
        last_state: Dict[str, Any],
        outcome: Dict[str, Any],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """执行 LangGraph 状态图流，持续产出事件并回填运行结果"""
        try:
            async for mode, chunk in graph_app.astream(initial_state, config=run_config, stream_mode=["updates", "custom"]):
                # 响应客户端主动取消/中断信号
                if cancel_check and await cancel_check():
                    logger.info("ReAct execution canceled by client abort signal")
                    outcome["is_cancelled"] = True
                    yield {
                        "type": "cancelled",
                        "reason": "用户主动取消/终止排障流程",
                        "steps_count": last_state.get("step_idx", 1),
                    }
                    break

                if mode == "custom":
                    yield self._sanitize_event(chunk)
                    continue

                for node_name, update in chunk.items():
                    for event in update.get("emitted_events", []):
                        yield self._sanitize_event(event)
                    for k, v in update.items():
                        if k != "emitted_events":
                            last_state[k] = v
        except GeneratorExit:
            logger.info("ReAct execution generator closed by caller (GeneratorExit)")
            outcome["is_cancelled"] = True
            return
        except asyncio.CancelledError:
            logger.info("ReAct execution canceled by client CancelledError")
            outcome["is_cancelled"] = True
            yield {
                "type": "cancelled",
                "reason": "用户主动取消/终止排障流程",
                "steps_count": last_state.get("step_idx", 1),
            }
        except Exception as exc:
            if "NodeCancelledError" in type(exc).__name__ or isinstance(getattr(exc, "__cause__", None), asyncio.CancelledError):
                logger.info("ReAct execution canceled by NodeCancelledError / CancelledError cause")
                outcome["is_cancelled"] = True
                yield {
                    "type": "cancelled",
                    "reason": "用户主动取消/终止排障流程",
                    "steps_count": last_state.get("step_idx", 1),
                }
            else:
                outcome["is_errored"] = True
                outcome["error_msg"] = str(exc)
                logger.error("Exception during ReAct graph execution: %s", exc, exc_info=True)
                yield {
                    "type": "error",
                    "error": str(exc),
                    "step": last_state.get("step_idx", 1),
                }
                raise

    async def _persist_outcome(
        self,
        session: Optional[ChatSession],
        host: SSHConnection,
        last_state: Dict[str, Any],
        outcome: Dict[str, Any],
        os_family: str,
        is_root_user: bool,
    ) -> None:
        """会话历史与排障里程碑落库 (chat_session / chat_message / chat_milestone)"""
        executed_steps = last_state.get("executed_steps", [])
        if outcome["is_cancelled"]:
            is_success = False
            status_str = "【用户取消】"
            final_conclusion = "用户已主动中断/取消当前运维排障流程。"
        elif outcome["is_errored"]:
            is_success = False
            status_str = "【执行异常】"
            final_conclusion = f"执行过程中发生异常中断: {outcome.get('error_msg') or '未知异常'}"
        else:
            is_success = last_state.get("is_success", False)
            status_str = "【执行成功】" if is_success else "【执行受阻/未达成】"
            final_conclusion = last_state.get("final_conclusion") or ("排障流程已执行完成。" if is_success else "排障流程未完全达成或已被阻断。")

        if session and self.session_repo:
            trace_md = f"### 智能排障报告 {status_str}\n\n**最终结论**：\n{final_conclusion}\n\n**执行推演详情**：\n"
            for s in executed_steps:
                trace_md += f"- **步骤 {s['step']} ({s['title']})**: `{s['command']}`\n  - 研判：{s['analysis']}\n"

            session.append_message(role="assistant", content=remove_emojis(trace_md))
            session.add_milestone(type_name="REACT_DIAGNOSIS", content=final_conclusion[:200])

            if is_success:
                # 仅在任务成功闭环时沉淀 ServerEnv 画像，避免失败任务错误猜测污染
                await _save_long_term_memory(
                    self.memory_repo,
                    user_id=session.user_id,
                    memory_type="ServerEnv",
                    memory_key=f"{host.connection_id}:os_env",
                    content=(
                        f"主机 {host.connection_name} ({host.host})，系统发行版：{os_family}，"
                        f"登录用户：{host.username}（is_root={is_root_user}）"
                    ),
                    keywords="环境,发行版,用户",
                    session_id=session.id,
                )
                # 沉淀累积排障经验，使用任务独立 key 避免覆盖历史知识
                task_key_suffix = session.id[:8] if session and session.id else uuid.uuid4().hex[:8]
                await _save_long_term_memory(
                    self.memory_repo,
                    user_id=session.user_id,
                    memory_type="Troubleshooting",
                    memory_key=f"{host.connection_id}:task:{task_key_suffix}",
                    content=final_conclusion,
                    keywords="排障,结论",
                    confidence=0.7,
                    session_id=session.id,
                )

            try:
                await self.session_repo.save(session)
                logger.info("Saved ReAct session: session_id=%s, steps=%d, success=%s", session.id, len(executed_steps), is_success)
            except Exception as exc:
                logger.error("Failed to save ReAct session: %s", exc)

    def _small_talk_reply(self, user_prompt: str) -> Optional[str]:
        """非运维任务（打招呼、闲聊等）的极简回答，无需触碰终端"""
        if self.is_greeting(user_prompt):
            return "您好！我是 SmartOps AI 运维智能副驾驶，随时为您提供系统运维支持。请问有什么可以协助您的？"
        if self.is_non_ops_chat(user_prompt):
            return "收到。我是专门负责 Linux 服务器运维与排障的智能副驾驶，日常闲聊请恕我简短回答。如果您有任何服务器、网络或系统排障需求，请随时告诉我！"
        return None

    def _build_initial_state(self, ctx: ReActExecutionContext) -> ReActGraphState:
        """构造 LangGraph 初始状态，并合并分支继承的前序历史事实"""
        options = ctx.options
        host = ctx.host
        session = ctx.session
        branch_step_idx = options.branch_step_idx or 0

        # 服务端硬上限保护：防止客户端传入过大预算导致长久空转绕过
        effective_max_run_seconds = min(options.max_run_seconds or 600.0, 3600.0)
        effective_max_llm_calls = min(options.max_llm_calls or 30, 200)
        effective_max_tokens = min(options.max_tokens or 100_000, 500_000)

        initial_state: ReActGraphState = {
            "host": host,
            "user_prompt": options.user_prompt,
            "images": list(options.images) if options.images else [],
            "session_id": session.id if session else None,
            "max_steps": options.max_steps,
            "start_time": time.time(),
            "max_run_seconds": effective_max_run_seconds,
            "llm_call_count": 0,
            "max_llm_calls": effective_max_llm_calls,
            "total_tokens": 0,
            "max_tokens": effective_max_tokens,
            "budget_exceeded": False,
            "is_stalled": False,
            "steering_message": options.steering_message,
            "terminal_buffer": options.terminal_buffer,
            "os_family": ctx.os_family,
            "is_root": ctx.is_root_user,
            "core_memories_text": ctx.memory_extra,
            "session_history": ctx.session_history,
            "permission_mode": options.permission_mode or "request_approval",
            "approved_commands": list(options.approved_commands) if options.approved_commands else [],
            "is_plan_confirmed": False,
            "pending_approval_id": None,
            "current_thought": "",
            "current_command": None,
            "current_tool_name": None,
            "current_tool_args": None,
            "current_step_title": "",
            "current_observation": "",
            "current_reflection": "",
            "is_step_normal": True,
            "step_idx": 1,
            "planned_steps_queue": list(options.pre_planned_steps) if options.pre_planned_steps else [],
            "react_history": [],
            "executed_steps": [],
            "has_encountered_error": False,
            "last_error_detail": None,
            "consecutive_errors": 0,
            "is_blocked": False,
            "blocked_reason": None,
            "final_answer": None,
            "final_conclusion": None,
            "is_success": False,
            "should_stop": False,
            "emitted_events": [],
        }

        if ctx.is_branching and ctx.forked_state:
            # 仅继承分支前序历史步骤事实，当前请求的运行参数（权限模式、白名单、软预算、转向消息等）保持最高优先级
            forked_state_dict = ctx.forked_state
            initial_state["executed_steps"] = list(forked_state_dict.get("executed_steps") or [])
            initial_state["react_history"] = list(forked_state_dict.get("react_history") or [])
            initial_state["step_idx"] = forked_state_dict.get("step_idx", branch_step_idx + 1)
            if initial_state["step_idx"] <= branch_step_idx:
                initial_state["step_idx"] = branch_step_idx + 1
            if "os_family" in forked_state_dict and not initial_state.get("os_family"):
                initial_state["os_family"] = forked_state_dict["os_family"]
            if "is_root" in forked_state_dict and initial_state.get("is_root") is None:
                initial_state["is_root"] = forked_state_dict["is_root"]
            if forked_state_dict.get("current_thought"):
                initial_state["current_thought"] = forked_state_dict["current_thought"]
            if forked_state_dict.get("current_step_title"):
                initial_state["current_step_title"] = forked_state_dict["current_step_title"]
            initial_state["is_success"] = False
            initial_state["should_stop"] = False

        return initial_state

    async def execute(
        self,
        identifier: str,
        user_prompt: str,
        images: Optional[List[str]] = None,
        terminal_buffer: Optional[str] = None,
        session_id: Optional[str] = None,
        max_steps: Optional[int] = None,
        max_run_seconds: Optional[float] = None,
        max_llm_calls: Optional[int] = None,
        max_tokens: Optional[int] = None,
        pre_planned_steps: Optional[List[Dict[str, str]]] = None,
        cancel_check: Optional[Callable[[], Awaitable[bool]]] = None,
        permission_mode: Optional[str] = "request_approval",
        approved_commands: Optional[List[str]] = None,
        branch_from_session_id: Optional[str] = None,
        branch_step_idx: Optional[int] = None,
        branch_title: Optional[str] = None,
        steering_message: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """面向调用方的便捷入口：将散列参数聚合为 ReActRunOptions 后执行"""
        options = ReActRunOptions(
            identifier=identifier,
            user_prompt=user_prompt,
            images=images,
            terminal_buffer=terminal_buffer,
            session_id=session_id,
            max_steps=max_steps,
            max_run_seconds=max_run_seconds,
            max_llm_calls=max_llm_calls,
            max_tokens=max_tokens,
            pre_planned_steps=pre_planned_steps,
            cancel_check=cancel_check,
            permission_mode=permission_mode,
            approved_commands=approved_commands,
            branch_from_session_id=branch_from_session_id,
            branch_step_idx=branch_step_idx,
            branch_title=branch_title,
            steering_message=steering_message,
        )
        async for event in self._execute(options):
            yield event

    async def _execute(self, options: ReActRunOptions) -> AsyncGenerator[Dict[str, Any], None]:
        """执行一次 ReAct 排障请求的完整生命周期编排"""
        host = await self.host_repo.get_by_identifier(options.identifier)
        if not host:
            raise EntityNotFoundError(f"Host '{options.identifier}' not found.")
        options.user_prompt = remove_emojis(options.user_prompt).strip()
        user_prompt = options.user_prompt

        # 加载或初始化会话 (chat_session)
        session = await self._prepare_session(options.session_id, host)

        # 1. 意图分流：若非运维任务（打招呼、闲聊等），极简回答并直接退出，不触碰终端
        small_talk_text = self._small_talk_reply(user_prompt)
        if small_talk_text is not None:
            yield {"type": "greeting", "content": small_talk_text}
            if session and self.session_repo:
                session.append_message(role="user", content=user_prompt)
                session.append_message(role="assistant", content=small_talk_text)
                await self.session_repo.save(session)
            return

        # 分支特性支持：若指定了父分支，克隆历史并初始化分支状态
        is_branching = bool(options.branch_from_session_id and options.branch_step_idx is not None)
        forked_state_dict: Dict[str, Any] = {}
        if is_branching:
            branch_sid, forked_state_dict = await self.engine.fork_branch(
                parent_session_id=options.branch_from_session_id,
                branch_point_step=options.branch_step_idx,
                new_branch_session_id=session.id if session else None,
                branch_title=options.branch_title,
                new_prompt=user_prompt,
            )
            yield {
                "type": "branch_created",
                "branch_session_id": branch_sid,
                "parent_session_id": options.branch_from_session_id,
                "branch_step": options.branch_step_idx,
                "title": options.branch_title or f"分支 (切入第 {options.branch_step_idx} 步)",
            }

        # 2. 属于运维排障任务，正式开启 LangGraph 驱动的 ReAct 状态图循环
        yield {
            "type": "start",
            "session_id": session.id if session else "",
            "host_id": host.connection_id,
            "host_name": host.connection_name,
            "user_prompt": user_prompt,
        }

        # 智能研判受管机 Linux 发行版与包管理器
        os_family = detect_os_family(options.terminal_buffer, host.connection_name)
        is_root_user = host.username == "root" or LangGraphReActEngine.detect_root_from_output(options.terminal_buffer) is True

        # 核心记忆与长期记忆注入
        memory_extra = await self._load_memory_context(session, host, user_prompt)

        # 提取会话历史多轮消息（供推演上下文使用）
        session_history = self._collect_session_history(session, user_prompt)

        # 绑定协程级取消检查上下文（不直接放入 initial_state，避免破坏 checkpointer msgpack 序列化）
        cancel_token = set_cancel_check(options.cancel_check)

        # 构造 LangGraph 初始状态
        ctx = ReActExecutionContext(
            host=host,
            session=session,
            options=options,
            os_family=os_family,
            is_root_user=is_root_user,
            memory_extra=memory_extra,
            session_history=session_history,
            is_branching=is_branching,
            forked_state=forked_state_dict,
        )
        initial_state = self._build_initial_state(ctx)

        # 复用已编译的状态图实例，避免每次请求重复构建与编译
        graph_app = self.engine.get_compiled_graph()
        last_state = dict(initial_state)
        thread_id = session.id if session else f"react_{uuid.uuid4().hex[:12]}"
        run_config = {"configurable": {"thread_id": thread_id}}

        outcome: Dict[str, Any] = {"is_cancelled": False, "is_errored": False, "error_msg": None}
        try:
            async for event in self._run_graph_stream(
                graph_app=graph_app,
                initial_state=initial_state,
                run_config=run_config,
                cancel_check=options.cancel_check,
                last_state=last_state,
                outcome=outcome,
            ):
                yield event
        finally:
            reset_cancel_check(cancel_token)
            await self._persist_outcome(
                session=session,
                host=host,
                last_state=last_state,
                outcome=outcome,
                os_family=os_family,
                is_root_user=is_root_user,
            )

    async def get_session_checkpoints(self, session_id: str) -> List[Dict[str, Any]]:
        """时间旅行：查询会话历史执行各步检查点"""
        return await self.engine.get_session_checkpoints(session_id)

    async def fork_branch(
        self,
        parent_session_id: str,
        branch_point_step: int,
        branch_title: Optional[str] = None,
        new_prompt: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """手动从指定检查点分叉新排障分支"""
        return await self.engine.fork_branch(
            parent_session_id=parent_session_id,
            branch_point_step=branch_point_step,
            branch_title=branch_title,
            new_prompt=new_prompt,
        )

    def get_session_tree(self, root_session_id: str) -> Dict[str, Any]:
        """获取会话分支拓扑树"""
        return self.engine.session_tree_manager.get_tree(root_session_id)
