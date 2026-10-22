import pytest

from verifix.llm import LLM, LLMRateLimited, ToolSpec, Usage
from verifix.llm.fake import FakeLLM, FakeLLMExhausted, text_response, tool_response
from verifix.llm.pricing import UnknownModelPrice, cost_usd

READ = ToolSpec("read_source_file", "Read a file", {"type": "object", "properties": {}})


def test_fake_satisfies_the_protocol() -> None:
    assert isinstance(FakeLLM([]), LLM)


async def test_scripted_responses_in_order_and_requests_recorded() -> None:
    llm = FakeLLM([tool_response("read_source_file", {"path": "app.py"}), text_response("done")])
    first = await llm.respond(
        system="sys", messages=[{"role": "user", "content": "hi"}], tools=[READ]
    )
    assert first.tool_calls[0].name == "read_source_file"
    assert first.tool_calls[0].input == {"path": "app.py"}
    second = await llm.respond(system="sys", messages=[])
    assert second.text == "done"
    assert llm.calls[0].tools == ["read_source_file"]
    assert llm.calls[0].messages == [{"role": "user", "content": "hi"}]
    assert llm.remaining == 0


async def test_running_out_of_script_fails_loudly() -> None:
    llm = FakeLLM([text_response("only one")])
    await llm.respond(system="s", messages=[])
    with pytest.raises(FakeLLMExhausted):
        await llm.respond(system="s", messages=[])


async def test_scripted_errors_are_raised() -> None:
    llm = FakeLLM([LLMRateLimited("slow down")])
    with pytest.raises(LLMRateLimited):
        await llm.respond(system="s", messages=[])


def test_cost_matches_list_prices() -> None:
    # Sonnet 5.5: $2 in / $10 out per MTok.
    assert cost_usd(
        "claude-sonnet-5-5", input_tokens=1_000_000, output_tokens=100_000
    ) == pytest.approx(3.0)
    # Cache reads at 10%, writes at 125% of input.
    assert cost_usd("claude-sonnet-5-5", cache_read_tokens=1_000_000) == pytest.approx(0.20)
    assert cost_usd("claude-sonnet-5-5", cache_write_tokens=1_000_000) == pytest.approx(2.50)
    # Dated snapshot names resolve to the family price.
    assert cost_usd("claude-haiku-5-5-20260915", input_tokens=1_000_000) == pytest.approx(0.10)
    assert Usage(input_tokens=1000, output_tokens=1000).cost_usd(
        "claude-opus-5-5"
    ) == pytest.approx(0.024)


def test_unknown_model_price_is_an_error_not_free() -> None:
    with pytest.raises(UnknownModelPrice):
        cost_usd("some-other-model", input_tokens=10)
