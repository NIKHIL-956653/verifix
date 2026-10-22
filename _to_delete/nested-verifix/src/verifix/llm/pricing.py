"""
Per-model prices, USD per million tokens, used for budget enforcement and cost reports.

Checked against Anthropic's published list prices on 22 Oct 2026. Cache reads bill at
10% of the input price and cache writes at 125%. An unknown model raises rather than
silently costing $0: a budget that cannot be computed must not be treated as unspent.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input_per_mtok: float
    output_per_mtok: float

    @property
    def cache_read_per_mtok(self) -> float:
        return self.input_per_mtok * 0.10

    @property
    def cache_write_per_mtok(self) -> float:
        return self.input_per_mtok * 1.25


PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(4.00, 20.00),
    "claude-sonnet-5-5": Price(2.00, 10.00),
    "claude-haiku-5-5": Price(0.10, 0.50),
}


class UnknownModelPrice(KeyError):
    pass


def price_for(model: str) -> Price:
    # Accept dated snapshots such as "claude-sonnet-5-5-20260901".
    for name, price in PRICES.items():
        if model == name or model.startswith(name + "-"):
            return price
    raise UnknownModelPrice(model)


def cost_usd(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    p = price_for(model)
    return (
        input_tokens * p.input_per_mtok
        + output_tokens * p.output_per_mtok
        + cache_read_tokens * p.cache_read_per_mtok
        + cache_write_tokens * p.cache_write_per_mtok
    ) / 1_000_000
