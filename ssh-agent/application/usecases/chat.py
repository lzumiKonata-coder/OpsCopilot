"""应用层 - OpsAgent 对话用例编排 (chat_session / chat_message / chat_milestone)"""

import logging
import re
import time
import uuid
from typing import AsyncGenerator

from application.ports import (
    IChatSessionRepository,
    ILLMClient,
    IMemoryRepository,
    ISSHConnectionRepository,
)
from application.prompts import SYSTEM_PROMPT_TEMPLATE
from application.usecases.memory import _load_long_term_memory_text, _save_long_term_memory
from domain.entities import ChatSession
from domain.rules import EntityNotFoundError
from utils.shell import detect_os_family
from utils.text import TokenBudgetEstimator, remove_emojis

logger = logging.getLogger("smartops.application.usecases.chat")


class ChatWithOpsAgentUseCase:
    """与 OpsAgent 对话的业务用例编排（支持上下文记忆与会话历史落库）"""

    def __init__(
        self,
        host_repo: ISSHConnectionRepository,
        llm_client: ILLMClient,
        session_repo: IChatSessionRepository | None = None,
        memory_repo: IMemoryRepository | None = None,
    ):
        self.host_repo = host_repo
        self.llm_client = llm_client
        self.session_repo = session_repo
        self.memory_repo = memory_repo

    @staticmethod
    def _extract_root_cause(text: str) -> str | None:
        match = re.search(r"###\s*1\.\s*\s*根因分析[^\n]*\n(.*?)(?=###|\Z)", text, re.DOTALL)
        if match:
            cause = match.group(1).strip()
            return cause if cause else None
        return None

    @staticmethod
    def is_greeting(prompt: str) -> bool:
        clean = (prompt or "").strip().lower()
        if clean in {
            "你好", "您好", "hi", "hello", "hey", "在吗", "在么",
            "你是谁", "你叫什么", "介绍一下自己", "介绍下自己", "你能做什么",
            "你会做什么", "有什么功能", "帮助", "help",
        }:
            return True
        return bool(re.match(r"^(你好|您好|hi|hello|hey|在吗|在么|你是谁|你叫什么|介绍一下自己?|你能做[什甚]么|help)[啊呀哈哇呢啦么呗\s!！?？~～]*$", clean))

    async def execute(
        self,
        identifier: str,
        user_prompt: str,
        terminal_buffer: str | None = None,
        task_id: str | None = None,
        permission_mode: str | None = "request_approval",
        session_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        host = await self.host_repo.get_by_identifier(identifier)
        if not host:
            raise EntityNotFoundError(f"Host '{identifier}' not found.")
        user_prompt = remove_emojis(user_prompt).strip()

        # 会话与多轮上下文加载 (chat_session / chat_message)
        session: ChatSession | None = None
        if self.session_repo:
            if session_id:
                session = await self.session_repo.get_by_id(session_id)
            if not session:
                session = ChatSession(
                    id=session_id or str(uuid.uuid4()),
                    agent_id=host.connection_id,
                    user_id=host.user_id,
                    title="新会话",
                    messages=[],
                )

        # 动态替换 System Prompt 变量
        os_family = detect_os_family(terminal_buffer, host.connection_name)
        rendered_prompt = SYSTEM_PROMPT_TEMPLATE.replace("{{host_ip}}", host.host)
        rendered_prompt = rendered_prompt.replace("{{host_port}}", str(host.port))
        rendered_prompt = rendered_prompt.replace("{{os_info}}", os_family)
        rendered_prompt = rendered_prompt.replace("{{login_user}}", host.username)
        rendered_prompt = rendered_prompt.replace("{{is_root}}", str(host.username == "root"))
        rendered_prompt = rendered_prompt.replace("{{host_name}}", host.connection_name)
        rendered_prompt = rendered_prompt.replace(
            "{{terminal_buffer}}",
            terminal_buffer.strip() if terminal_buffer else "当前终端暂无最新输出。"
        )

        # 核心记忆提取注入 (core_memory)
        if self.memory_repo:
            try:
                memories = await self.memory_repo.find_relevant_core_memories(
                    user_id=session.user_id if session else "default",
                    query_text=user_prompt,
                    limit=5,
                )
                if memories:
                    mem_lines = [f"- [{m.category}] {m.title}: {m.content}" for m in memories]
                    rendered_prompt += "\n\n# 5. 用户核心记忆与排障偏好 (Core Memory Rules)\n" + "\n".join(mem_lines)
            except Exception as mem_err:
                logger.warning("Failed to retrieve core memories: %s", mem_err)

        # 长期记忆注入 (long_term_memory)
        rendered_prompt += await _load_long_term_memory_text(
            self.memory_repo,
            session.user_id if session else "default",
            host_id=host.connection_id or host.host,
        )

        is_pure_greeting = self.is_greeting(user_prompt)

        # 针对当前用户意图，注入最高优先级的意图引导指令
        if is_pure_greeting:
            intent_instruction = (
                "\n\n# 【本轮执行意图强制指令】\n"
                "用户当前输入为纯日常打招呼或问候。请极简礼貌回复（控制在 1~2 句话内，如：'您好！我是 SmartOps AI 运维智能副驾驶，随时为您提供运维与系统管理支持。请问有什么可以协助您的？'）。"
                "【绝对禁止】输出长篇大论、能力清单或已登录机器清单，【绝对禁止】输出任何根因分析或 Bash 代码块。"
            )
        else:
            intent_instruction = (
                f"\n\n# 【本轮执行意图强制指令（直奔主题，严格对齐物理机发行版）】\n"
                f"当前受管主机：{host.connection_name} ({host.host})，推断系统发行版：{os_family}，登录用户：{host.username}（is_root: {host.username == 'root'}）。\n"
                f"当前用户提出了需求：“{user_prompt}”。\n"
                "1. 【发行版与包管理器绝对约束】：若主机为 CentOS / RHEL / Rocky / openEuler / Hadoop 节点体系，【必须使用 yum 或 dnf】！【绝对禁止输出 apt 或 apt-get】！\n"
                "2. 【特权约束】：若当前登录用户已是 root（is_root == True）或终端提示符以 # 结尾，生成的命令【严禁添加 sudo 前缀】！直接使用原生命令（如 yum install -y docker）。\n"
                "3. 【若用户发送内容与运维无关】：客观、直接、简短回答（1~3句），严禁输出四段式或代码块！\n"
                "4. 【开门见山】：输出第一行必须直接以 \"### 1. 根因分析\" 开始，严禁任何客套废话或自我介绍！"
            )
        rendered_prompt += intent_instruction

        # 组合 System Prompt、历史上下文与本轮用户输入
        messages = [{"role": "system", "content": rendered_prompt}]
        if session:
            recent_history = session.get_recent_history(limit=8)
            for m in recent_history:
                messages.append({"role": m.role, "content": m.content})
            session.append_message(
                role="user",
                content=user_prompt,
                priority="HIGH",
                token_count=TokenBudgetEstimator.estimate_tokens(user_prompt),
            )

        messages.append({"role": "user", "content": user_prompt})

        start_time = time.monotonic()
        full_response_chunks: list[str] = []

        try:
            if not is_pure_greeting:
                # 针对运维任务增加冗余问候前缀清洗过滤，确保绝不输出冗余问候和客套话
                buffer = ""
                cleaning = True
                async for chunk in self.llm_client.stream_chat(messages):
                    full_response_chunks.append(chunk)
                    if cleaning:
                        buffer += chunk
                        if "### 1." in buffer or "1. 根因分析" in buffer or "1. 根因分析" in buffer or "### 根因分析" in buffer:
                            match = re.search(r"(###\s*1\.\s*\s*根因分析.*)", buffer, re.DOTALL)
                            if match:
                                cleaning = False
                                yield match.group(1)
                            else:
                                m2 = re.search(r"(\b1\.\s*\s*根因分析.*)", buffer, re.DOTALL)
                                if m2:
                                    cleaning = False
                                    yield "### " + m2.group(1).lstrip("#").strip()
                                else:
                                    cleaning = False
                                    yield buffer
                        elif len(buffer) > 250 and ("你好" not in buffer and "SmartOps" not in buffer and "机器" not in buffer):
                            cleaning = False
                            yield buffer
                    else:
                        yield chunk
                if cleaning and buffer:
                    cleaned = re.sub(r"你好，我是\s*SmartOps AI.*?直接告诉我你想做什么就行。?\s*", "", buffer, flags=re.DOTALL).strip()
                    cleaned = re.sub(r"关于.*?可以帮你完成这个任务[^\n]*\n*", "", cleaned).strip()
                    yield cleaned if cleaned else buffer
            else:
                async for chunk in self.llm_client.stream_chat(messages):
                    full_response_chunks.append(chunk)
                    yield chunk
        finally:
            full_text = "".join(full_response_chunks)

            # 会话历史与里程碑持久化落库 (chat_session / chat_message / chat_milestone)
            if self.session_repo and session:
                if full_response_chunks:
                    session.append_message(
                        role="assistant",
                        content=full_text,
                        token_count=TokenBudgetEstimator.estimate_tokens(full_text),
                    )

                    # 提炼关键排障结论作为里程碑 (chat_milestone)
                    root_cause = self._extract_root_cause(full_text)
                    if root_cause:
                        session.add_milestone(type_name="ROOT_CAUSE", content=root_cause[:200])

                    # 长期记忆沉淀：主机环境画像 + 已定位根因
                    await _save_long_term_memory(
                        self.memory_repo,
                        user_id=session.user_id,
                        memory_type="ServerEnv",
                        memory_key=f"{host.connection_id}:os_env",
                        content=(
                            f"主机 {host.connection_name} ({host.host})，系统发行版推断：{os_family}，"
                            f"登录用户：{host.username}（is_root={host.username == 'root'}）"
                        ),
                        keywords="环境,发行版,用户",
                        session_id=session.id,
                    )
                    if root_cause:
                        await _save_long_term_memory(
                            self.memory_repo,
                            user_id=session.user_id,
                            memory_type="Troubleshooting",
                            memory_key=f"{host.connection_id}:root_cause",
                            content=root_cause,
                            keywords="根因,排障",
                            confidence=0.7,
                            session_id=session.id,
                        )
                else:
                    # 首 Token 前遭遇异常，仍落库系统级错误记录，确保用户提问不丢库
                    session.append_message(
                        role="assistant",
                        content="[系统异常：大模型服务响应失败或未返回任何数据，请稍后重试]",
                        priority="CRITICAL",
                        token_count=TokenBudgetEstimator.estimate_tokens(
                            "[系统异常：大模型服务响应失败或未返回任何数据，请稍后重试]"
                        ),
                    )

                try:
                    await self.session_repo.save(session)
                    logger.info(
                        "Saved chat session: session_id=%s, title=%s, msg_count=%d",
                        session.id,
                        session.title,
                        len(session.messages),
                    )
                except Exception as exc:
                    logger.error("Failed to persist chat session: %s", exc)
