"""
适配层 - OpenAI 兼容规范大模型客户端驱动实现
支持标准 OpenAI, DeepSeek, 阿里通义千问 (DashScope), Ollama 等兼容端点
支持原生 Tool Calling (Function Calling)、SSE 流式返回分块 (Chunks) 与断网/未配置优雅降级。
"""

import json
import logging
import re
import asyncio
from typing import Any, AsyncGenerator, Callable

import httpx

from application.ports import ILLMClient, LLMResponse, ToolCall
from config.settings import load_settings

logger = logging.getLogger("smartops.agent.llm")


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def normalize_usage(raw: Any) -> dict[str, Any]:
    """
    归一化不同上游的 usage 字段，统一暴露输入/输出/缓存命中统计。
    兼容 OpenAI 的 prompt_tokens_details.cached_tokens、DeepSeek 的
    prompt_cache_hit_tokens 以及 Anthropic 风格的 cache_read_input_tokens。
    """
    if not isinstance(raw, dict):
        return {}

    details = raw.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}
    cached_raw = raw.get("prompt_cache_hit_tokens")
    if cached_raw is None:
        cached_raw = details.get("cached_tokens")
    if cached_raw is None:
        cached_raw = raw.get("cache_read_input_tokens")

    prompt_tokens = _to_int(raw.get("prompt_tokens"))
    cached_tokens = _to_int(cached_raw)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": _to_int(raw.get("completion_tokens")),
        "total_tokens": _to_int(raw.get("total_tokens")),
        "cached_tokens": cached_tokens,
        "cache_hit": cached_tokens > 0,
        "cache_hit_rate": round(cached_tokens / prompt_tokens, 4) if prompt_tokens else 0.0,
    }


def _log_usage(stage: str, model: str, usage: dict[str, Any]) -> None:
    """统一记录 Token 与缓存命中统计，便于观测 Prompt Caching 效果"""
    if not usage:
        return
    logger.info(
        "LLM usage [%s] model=%s prompt=%d completion=%d total=%d cached=%d hit_rate=%.2f%%",
        stage,
        model,
        usage.get("prompt_tokens", 0),
        usage.get("completion_tokens", 0),
        usage.get("total_tokens", 0),
        usage.get("cached_tokens", 0),
        usage.get("cache_hit_rate", 0.0) * 100,
    )


