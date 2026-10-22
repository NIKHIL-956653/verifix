"""
Record / replay of model responses, so a benchmark run can be reproduced for free.

Record: wrap the real model; every response is appended to a JSONL cassette.
Replay: serve the recorded responses in order, with no network and no cost.

Replay matches by position, not by hashing the request: tool results contain timings and
paths that differ run to run, so an exact-request match would never hit. Instead each entry
remembers how long the conversation was when it was recorded, and replay refuses to
continue if the agent has drifted (different conversation length = it did something else).
That is reported as a model error, so the task fails loudly instead of grading a run that
silently diverged.
"""

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from verifix.llm.base import LLM, LLMError, LLMResponse, ToolCall, ToolSpec, Usage


class CassetteMismatch(LLMError):
    pass


def response_to_dict(r: LLMResponse) -> dict[str, Any]:
    return {
        "text": r.text,
        "tool_calls": [asdict(c) for c in r.tool_calls],
        "stop_reason": r.stop_reason,
        "model": r.model,
        "usage": asdict(r.usage),
        "raw_content": list(r.raw_content),
    }


def response_from_dict(d: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        text=d["text"],
        tool_calls=tuple(ToolCall(**c) for c in d["tool_calls"]),
        stop_reason=d["stop_reason"],
        model=d["model"],
        usage=Usage(**d["usage"]),
        raw_content=tuple(d["raw_content"]),
    )


class RecordingLLM:
    def __init__(self, inner: LLM, path: Path) -> None:
        self.inner = inner
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    async def respond(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> LLMResponse:
        resp = await self.inner.respond(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens, model=model
        )
        entry = {"n_messages": len(messages), "response": response_to_dict(resp)}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return resp


class ReplayLLM:
    def __init__(self, path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(f"no cassette at {path}")
        self.path = path
        self._entries = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self._i = 0

    async def respond(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> LLMResponse:
        if self._i >= len(self._entries):
            raise CassetteMismatch(f"cassette {self.path.name} has no response #{self._i + 1}")
        entry = self._entries[self._i]
        if entry["n_messages"] != len(messages):
            raise CassetteMismatch(
                f"replay diverged at response #{self._i + 1}: recorded with "
                f"{entry['n_messages']} messages, now {len(messages)}"
            )
        self._i += 1
        return response_from_dict(entry["response"])
