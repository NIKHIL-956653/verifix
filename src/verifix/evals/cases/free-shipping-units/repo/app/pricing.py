"""Money amounts are integer cents unless a name says otherwise."""

FREE_SHIPPING_THRESHOLD_DOLLARS = 50
SHIPPING_FLAT_CENTS = 599


def line_total_cents(unit_cents: int, qty: int) -> int:
    return unit_cents * qty
