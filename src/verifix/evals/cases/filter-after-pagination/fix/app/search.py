"""In-memory order search."""


def search(
    items: list[dict],
    *,
    status: str | None = None,
    offset: int = 0,
    limit: int = 10,
) -> list[dict]:
    matching = items if status is None else [i for i in items if i["status"] == status]
    return matching[offset : offset + limit]
