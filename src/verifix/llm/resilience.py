"""
Bounded retry with jitter, and a circuit breaker.

Ported from eap (EAP-45) with one change: the clock and sleep are injectable, so the
tests prove the timing behaviour without actually waiting.

- Retry absorbs a dropped delivery (one 429, one timeout) without failing the task.
- The breaker absorbs an outage: after `failure_threshold` consecutive transient
  failures every call fails immediately for `cooldown_s`, then one probe is allowed
  through. A task that cannot reach the provider stops quickly and says so, instead of
  burning its time budget on timeouts.

Non-transient errors (no credit, bad key, bad request) are never retried and do not
trip the breaker - they are about this request or this account, not the provider's health.
"""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable

from verifix.llm.base import LLMUnavailable


class CircuitOpenError(LLMUnavailable):
    """The provider failed repeatedly moments ago; failing fast until the cooldown ends."""


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        cooldown_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self.cooldown_s:
            return "half_open"
        return "open"

    def before_call(self) -> None:
        if self.state == "open":
            raise CircuitOpenError("model provider circuit is open")

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        if self.state == "half_open":
            self._opened_at = self._clock()  # probe failed: stay open for another cooldown
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = self._clock()


async def call_with_retry[T](
    op: Callable[[], Awaitable[T]],
    *,
    is_transient: Callable[[BaseException], bool],
    attempts: int = 4,
    base_delay_s: float = 1.0,
    max_delay_s: float = 20.0,
    breaker: CircuitBreaker | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: random.Random | None = None,
) -> T:
    """Run op(); on a transient error wait (full jitter, exponential cap) and try again."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    rng = rng or random.Random()  # noqa: S311 - jitter, not cryptography
    for attempt in range(1, attempts + 1):
        if breaker is not None:
            breaker.before_call()
        try:
            result = await op()
        except Exception as exc:  # not BaseException: task cancellation must propagate untouched
            transient = is_transient(exc)
            if breaker is not None and transient:
                breaker.record_failure()
            if not transient or attempt == attempts:
                raise
            cap = min(max_delay_s, base_delay_s * (2 ** (attempt - 1)))
            await sleep(rng.uniform(0, cap))
            continue
        if breaker is not None:
            breaker.record_success()
        return result
    raise AssertionError("unreachable")  # pragma: no cover