class OpenAICompatibleLLMClient(ILLMClient):
    """
    OpenAI 协议兼容的 LLM 客户端实现
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
    ):
        settings = load_settings()
        self.api_key = (api_key if api_key is not None else settings.llm_api_key).strip()
        raw_base_url = (base_url or settings.llm_base_url).strip().rstrip("/")
        # 容错处理：若用户在 base_url 中误填了 /chat/completions，自动截断还原为基准 URL
        if raw_base_url.endswith("/chat/completions"):
            raw_base_url = raw_base_url[:-len("/chat/completions")].rstrip("/")
        self.base_url = raw_base_url
        self.model = (model or settings.llm_model).strip()
        self.timeout = timeout

    def _generate_fallback_chunks(self, messages: list[dict[str, Any]]) -> list[str]:
        user_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_text = msg.get("content", "")
                if isinstance(user_text, str):
                    user_text = user_text.strip()
                else:
                    user_text = str(user_text)
                break

        clean_user = user_text.lower()
        is_greeting = clean_user in {
            "你好", "您好", "hi", "hello", "hey", "在吗", "在么",
            "你是谁", "你叫什么", "介绍一下自己", "介绍下自己", "你能做什么",
            "你会做什么", "有什么功能", "帮助", "help",
        } or bool(re.match(r"^(你好|您好|hi|hello|hey|在吗|在么|你是谁|你叫什么|介绍一下自己?|你能做[什甚]么|help)[\s!！?？~～]*$", clean_user))

        ops_keywords = [
            "ping", "ssh", "linux", "cpu", "内存", "磁盘", "网络", "端口", "服务", "进程",
            "nginx", "docker", "mysql", "redis", "node", "java", "top", "free", "df", "ps", "ss",
            "netstat", "systemctl", "journalctl", "kill", "iptables", "firewall", "ip", "cat", "tail",
            "grep", "find", "chmod", "chown", "sudo", "重启", "排障", "故障", "报错", "诊断",
            "负载", "挂载", "oom", "502", "404", "超时", "连接", "无法", "异常", "挂了",
            "write failed", "status", "log", "日志", "空间", "带宽", "uptime", "uname", "bash", "sh"
        ]
        is_ops = any(k in clean_user for k in ops_keywords)

        if is_greeting:
            return [
                "您好！我是 SmartOps AI 运维智能副驾驶，随时为您提供运维与系统管理支持。请问有什么可以协助您的？\n",
            ]

        if not is_ops and user_text:
            return [
                "您好！该提问与当前 Linux 运维无直接关联。请问有什么我可以协助您的运维需求吗？如需协助管理服务器或排查故障，请随时告诉我。\n",
            ]

        return [
            "> **OpsAgent 未配置 LLM_API_KEY**\n",
            "> 请在环境变量中配置 `LLM_API_KEY`（以及可选的 `LLM_BASE_URL` 和 `LLM_MODEL`）以启用真实大模型流式推理。\n",
            "> 以下为本地演示模式生成的排查建议：\n\n",
            "### 1. 根因分析 (Diagnosis)\n",
            "当前处于离线/演示模式。系统建议对主机的关键运行健康指标（资源水位、服务状态、连接数）展开只读探测。\n\n",
            "### 2. 建议指令 (Proposed Commands)\n",
            "```bash\n",
            "# 步骤 1：检查负载与运行时间\n",
            "uptime\n\n",
            "# 步骤 2：检查内存可用余量与 Swap 消耗\n",
            "free -h\n\n",
            "# 步骤 3：检查根分区及挂载点磁盘使用率\n",
            "df -h\n",
            "```\n\n",
            "### 3. 风险评估 (Risk & Impact)\n",
            "- **风险等级**: SAFE\n",
            "- **潜在影响**: 仅读取物理指标，对生产环境无任何破坏性或状态变更。\n\n",
            "### 4. 效果验证 (Verification)\n",
            "```bash\n",
            "uname -a\n",
            "```\n",
        ]

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.2,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        通过 SSE 流式向大模型发起对话请求，并逐 token / chunk 输出
        """
        if not self.api_key.strip():
            logger.warning("LLM_API_KEY is not configured. Falling back to graceful demonstration mode.")
            for chunk in self._generate_fallback_chunks(messages):
                yield chunk
            return

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        # 请求上游在流式结束时回传 usage，用于统计 Token 与 Prompt Caching 命中情况
        payload["stream_options"] = {"include_usage": True}

        enable_thinking = load_settings().llm_enable_thinking
        is_reasoning_model = any(k in self.model.lower() for k in ["glm-4.7", "deepseek-r1", "reasoner", "qwq"])
        if enable_thinking or is_reasoning_model:
            payload["thinking"] = {"type": "enabled"}

        url = f"{self.base_url}/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    if response.status_code != 200:
                        error_bytes = await response.aread()
                        err_text = error_bytes.decode("utf-8", errors="ignore")
                        logger.error("LLM upstream error HTTP %s: %s", response.status_code, err_text)
                        yield f"> **大模型上游接口请求异常 (HTTP {response.status_code})**\n>\n> {err_text}"
                        return

                    in_thinking = False
                    stream_usage: dict[str, Any] = {}
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or line.startswith(":"):
                            continue
                        if line.startswith("data: "):
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk_json = json.loads(data_str)
                                chunk_usage = chunk_json.get("usage")
                                if chunk_usage:
                                    stream_usage = normalize_usage(chunk_usage)
                                choices = chunk_json.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                                    content = delta.get("content")

                                    if reasoning:
                                        if not in_thinking:
                                            in_thinking = True
                                            yield "<think>"
                                        yield reasoning

                                    if content:
                                        if in_thinking:
                                            in_thinking = False
                                            yield "</think>\n\n"
                                        yield content
                            except json.JSONDecodeError:
                                continue

                    if in_thinking:
                        yield "</think>\n\n"

                    _log_usage("chat.stream", self.model, stream_usage)
        except Exception as exc:
            logger.exception("Network exception during LLM streaming: %s", str(exc))
            yield f"> **OpsAgent 请求网络异常**: {str(exc)}"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.2,
        tools: list[dict[str, Any]] | None = None,
        on_token: Callable[[str], Any] | None = None,
    ) -> LLMResponse:
        """
        获取大模型响应，支持提取结构化 Tool Calling；传入 on_token 时启用流式推演
        """
        if not self.api_key.strip():
            logger.warning("LLM_API_KEY is not configured. Falling back to graceful demonstration mode.")
            fallback_chunks = self._generate_fallback_chunks(messages)
            if on_token:
                for chunk in fallback_chunks:
                    res = on_token(chunk)
                    if asyncio.iscoroutine(res):
                        await res
            return LLMResponse(content="".join(fallback_chunks))

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True if on_token else False,
        }
        if tools:
            payload["tools"] = tools
        if on_token:
            # 流式模式下请求上游回传 usage，用于统计缓存命中
            payload["stream_options"] = {"include_usage": True}

        enable_thinking = load_settings().llm_enable_thinking
        is_reasoning_model = any(k in self.model.lower() for k in ["glm-4.7", "deepseek-r1", "reasoner", "qwq"])
        if enable_thinking or is_reasoning_model:
            payload["thinking"] = {"type": "enabled"}

        url = f"{self.base_url}/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if on_token:
                    async with client.stream("POST", url, headers=headers, json=payload) as response:
                        if response.status_code != 200:
                            err_bytes = await response.aread()
                            err_text = err_bytes.decode("utf-8", errors="ignore")
                            logger.error("LLM upstream error HTTP %s: %s", response.status_code, err_text)
                            return LLMResponse(content=f"> **大模型上游接口请求异常 (HTTP {response.status_code})**\n>\n> {err_text}")

                        content_parts: list[str] = []
                        reasoning_parts: list[str] = []
                        tool_calls_map: dict[int, dict[str, Any]] = {}
                        stream_usage: dict[str, Any] = {}

                        async for line in response.aiter_lines():
                            line = line.strip()
                            if not line or line.startswith(":"):
                                continue
                            if line.startswith("data: "):
                                data_str = line[6:].strip()
                                if data_str == "[DONE]":
                                    break
                                try:
                                    chunk_json = json.loads(data_str)
                                    chunk_usage = chunk_json.get("usage")
                                    if chunk_usage:
                                        stream_usage = normalize_usage(chunk_usage)
                                    choices = chunk_json.get("choices") or []
                                    if not choices:
                                        continue
                                    delta = choices[0].get("delta") or {}

                                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                                    if reasoning:
                                        reasoning_parts.append(reasoning)
                                        res = on_token(reasoning)
                                        if asyncio.iscoroutine(res):
                                            await res

                                    content = delta.get("content")
                                    if content:
                                        content_parts.append(content)
                                        res = on_token(content)
                                        if asyncio.iscoroutine(res):
                                            await res

                                    delta_tool_calls = delta.get("tool_calls") or []
                                    for tc in delta_tool_calls:
                                        idx = tc.get("index", 0)
                                        if idx not in tool_calls_map:
                                            tool_calls_map[idx] = {
                                                "id": tc.get("id") or "",
                                                "name": "",
                                                "arguments_chunks": [],
                                                "thought_extracted": "",
                                            }
                                        if tc.get("id"):
                                            tool_calls_map[idx]["id"] = tc["id"]
                                        fn = tc.get("function") or {}
                                        if fn.get("name"):
                                            tool_calls_map[idx]["name"] = fn["name"]
                                        if fn.get("arguments"):
                                            tool_calls_map[idx]["arguments_chunks"].append(fn["arguments"])
                                            if not content_parts and not reasoning_parts and on_token:
                                                curr_raw = "".join(tool_calls_map[idx]["arguments_chunks"])
                                                tm = re.search(r'"thought"\s*:\s*"((?:[^"\\]|\\.)*)', curr_raw)
                                                if tm:
                                                    val = tm.group(1).replace('\\"', '"').replace('\\n', '\n')
                                                    prev_extracted = tool_calls_map[idx].get("thought_extracted", "")
                                                    if len(val) > len(prev_extracted):
                                                        thought_delta = val[len(prev_extracted):]
                                                        tool_calls_map[idx]["thought_extracted"] = val
                                                        res = on_token(thought_delta)
                                                        if asyncio.iscoroutine(res):
                                                            await res
                                except json.JSONDecodeError:
                                    continue

                        full_content = "".join(content_parts) if content_parts else None
                        full_reasoning = "".join(reasoning_parts) if reasoning_parts else None

                        tool_calls: list[ToolCall] = []
                        for idx in sorted(tool_calls_map.keys()):
                            tc_info = tool_calls_map[idx]
                            raw_args = "".join(tc_info["arguments_chunks"])
                            args: dict[str, Any] = {}
                            if raw_args.strip():
                                try:
                                    args = json.loads(raw_args)
                                except Exception:
                                    args = {"command": raw_args}
                            tool_calls.append(
                                ToolCall(
                                    id=tc_info["id"],
                                    name=tc_info["name"],
                                    arguments=args,
                                )
                            )

                        _log_usage("react.stream", self.model, stream_usage)
                        return LLMResponse(
                            content=full_content,
                            tool_calls=tool_calls,
                            reasoning_content=full_reasoning,
                            usage=stream_usage,
                        )

                # 非流式直接请求
                response = await client.post(url, headers=headers, json=payload)
                if response.status_code != 200:
                    err_text = response.text
                    logger.error("LLM upstream error HTTP %s: %s", response.status_code, err_text)
                    return LLMResponse(content=f"> **大模型上游接口请求异常 (HTTP {response.status_code})**\n>\n> {err_text}")

                res_json = response.json()
                usage = normalize_usage(res_json.get("usage"))
                choices = res_json.get("choices") or []
                if not choices:
                    return LLMResponse(content="", usage=usage)

                msg = choices[0].get("message") or {}
                content = msg.get("content")
                raw_tool_calls = msg.get("tool_calls") or []

                tool_calls: list[ToolCall] = []
                for tc in raw_tool_calls:
                    fn = tc.get("function") or {}
                    raw_args = fn.get("arguments", "{}")
                    if isinstance(raw_args, str):
                        try:
                            args = json.loads(raw_args)
                        except Exception:
                            args = {"command": raw_args}
                    elif isinstance(raw_args, dict):
                        args = raw_args
                    else:
                        args = {}
                    tool_calls.append(
                        ToolCall(
                            id=tc.get("id", ""),
                            name=fn.get("name", ""),
                            arguments=args,
                        )
                    )

                reasoning = msg.get("reasoning_content") or msg.get("reasoning")
                _log_usage("react.chat", self.model, usage)
                return LLMResponse(
                    content=content,
                    tool_calls=tool_calls,
                    reasoning_content=reasoning,
                    usage=usage,
                )
        except Exception as exc:
            logger.exception("Network exception during LLM chat: %s", str(exc))
            return LLMResponse(content=f"> **OpsAgent 请求网络异常**: {str(exc)}")
