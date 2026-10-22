"""Price calculations. Amounts are integer cents."""


def apply_discount(price_cents: int, percent: int) -> int:
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    return int(price_cents * (100 - percent) / 100)
