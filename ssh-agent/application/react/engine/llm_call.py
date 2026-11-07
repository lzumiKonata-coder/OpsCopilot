"""应用层 - ReAct 引擎 LLM 调用能力"""

import asyncio
from typing import Any, Callable

from application.ports import ILLMClient
from application.react.cancel_context import get_cancel_check


class LLMCallMixin:
    """大模型安全调用：能力探测、参数降级与中途取消"""

    @staticmethod
    def _detect_llm_capabilities(llm_client: ILLMClient) -> tuple:
        """探测 LLM 客户端是否原生支持 tools / on_token 参数"""
        import inspect

        try:
            sig = inspect.signature(llm_client.chat)
            params = sig.parameters
            has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
            return (has_var_kw or "tools" in params, has_var_kw or "on_token" in params)
        except Exception:
            return True, True

    async def _safe_llm_chat(
        self,
        conversation: list[dict[str, Any]],
        temperature: float = 0.1,
        tools: list[dict[str, Any]] | None = None,
        on_token: Callable[[str], Any] | None = None,
        cancel_check: Callable[[], Any] | None = None,
    ) -> Any:
        """安全调用大模型：自动按客户端签名或降级机制传入 tools / on_token，支持异步流式与长时间调用中途即时取消打断"""
        eff_cancel_check = cancel_check or get_cancel_check()
        if eff_cancel_check and await eff_cancel_check():
            raise asyncio.CancelledError("客户端连接已断开或用户主动取消")

        chat_fn = self.llm_client.chat
        supports_tools = self._llm_supports_tools
        supports_on_token = self._llm_supports_on_token

        is_aborted = False

        def wrapped_on_token(token: str):
            if is_aborted:
                raise asyncio.CancelledError("客户端连接已断开或用户主动取消")
            if on_token is not None:
                on_token(token)

        kwargs: dict[str, Any] = {"temperature": temperature}
        if tools is not None and supports_tools:
            kwargs["tools"] = tools
        if on_token is not None and supports_on_token:
            kwargs["on_token"] = wrapped_on_token if eff_cancel_check else on_token

        async def _invoke_chat():
            try:
                return await chat_fn(conversation, **kwargs)
            except TypeError:
                kwargs.pop("on_token", None)
                try:
                    return await chat_fn(conversation, **kwargs)
                except TypeError:
                    kwargs.pop("tools", None)
                    try:
                        return await chat_fn(conversation, **kwargs)
                    except TypeError:
                        return await chat_fn(conversation)

        if not eff_cancel_check:
            return await _invoke_chat()

        chat_task = asyncio.create_task(_invoke_chat())
        stop_watch = asyncio.Event()

        async def _watch_cancel():
            while not stop_watch.is_set():
                try:
                    if await eff_cancel_check():
                        return True
                except Exception:
                    pass
                await asyncio.sleep(0.1)
            return False

        watch_task = asyncio.create_task(_watch_cancel())
        done, pending = await asyncio.wait(
            [chat_task, watch_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        stop_watch.set()
        is_aborted = True

        for p in pending:
            p.cancel()
            try:
                await p
            except BaseException:
                pass

        if chat_task not in done:
            raise asyncio.CancelledError("客户端连接已断开或用户主动取消排障流程")

        return chat_task.result()
