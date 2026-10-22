from datetime import date

from app.reports import Order, revenue


def test_revenue():
    assert revenue([Order(1, date(2026, 3, 2), 500), Order(2, date(2026, 3, 3), 250)]) == 750
