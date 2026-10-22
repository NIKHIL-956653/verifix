from app.checkout import order_total_cents


def test_ten_dollar_order_pays_shipping():
    assert order_total_cents([(1000, 1)]) == 1000 + 599


def test_exactly_fifty_dollars_is_free():
    assert order_total_cents([(2500, 2)]) == 5000


def test_just_under_fifty_pays():
    assert order_total_cents([(4999, 1)]) == 4999 + 599
