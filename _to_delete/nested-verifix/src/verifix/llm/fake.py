"""
A scripted LLM for tests and offline demos.

Give it the responses it should return, in order. It records every request so a test can
assert what the agent actually sent (system prompt, tools offered, conversation so far).
Running out of scripted responses is an error, not a silent default: a test that makes
more model calls than it expected should fail loudly.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from verifix.llm.base import LLMError, LLMResponse, ToolCall, ToolSpec, Usage


@dataclass
class RecordedCall:
    system: str
    messages: list[dict[str, Any]]
    tools: list[str]
    max_tokens: int
    model: str


class FakeLLMExhausted(AssertionError):
    pass


def text_response(
    text: str, *, model: str = "claude-sonnet-5-5", usage: Usage | None = None
) -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=(),
        stop_reason="end_turn",
        model=model,
        usage=usage or Usage(input_tokens=100, output_tokens=20),
        raw_content=({"type": "text", "text": text},),
    )


def tool_response(
    name: str,
    tool_input: dict[str, Any],
    *,
    call_id: str = "toolu_fake_1",
    model: str = "claude-sonnet-5-5",
    usage: Usage | None = None,
) -> LLMResponse:
    return LLMResponse(
        text="",
        tool_calls=(ToolCall(id=call_id, name=name, input=tool_input),),
        stop_reason="tool_use",
        model=model,
        usage=usage or Usage(input_tokens=100, output_tokens=20),
        raw_content=({"type": "tool_use", "id": call_id, "name": name, "input": tool_input},),
    )


class FakeLLM:
    def __init__(
        self, script: Sequence[LLMResponse | LLMError], default_model: str = "claude-sonnet-5-5"
    ):
        self._script = list(script)
        self._default_model = default_model
        self.calls: list[RecordedCall] = []

    async def respond(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> LLMResponse:
        self.calls.append(
            RecordedCall(
                system=system,
                messages=[dict(m) for m in messages],
                tools=[t.name for t in tools],
                max_tokens=max_tokens,
                model=model or self._default_model,
            )
        )
        if not self._script:
            raise FakeLLMExhausted(
                f"FakeLLM ran out of scripted responses after {len(self.calls) - 1} calls"
            )
        nxt = self._script.pop(0)
        if isinstance(nxt, LLMError):
            raise nxt
        return nxt

    @property
    def remaining(self) -> int:
        return len(self._script)
