from app.cart import add_item


def test_new_carts_are_independent():
    a = add_item("tea")
    b = add_item("milk")
    assert a == ["tea"]
    assert b == ["milk"]


def test_explicit_cart_still_mutated():
    c = []
    add_item("x", c)
    assert c == ["x"]
