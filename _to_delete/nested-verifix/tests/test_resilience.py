import asyncio
import random

import pytest

from verifix.llm.base import LLMCreditExhausted, LLMRateLimited, LLMUnavailable
from verifix.llm.resilience import CircuitBreaker, CircuitOpenError, call_with_retry


def _transient(exc: BaseException) -> bool:
    return isinstance(exc, LLMRateLimited | LLMUnavailable)


class Flaky:
    def __init__(self, failures: list[BaseException], result: str = "ok") -> None:
        self.failures = list(failures)
        self.result = result
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return self.result


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def _no_sleep(_s: float) -> None:
    return None


async def test_transient_errors_are_retried_until_success() -> None:
    op = Flaky([LLMRateLimited(), LLMUnavailable()])
    sleeps: list[float] = []

    async def record(s: float) -> None:
        sleeps.append(s)

    assert (
        await call_with_retry(
            op, is_transient=_transient, attempts=4, sleep=record, rng=random.Random(1)
        )
        == "ok"
    )
    assert op.calls == 3
    assert len(sleeps) == 2
    assert sleeps[0] <= 1.0 and sleeps[1] <= 2.0  # full jitter under an exponential cap


async def test_non_transient_error_is_not_retried() -> None:
    op = Flaky([LLMCreditExhausted()])
    with pytest.raises(LLMCreditExhausted):
        await call_with_retry(op, is_transient=_transient, attempts=4, sleep=_no_sleep)
    assert op.calls == 1


async def test_gives_up_after_the_attempt_limit() -> None:
    op = Flaky([LLMUnavailable()] * 10)
    with pytest.raises(LLMUnavailable):
        await call_with_retry(op, is_transient=_transient, attempts=3, sleep=_no_sleep)
    assert op.calls == 3


async def test_cancellation_is_never_swallowed() -> None:
    op = Flaky([asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        await call_with_retry(op, is_transient=lambda e: True, attempts=4, sleep=_no_sleep)
    assert op.calls == 1


async def test_breaker_opens_then_fails_fast_then_probes() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=3, cooldown_s=30, clock=clock)
    down = Flaky([LLMUnavailable()] * 3)
    with pytest.raises(LLMUnavailable):
        await call_with_retry(
            down, is_transient=_transient, attempts=3, breaker=breaker, sleep=_no_sleep
        )
    assert breaker.state == "open"

    healthy = Flaky([])
    with pytest.raises(CircuitOpenError):
        await call_with_retry(
            healthy, is_transient=_transient, attempts=1, breaker=breaker, sleep=_no_sleep
        )
    assert healthy.calls == 0  # failed fast, the provider was not even called

    clock.now += 31
    assert breaker.state == "half_open"
    assert (
        await call_with_retry(
            healthy, is_transient=_transient, attempts=1, breaker=breaker, sleep=_no_sleep
        )
        == "ok"
    )
    assert breaker.state == "closed"


async def test_failed_probe_reopens_for_a_full_cooldown() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, cooldown_s=30, clock=clock)
    breaker.record_failure()
    clock.now += 31
    assert breaker.state == "half_open"
    breaker.record_failure()
    assert breaker.state == "open"
    clock.now += 29
    assert breaker.state == "open"


async def test_non_transient_errors_do_not_trip_the_breaker() -> None:
    breaker = CircuitBreaker(failure_threshold=1, cooldown_s=30, clock=FakeClock())
    with pytest.raises(LLMCreditExhausted):
        await call_with_retry(
            Flaky([LLMCreditExhausted()]), is_transient=_transient, breaker=breaker, sleep=_no_sleep
        )
    assert breaker.state == "closed"
