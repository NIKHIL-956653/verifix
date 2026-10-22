"""Order totals."""

from app.pricing import (
    FREE_SHIPPING_THRESHOLD_DOLLARS,
    SHIPPING_FLAT_CENTS,
    line_total_cents,
)


def order_total_cents(items: list[tuple[int, int]]) -> int:
    """items: (unit_cents, qty) pairs."""
    subtotal = sum(line_total_cents(u, q) for u, q in items)
    shipping = 0 if subtotal >= FREE_SHIPPING_THRESHOLD_DOLLARS * 100 else SHIPPING_FLAT_CENTS
    return subtotal + shipping
