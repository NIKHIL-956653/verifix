"""Order reporting."""

from dataclasses import dataclass
from datetime import date


@dataclass
class Order:
    id: int
    placed_on: date
    total_cents: int


def orders_between(orders: list[Order], start: date, end: date) -> list[Order]:
    if end < start:
        raise ValueError("end before start")
    return [o for o in orders if start <= o.placed_on <= end]


def revenue(orders: list[Order]) -> int:
    return sum(o.total_cents for o in orders)
