from app.checkout import order_total_cents


def test_large_order_ships_free():
    assert order_total_cents([(3000, 2)]) == 6000


def test_tiny_order_pays_shipping():
    assert order_total_cents([(10, 1)]) == 10 + 599
