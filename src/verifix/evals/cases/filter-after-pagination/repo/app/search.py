"""In-memory order search."""


def search(
    items: list[dict],
    *,
    status: str | None = None,
    offset: int = 0,
    limit: int = 10,
) -> list[dict]:
    # BUG: pagination is applied before the status filter.
    page = items[offset : offset + limit]
    if status is not None:
        page = [i for i in page if i["status"] == status]
    return page
