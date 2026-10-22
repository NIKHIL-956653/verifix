from app.ratelimit import RateLimiter


def test_blocks_at_the_limit():
    rl = RateLimiter(limit=2, window_s=60)
    assert rl.allow("u", 0.0)
    assert rl.allow("u", 1.0)
    assert not rl.allow("u", 2.0)
