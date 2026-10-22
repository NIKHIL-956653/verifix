from datetime import date

import pytest

from app.reports import Order, orders_between

ORDERS = [
    Order(1, date(2026, 2, 28), 1),
    Order(2, date(2026, 3, 1), 1),
    Order(3, date(2026, 3, 15), 1),
    Order(4, date(2026, 3, 31), 1),
    Order(5, date(2026, 4, 1), 1),
]


def test_both_ends_inclusive():
    got = orders_between(ORDERS, date(2026, 3, 1), date(2026, 3, 31))
    assert [o.id for o in got] == [2, 3, 4]


def test_single_day():
    assert [o.id for o in orders_between(ORDERS, date(2026, 3, 31), date(2026, 3, 31))] == [4]


def test_reversed_range_rejected():
    with pytest.raises(ValueError):
        orders_between(ORDERS, date(2026, 3, 31), date(2026, 3, 1))
