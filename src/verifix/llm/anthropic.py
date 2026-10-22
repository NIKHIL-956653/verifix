"""
Anthropic implementation of the LLM protocol.

The SDK's own retries are switched off (max_retries=0) so there is exactly one retry
policy in the system - ours, with the circuit breaker - and its behaviour is tested.

Prompt caching: the system prompt and the tool definitions are identical on every turn
of an agent loop, so both are marked with cache_control. On a long investigation that
turns most of each turn's input into a cache read at 10% of the price.

Note: Claude 5.x models take no `temperature` parameter, so none is sent.
"""

from collections.abc import Sequence
from typing import Any

import anthropic
import httpx2

from verifix.llm.base import (
    LLMAuthError,
    LLMCreditExhausted,
    LLMError,
    LLMRateLimited,
    LLMResponse,
    LLMUnavailable,
    ToolCall,
    ToolSpec,
    Usage,
)
from verifix.llm.resilience import CircuitBreaker, call_with_retry

_CACHE = {"type": "ephemeral"}


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, LLMRateLimited | LLMUnavailable)


def _classify(exc: Exception) -> LLMError:
    """Map SDK exceptions onto our small closed set. Vendor text is not propagated."""
    if isinstance(exc, anthropic.RateLimitError):
        return LLMRateLimited("rate limited")
    if isinstance(exc, anthropic.APITimeoutError | anthropic.APIConnectionError):
        return LLMUnavailable("could not reach the model provider")
    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return LLMAuthError("API key missing or rejected")
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:
            return LLMUnavailable(f"provider error {exc.status_code}")
        message = str(getattr(exc, "message", "") or "").lower()
        if "credit balance" in message or "billing" in message:
            return LLMCreditExhausted("Anthropic credit exhausted")
        return LLMError(f"request rejected ({exc.status_code})")
    return LLMError(type(exc).__name__)


def _tool_param(spec: ToolSpec) -> dict[str, Any]:
    return {"name": spec.name, "description": spec.description, "input_schema": spec.input_schema}


class AnthropicLLM:
    def __init__(
        self,
        *,
        api_key: str,
        default_model: str,
        timeout_s: float = 120.0,
        max_attempts: int = 4,
        breaker: CircuitBreaker | None = None,
        http_client: httpx2.AsyncClient | None = None,
        retry_sleep: Any = None,
    ) -> None:
        if not api_key:
            raise LLMAuthError("API key missing or rejected")
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=timeout_s,
            max_retries=0,
            http_client=http_client,
        )
        self._default_model = default_model
        self._max_attempts = max_attempts
        self._breaker = breaker or CircuitBreaker()
        self._retry_kwargs: dict[str, Any] = {} if retry_sleep is None else {"sleep": retry_sleep}

    async def respond(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> LLMResponse:
        model = model or self._default_model
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": _CACHE}],
            "messages": list(messages),
        }
        if tools:
            params = [_tool_param(t) for t in tools]
            params[-1] = {**params[-1], "cache_control": _CACHE}  # caches every tool before it
            kwargs["tools"] = params

        async def once() -> Any:
            try:
                return await self._client.messages.create(**kwargs)
            except anthropic.AnthropicError as exc:
                raise _classify(exc) from None  # `from None`: keep vendor detail out of logs

        msg = await call_with_retry(
            once,
            is_transient=_is_transient,
            attempts=self._max_attempts,
            breaker=self._breaker,
            **self._retry_kwargs,
        )
        return _to_response(msg, model)

    async def aclose(self) -> None:
        await self._client.close()


def _to_response(msg: Any, requested_model: str) -> LLMResponse:
    texts: list[str] = []
    calls: list[ToolCall] = []
    raw: list[dict[str, Any]] = []
    for block in msg.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            texts.append(block.text)
            raw.append({"type": "text", "text": block.text})
        elif btype == "tool_use":
            tool_input = dict(block.input) if isinstance(block.input, dict) else {}
            calls.append(ToolCall(id=block.id, name=block.name, input=tool_input))
            raw.append(
                {"type": "tool_use", "id": block.id, "name": block.name, "input": tool_input}
            )
    u = msg.usage
    usage = Usage(
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )
    return LLMResponse(
        text="".join(texts),
        tool_calls=tuple(calls),
        stop_reason=str(msg.stop_reason or ""),
        model=str(getattr(msg, "model", None) or requested_model),
        usage=usage,
        raw_content=tuple(raw),
    )
