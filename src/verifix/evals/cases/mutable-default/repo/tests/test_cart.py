from app.cart import add_item, cart_total


def test_explicit_cart():
    c = ["tea"]
    assert add_item("milk", c) == ["tea", "milk"]


def test_total():
    assert cart_total(["tea", "tea"], {"tea": 3}) == 6
