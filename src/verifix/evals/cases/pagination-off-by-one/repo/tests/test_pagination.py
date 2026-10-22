from app.pagination import total_pages


def test_total_pages():
    assert total_pages(0, 10) == 1
    assert total_pages(25, 10) == 3
