from app.search import search

ITEMS = [{"id": n, "status": "open"} for n in range(15)]


def test_unfiltered_pagination():
    assert [i["id"] for i in search(ITEMS, offset=0, limit=10)] == list(range(10))
    assert [i["id"] for i in search(ITEMS, offset=10, limit=10)] == list(range(10, 15))
