from app.ratelimit import RateLimiter


def test_new_window_gives_a_fresh_allowance():
    rl = RateLimiter(limit=2, window_s=60)
    assert rl.allow("u", 0.0) and rl.allow("u", 1.0)
    assert not rl.allow("u", 2.0)
    assert rl.allow("u", 61.0)
    assert rl.allow("u", 62.0)
    assert not rl.allow("u", 63.0)


def test_still_blocks_within_a_window():
    rl = RateLimiter(limit=1, window_s=60)
    assert rl.allow("u", 100.0)
    assert not rl.allow("u", 159.9)


def test_users_are_independent():
    rl = RateLimiter(limit=1, window_s=60)
    assert rl.allow("a", 0.0)
    assert rl.allow("b", 0.0)
    assert not rl.allow("a", 1.0)
