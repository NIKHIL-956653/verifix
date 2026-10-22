"""
The Anthropic adapter against a mocked HTTP transport.

No network: httpx.MockTransport answers in-process. These tests pin the request shape we
send (caching, tools, no temperature) and the response parsing, so an SDK upgrade that
changes either shows up here instead of in a live agent run.
"""

import json

import httpx2 as httpx  # the Anthropic SDK 2.x transport package (API-compatible with httpx)
import pytest

from verifix.llm import (
    LLMAuthError,
    LLMCreditExhausted,
    LLMError,
    LLMRateLimited,
    LLMUnavailable,
    ToolSpec,
)
from verifix.llm.anthropic import AnthropicLLM
from verifix.llm.resilience import CircuitBreaker

KEY = "sk-ant-test-0000000000000000"
TOOL = ToolSpec(
    "read_source_file",
    "Read a file from the repository",
    {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
)

OK_BODY = {
    "id": "msg_01",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-5-5",
    "content": [
        {"type": "text", "text": "Reading the handler first."},
        {
            "type": "tool_use",
            "id": "toolu_01",
            "name": "read_source_file",
            "input": {"path": "app/auth.py"},
        },
    ],
    "stop_reason": "tool_use",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 1200,
        "output_tokens": 80,
        "cache_read_input_tokens": 3000,
        "cache_creation_input_tokens": 0,
    },
}


def _error(status: int, etype: str, message: str) -> httpx.Response:
    return httpx.Response(
        status, json={"type": "error", "error": {"type": etype, "message": message}}
    )


class Server:
    """Replays a list of responses and records each request body."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        return self.responses.pop(0)


async def _no_sleep(_s: float) -> None:
    return None


def _llm(server: Server, **kw) -> AnthropicLLM:
    client = httpx.AsyncClient(transport=httpx.MockTransport(server.handler))
    return AnthropicLLM(
        api_key=KEY,
        default_model="claude-sonnet-5-5",
        http_client=client,
        retry_sleep=_no_sleep,
        breaker=kw.pop("breaker", CircuitBreaker(failure_threshold=100)),
        **kw,
    )


async def test_parses_text_tool_calls_and_usage() -> None:
    server = Server([httpx.Response(200, json=OK_BODY)])
    resp = await _llm(server).respond(
        system="You are verifix.", messages=[{"role": "user", "content": "why 500?"}], tools=[TOOL]
    )
    assert resp.text == "Reading the handler first."
    assert len(resp.tool_calls) == 1
    call = resp.tool_calls[0]
    assert (call.id, call.name, call.input) == (
        "toolu_01",
        "read_source_file",
        {"path": "app/auth.py"},
    )
    assert resp.stop_reason == "tool_use"
    assert resp.usage.input_tokens == 1200
    assert resp.usage.cache_read_tokens == 3000
    # 1200*2 + 80*10 + 3000*0.2 = 3800 per million
    assert resp.cost_usd == pytest.approx(0.0038)
    assert resp.raw_content[1]["type"] == "tool_use"


async def test_request_shape_caching_and_no_temperature() -> None:
    server = Server([httpx.Response(200, json=OK_BODY)])
    await _llm(server).respond(
        system="SYS", messages=[{"role": "user", "content": "x"}], tools=[TOOL], max_tokens=512
    )
    body = server.requests[0]
    assert body["model"] == "claude-sonnet-5-5"
    assert body["max_tokens"] == 512
    assert body["system"] == [
        {"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}
    ]
    assert body["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert body["tools"][0]["input_schema"]["required"] == ["path"]
    assert "temperature" not in body


async def test_no_tools_key_when_no_tools_offered() -> None:
    server = Server([httpx.Response(200, json=OK_BODY)])
    await _llm(server).respond(system="S", messages=[{"role": "user", "content": "x"}])
    assert "tools" not in server.requests[0]


async def test_rate_limit_is_retried_then_succeeds() -> None:
    server = Server(
        [_error(429, "rate_limit_error", "slow down"), httpx.Response(200, json=OK_BODY)]
    )
    resp = await _llm(server).respond(system="S", messages=[{"role": "user", "content": "x"}])
    assert resp.stop_reason == "tool_use"
    assert len(server.requests) == 2


async def test_rate_limit_exhausts_attempts() -> None:
    server = Server([_error(429, "rate_limit_error", "slow down")] * 3)
    with pytest.raises(LLMRateLimited):
        await _llm(server, max_attempts=3).respond(
            system="S", messages=[{"role": "user", "content": "x"}]
        )
    assert len(server.requests) == 3


@pytest.mark.parametrize("status", [500, 529])
async def test_server_errors_are_unavailable_and_retried(status: int) -> None:
    server = Server([_error(status, "overloaded_error", "busy"), httpx.Response(200, json=OK_BODY)])
    await _llm(server).respond(system="S", messages=[{"role": "user", "content": "x"}])
    assert len(server.requests) == 2

    server = Server([_error(status, "overloaded_error", "busy")] * 2)
    with pytest.raises(LLMUnavailable):
        await _llm(server, max_attempts=2).respond(
            system="S", messages=[{"role": "user", "content": "x"}]
        )


async def test_credit_exhausted_is_not_retried() -> None:
    server = Server(
        [
            _error(
                400,
                "invalid_request_error",
                "Your credit balance is too low to access the Anthropic API.",
            )
        ]
    )
    with pytest.raises(LLMCreditExhausted):
        await _llm(server).respond(system="S", messages=[{"role": "user", "content": "x"}])
    assert len(server.requests) == 1


async def test_bad_key_is_auth_error_and_not_retried() -> None:
    server = Server([_error(401, "authentication_error", "invalid x-api-key")])
    with pytest.raises(LLMAuthError):
        await _llm(server).respond(system="S", messages=[{"role": "user", "content": "x"}])
    assert len(server.requests) == 1


async def test_other_client_errors_are_generic_and_carry_no_vendor_text() -> None:
    server = Server([_error(400, "invalid_request_error", "messages.0: secret detail from vendor")])
    with pytest.raises(LLMError) as info:
        await _llm(server).respond(system="S", messages=[{"role": "user", "content": "x"}])
    assert type(info.value) is LLMError
    assert "secret detail" not in str(info.value)
    assert info.value.__cause__ is None  # raised `from None`


def test_empty_key_is_rejected_up_front() -> None:
    with pytest.raises(LLMAuthError):
        AnthropicLLM(api_key="", default_model="claude-sonnet-5-5")
