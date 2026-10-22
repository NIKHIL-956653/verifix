from app.users import add_user, connect, find_users


def test_find_existing():
    c = connect()
    add_user(c, "ann")
    add_user(c, "bob")
    assert find_users(c, "bob") == ["bob"]
