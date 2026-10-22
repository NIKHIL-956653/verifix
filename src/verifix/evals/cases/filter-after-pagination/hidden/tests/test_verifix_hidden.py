from app.search import search

# ids 0..24; even ids open, odd ids closed -> 13 open items
ITEMS = [{"id": n, "status": "open" if n % 2 == 0 else "closed"} for n in range(25)]


def test_filtered_first_page_is_full():
    got = [i["id"] for i in search(ITEMS, status="open", offset=0, limit=10)]
    assert got == [0, 2, 4, 6, 8, 10, 12, 14, 16, 18]


def test_filtered_second_page_has_the_rest():
    got = [i["id"] for i in search(ITEMS, status="open", offset=10, limit=10)]
    assert got == [20, 22, 24]


def test_no_matching_row_is_skipped_across_pages():
    seen = []
    for offset in (0, 10, 20):
        seen += [i["id"] for i in search(ITEMS, status="open", offset=offset, limit=10)]
    assert seen == [n for n in range(25) if n % 2 == 0]


def test_unfiltered_unchanged():
    assert [i["id"] for i in search(ITEMS, offset=0, limit=3)] == [0, 1, 2]
