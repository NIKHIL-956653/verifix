"""Simple list pagination (pages are 1-based)."""

import math


def total_pages(count: int, per_page: int) -> int:
    return max(1, math.ceil(count / per_page))


def paginate(items: list, page: int, per_page: int = 10) -> list:
    if page < 1:
        raise ValueError("page must be >= 1")
    start = (page - 1) * per_page
    return items[start : start + per_page]
