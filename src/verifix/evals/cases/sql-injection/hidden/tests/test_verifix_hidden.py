from app.users import add_user, connect, find_users


def _db():
    c = connect()
    for n in ("ann", "bob", "O'Brien"):
        add_user(c, n)
    return c


def test_injection_returns_nothing():
    assert find_users(_db(), "x' OR '1'='1") == []


def test_apostrophe_names_work():
    assert find_users(_db(), "O'Brien") == ["O'Brien"]


def test_normal_lookup_unchanged():
    assert find_users(_db(), "ann") == ["ann"]
