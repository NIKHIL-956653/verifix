"""Shopping cart helpers."""


def add_item(item: str, cart: list | None = None) -> list:
    if cart is None:
        cart = []
    cart.append(item)
    return cart


def cart_total(cart: list, prices: dict[str, int]) -> int:
    return sum(prices[i] for i in cart)
