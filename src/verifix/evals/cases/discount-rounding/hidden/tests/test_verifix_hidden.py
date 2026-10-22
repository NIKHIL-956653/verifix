from app.pricing import apply_discount


def test_rounds_to_nearest_cent():
    assert apply_discount(995, 15) == 846


def test_half_cent_rounds_up():
    assert apply_discount(50, 95) == 3
    assert apply_discount(10, 25) == 8


def test_rounds_down_below_half():
    assert apply_discount(999, 10) == 899


def test_whole_numbers_unchanged():
    assert apply_discount(1000, 10) == 900
