import pytest

from app.pagination import paginate

ITEMS = list(range(25))


def test_first_page():
    assert paginate(ITEMS, 1, 10) == list(range(10))


def test_last_partial_page():
    assert paginate(ITEMS, 3, 10) == list(range(20, 25))


def test_past_the_end_is_empty():
    assert paginate(ITEMS, 4, 10) == []


def test_page_zero_rejected():
    with pytest.raises(ValueError):
        paginate(ITEMS, 0, 10)
