"""Fixed-window rate limiting: `limit` requests per `window_s` seconds per user."""


class RateLimiter:
    def __init__(self, limit: int = 5, window_s: float = 60.0) -> None:
        self.limit = limit
        self.window_s = window_s
        self._buckets: dict[str, tuple[float, int]] = {}

    def allow(self, user: str, now: float) -> bool:
        start, count = self._buckets.get(user, (now, 0))
        if now - start >= self.window_s:
            # BUG: the window rolls over, but the old count is kept.
            start = now
        if count >= self.limit:
            return False
        self._buckets[user] = (start, count + 1)
        return True
