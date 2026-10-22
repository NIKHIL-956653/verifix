"""
The model boundary.

The agent depends on the LLM protocol below, never on the Anthropic SDK directly. That
one indirection is what lets every test run against a scripted FakeLLM with no network,
and lets the M7 benchmark replay recorded responses.

Messages use the Anthropic content-block shape ({"role": ..., "content": [...]}) because
that is the only provider in scope; ADR-004 records why we did not invent a neutral
format we would have nothing to test against.

Errors are a small closed set the agent loop can act on. Provider error text is kept out
of them on purpose: a vendor message can contain request details, and the agent only
needs to know which bucket it is in to decide retry / stop / ask the user.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from verifix.llm.pricing import cost_usd


class LLMError(RuntimeError):
    """A model call failed in a way that is not one of the specific cases below."""


class LLMRateLimited(LLMError):
    """429. Transient - retried with backoff."""


class LLMUnavailable(LLMError):
    """Overloaded, 5xx, timeout or connection failure. Transient - retried with backoff."""


class LLMCreditExhausted(LLMError):
    """The account has no credit left. Not transient - the task must stop."""


class LLMAuthError(LLMError):
    """Missing or rejected API key. Not transient."""


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may request. input_schema is JSON Schema."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """A tool request from the model. Nothing has been executed when this exists."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def cost_usd(self, model: str) -> float:
        return cost_usd(
            model,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_read_tokens=self.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens,
        )


@dataclass(frozen=True)
class LLMResponse:
    text: str
    tool_calls: tuple[ToolCall, ...]
    stop_reason: str
    model: str
    usage: Usage = field(default_factory=Usage)
    raw_content: tuple[dict[str, Any], ...] = ()
    """Assistant content blocks as returned, to append to the conversation unchanged."""

    @property
    def cost_usd(self) -> float:
        return self.usage.cost_usd(self.model)


@runtime_checkable
class LLM(Protocol):
    async def respond(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> LLMResponse: ...
