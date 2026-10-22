import pytest

from app.pricing import apply_discount


def test_whole_numbers():
    assert apply_discount(1000, 10) == 900
    assert apply_discount(1000, 0) == 1000
    assert apply_discount(1000, 100) == 0


def test_bad_percent():
    with pytest.raises(ValueError):
        apply_discount(100, 101)
